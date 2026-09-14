from __future__ import annotations

import array
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path


class AudioAnalyzer:
    """Continuously reduce an ALSA microphone stream to visualiser energy bands."""

    SAMPLE_RATE = 44_100
    SAMPLES = 1024
    FREQUENCIES = {
        "bass": (55, 80, 110, 150),
        "mid": (260, 440, 700, 1100, 1700),
        "treble": (2600, 4000, 6200, 9000),
    }

    def __init__(self, enabled: Callable[[], bool]):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.state = {
            "available": False,
            "device": "",
            "bass": 0.0,
            "mid": 0.0,
            "treble": 0.0,
            "volume": 0.0,
            "beat": 0.0,
            "error": "No microphone detected",
        }
        self.peaks = {name: 0.01 for name in (*self.FREQUENCIES, "volume")}
        self.bass_average = 0.0

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="audio-analyzer").start()

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    @staticmethod
    def _capture_device() -> str:
        override = os.getenv("PHOTO_VIEWER_AUDIO_INPUT_DEVICE", "").strip()
        if override:
            return override
        try:
            lines = Path("/proc/asound/pcm").read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines()
        except OSError:
            return ""
        candidates = []
        for line in lines:
            if "capture" not in line.lower():
                continue
            match = re.match(r"\s*(\d+)-(\d+):", line)
            if match:
                priority = 0 if "usb" in line.lower() else 1
                candidates.append((priority, f"plughw:{match[1]},{match[2]}"))
        return min(candidates, default=(9, ""))[1]

    @staticmethod
    def _goertzel(samples: list[float], frequency: float) -> float:
        omega = 2 * math.pi * frequency / AudioAnalyzer.SAMPLE_RATE
        coefficient = 2 * math.cos(omega)
        previous = previous_two = 0.0
        for sample in samples:
            current = sample + coefficient * previous - previous_two
            previous_two, previous = previous, current
        power = max(
            0.0,
            previous_two * previous_two
            + previous * previous
            - coefficient * previous * previous_two,
        )
        return 2 * math.sqrt(power) / len(samples)

    def _normalise(self, name: str, value: float) -> float:
        peak = max(value, self.peaks[name] * 0.985, 0.01)
        self.peaks[name] = peak
        return min(1.0, value / peak)

    def _analyse(self, raw: bytes, device: str) -> None:
        values = array.array("h")
        values.frombytes(raw)
        if os.sys.byteorder != "little":
            values.byteswap()
        samples = [value / 32768 for value in values]
        windowed = [
            sample * (0.5 - 0.5 * math.cos(2 * math.pi * index / len(samples)))
            for index, sample in enumerate(samples)
        ]
        energies = {
            name: sum(self._goertzel(windowed, frequency) for frequency in frequencies)
            / len(frequencies)
            for name, frequencies in self.FREQUENCIES.items()
        }
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        normalised = {
            name: self._normalise(name, value) for name, value in energies.items()
        }
        volume = self._normalise("volume", rms)
        bass = normalised["bass"]
        beat = 1.0 if bass > max(0.42, self.bass_average * 1.32) else 0.0
        self.bass_average = self.bass_average * 0.92 + bass * 0.08
        with self.lock:
            self.state = {
                "available": True,
                "device": device,
                **normalised,
                "volume": volume,
                "beat": beat,
                "error": "",
            }

    def _run(self) -> None:
        while True:
            if not self.enabled():
                time.sleep(0.5)
                continue
            device = self._capture_device()
            if not device or not shutil.which("arecord"):
                with self.lock:
                    self.state.update(
                        available=False,
                        device="",
                        error="Connect a USB microphone to enable live response",
                    )
                time.sleep(3)
                continue
            command = [
                "arecord",
                "-q",
                "-D",
                device,
                "-f",
                "S16_LE",
                "-r",
                str(self.SAMPLE_RATE),
                "-c",
                "1",
                "-t",
                "raw",
            ]
            process: subprocess.Popen | None = None
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                assert process.stdout is not None
                while self.enabled():
                    raw = process.stdout.read(self.SAMPLES * 2)
                    if len(raw) != self.SAMPLES * 2:
                        break
                    self._analyse(raw, device)
            except (OSError, subprocess.SubprocessError) as exc:
                with self.lock:
                    self.state.update(available=False, error=str(exc)[:200])
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
            time.sleep(2)
