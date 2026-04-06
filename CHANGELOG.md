# Changelog

All notable changes to CARSDR Station are documented here.
Format: [version] — date — description

---

## [0.1.0] — 2026-04-06

Initial release.

- RTL-SDR V4 scanner with frequency hopping state machine (SCANNING / LOCKED / MANUAL)
- Squelch-based signal detection using RMS amplitude measurement
- HLS audio streaming via ffmpeg → Safari on iPhone (no app install required)
- Per-transmission WAV recording with frequency + timestamp filenames
- Flask web server with REST API for scanner control
- Dark-theme phone UI: frequency display, signal bars, scanner controls, recordings list
- Wi-Fi hotspot setup (hostapd + dnsmasq) for standalone car deployment
- systemd service for auto-start on Pi boot
- Default AAR railroad channel list (Ch 1, 5, 7, 7A, 20, 22, 36/FRED)
