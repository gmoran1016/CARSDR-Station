# Changelog

All notable changes to CARSDR Station are documented here.
Format: [version] — date — description

---

## [0.3.1] — 2026-04-06

- **Fix hotspot on Bookworm/Trixie (NetworkManager)** — `install.sh` now detects whether NetworkManager is active and creates an NM hotspot profile (`nmcli con add ... wifi.mode ap`) instead of relying on `dhcpcd.conf` (which NM ignores). Standalone `hostapd` and `dnsmasq` are disabled on NM systems; NM handles AP and DHCP internally via `ipv4.method shared`
- `wifi_switch.sh` — `start_ap()` on NM now calls `nmcli con up carsdr-hotspot` instead of manually assigning the IP and restarting hostapd, which no longer works when NM is in control
- `do_client()` on NM brings down the hotspot profile before connecting, rather than stopping the hostapd service

## [0.3.0] — 2026-04-06

- **Wi-Fi mode switching** — tap the HOTSPOT/CLIENT badge in the header (or the Switch Mode button in the Network card) to toggle between hotspot and client mode without touching the Pi
- `setup/wifi_switch.sh` — root shell script with dual-backend support (NetworkManager/nmcli on Bookworm; classic hostapd+wpa_supplicant on Bullseye). Handles error recovery: automatically restores hostapd if client connection fails
- `src/wifi_manager.py` — Python wrapper with background-thread switching (returns 202 immediately; phone sees reconnect screen before connection drops)
- AP→Client: full-screen reconnect overlay shown immediately after switch — tells user to join the new network and open `http://carsdr.local:5000`
- Client→AP: reconnect overlay tells user to rejoin CARSDR and open `http://10.0.0.1:5000`
- Network scan via `nmcli` / `iwlist` — dropdown populated with nearby SSIDs and signal strength when in client mode
- `setup/install.sh` — adds `avahi-daemon` (mDNS for `carsdr.local`), `wpasupplicant`, sets Pi hostname to `carsdr`, sudoers rule for wifi_switch.sh, state directory `/etc/carsdr/`
- `setup/carsdr.service` — starts after `avahi-daemon.service`
- dhcpcd.conf uses CARSDR-BEGIN/END sentinel markers so wifi_switch.sh can safely add/remove the static IP block

## [0.2.1] — 2026-04-06

- RadioReference import now detects when the Pi has no internet access and shows an inline offline help banner with two workaround options (import at home, or temporarily connect Pi to phone's Personal Hotspot)
- Preview button is disabled when offline to prevent confusing timeout errors
- Added `GET /api/system/connectivity` endpoint for frontend connectivity checks
- `OfflineError` raised distinctly from other fetch errors for cleaner error handling

## [0.2.0] — 2026-04-06

- **Frequency persistence** — frequencies are now saved to `data/frequencies.json` on every change (add, remove, toggle, import). Changes survive reboots. On first run, the file is seeded from `config.yaml`.
- **RadioReference import** — new "Import from RadioReference" button in the Frequencies section. Paste any county, state, or agency URL from radioreference.com; the Pi fetches the page, parses all frequency tables, and presents them with tag filtering and per-row checkboxes. No RadioReference account required. Uses stdlib only (no new pip dependencies).
- Auto-highlights railroad entries in the import preview table (green tag pill)
- `rr_client.py` — stdlib HTML parser for RadioReference `rrdbTable` tables, supports county (`ctid`), state (`stid`), and agency (`aid`) URL patterns, normalizes legacy `/apps/db/?ctid=` URLs

## [0.1.1] — 2026-04-06

- Added README.md with hardware list, quickstart, configuration reference, UI overview, default frequency table, troubleshooting guide, and service management commands

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
