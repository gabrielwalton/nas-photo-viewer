# NAS Photo Viewer

A television-friendly random photo slideshow for a Managed Pi. It serves a
fullscreen viewer and a simple setup panel at port 8080. Photos can come from a
local directory or an SMB share on a NAS.

## Current features

- Configure the NAS and slideshow from a browser; no SSH editing is required.
- Browse subfolders before selecting the slideshow root.
- Recursively find JPEG, PNG, GIF, WebP, BMP and AVIF images.
- Random playback without immediate repeats.
- Configurable display duration, contain/cover fit and fade transition.
- Keep NAS credentials only in the Pi's persistent data directory.
- Stay healthy while unconfigured so initial Managed Pi deployment succeeds.

## Managed Pi deployment

This repository implements the Managed Pi application contract in `deploy/`.
After deployment, open `http://<pi-address>:8080` from a device on the same LAN.

The HDMI kiosk launcher is supplied by the Managed Pi base image. Until that
graphics layer is installed, this service can still be configured and tested
fully from another browser.

