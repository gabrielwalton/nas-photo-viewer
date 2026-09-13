# NAS Photo Viewer

A television-friendly random photo and video slideshow for a Managed Pi. It
serves a fullscreen viewer and a simple setup panel at port 8080. Media can come
from a local directory or an SMB share on a NAS.

## Current features

- Configure the NAS and slideshow from a browser; no SSH editing is required.
- Browse subfolders before selecting the slideshow root.
- Recursively find JPEG, PNG, GIF, WebP, BMP and AVIF images plus MP4, M4V,
  MOV, WebM and Ogg videos.
- Random playback without immediate repeats.
- Filter dated folders by year, month, or a month within a selected year.
- Show a discreet capture date and GPS location caption when metadata is available.
- Navigate backward and forward through the random playback history.
- Remember per-photo clockwise rotation corrections across restarts.
- Collage mode shows five or six gently rotated photos and replaces one at a
  time, without mixing videos into the collage.
- Play videos to the end and use the configured timer for still images.
- Configurable display duration, contain/cover fit and fade transition.
- Publish pause, next, favourite, refresh, timer, folder, fit, display mode and
  status controls to Home Assistant through MQTT Discovery.
- Switch the HDMI output between the slideshow and a Home Assistant dashboard,
  with an optional timed return to photos.
- Sleep to a black display manually or after a configurable period; Wake returns
  to photos and starts a fresh sleep timer.
- Hide the mouse pointer over the television viewer.
- Keep a persistent favourites list on the Pi.
- Protect deletion with a two-command confirmation window, then move the item
  into `_PhotoViewerDeleted` on the NAS instead of erasing it permanently.
- Keep NAS credentials only in the Pi's persistent data directory.
- Stay healthy while unconfigured so initial Managed Pi deployment succeeds.

## Managed Pi deployment

This repository implements the Managed Pi application contract in `deploy/`.
After deployment, open `http://<pi-address>:8080` from a device on the same LAN.

The HDMI kiosk launcher is supplied by the Managed Pi base image. Until that
graphics layer is installed, this service can still be configured and tested
fully from another browser.

Dashboard switching requires Managed Pi Base v0.3.1 or later. The application
always restores photo mode when it starts; its background MQTT connection stays
online while Chromium displays Home Assistant, so Home Assistant can switch the
same HDMI output back to the slideshow.

A fresh Managed Pi can be fully preconfigured by adding `PHOTO_VIEWER_SOURCE_TYPE`,
`PHOTO_VIEWER_SMB_SERVER`, `PHOTO_VIEWER_SMB_SHARE`,
`PHOTO_VIEWER_SMB_USERNAME`, `PHOTO_VIEWER_SMB_PASSWORD`,
`PHOTO_VIEWER_BASE_FOLDER` and `PHOTO_VIEWER_DASHBOARD_URL` to its private
`managed-pi.env` provisioning file. Settings subsequently changed through Home
Assistant are saved on the Pi and take precedence over these initial values.
