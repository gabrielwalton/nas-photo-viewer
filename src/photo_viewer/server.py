from __future__ import annotations

import os

from waitress import serve

from .app import create_app


def main() -> None:
    host = os.getenv("PHOTO_VIEWER_HOST", "0.0.0.0")
    port = int(os.getenv("PHOTO_VIEWER_PORT", "8080"))
    serve(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
