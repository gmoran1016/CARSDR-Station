# CARSDR Station

A car-based SDR basestation for railroad frequency monitoring, built on a Raspberry Pi 4 with an RTL-SDR V4. The Pi creates a Wi-Fi hotspot that your iPhone connects to — open Safari, tap Play, and listen to railroad traffic with a live frequency scanner.

No app installation required. Everything runs in the browser.

---

## Hardware

| Component | Notes |
|-----------|-------|
| Raspberry Pi 4 (2GB+) | Any 2GB+ model works |
| RTL-SDR V4 | [rtl-sdr.com/V4](https://www.rtl-sdr.com/V4) |
| Antenna | Discone or whip tuned for 160–162 MHz |
| 12V → USB-C car adapter | Powers the Pi from the car's 12V socket |

---

## How It Works

```
RTL-SDR V4 → rtl_fm → Python scanner → ffmpeg → HLS stream
                                    ↓                  ↓
                               WAV recorder      Flask web server
                                                       ↓
                                              iPhone Safari (Wi-Fi)
```

1. The Pi runs a frequency scanner that cycles through enabled railroad channels
2. When a signal is detected (squelch opens), it locks onto that frequency
3. Audio is encoded to HLS and served over the local Wi-Fi hotspot
4. Safari plays the stream natively — no plugins or apps needed
5. Transmissions are recorded as WAV files with frequency and timestamp in the filename

---

## Quickstart

### First-time Pi setup

```bash
# Clone the repo
git clone https://github.com/gmoran1016/CARSDR-Station.git /home/pi/carsdr
cd /home/pi/carsdr

# Configure — set your Wi-Fi password and adjust frequencies
cp config.yaml.example config.yaml
nano config.yaml

# Install everything (takes a few minutes)
sudo bash setup/install.sh

# Reboot to activate the hotspot and auto-start service
sudo reboot
```

### Connect your phone

1. On your iPhone, go to **Settings → Wi-Fi**
2. Connect to **CARSDR** (password set in your `config.yaml`)
3. Open **Safari** and navigate to `http://10.0.0.1:5000`
4. Tap **Play Audio** — there's a ~4 second buffer delay before audio starts

---

## Configuration

Edit `/home/pi/carsdr/config.yaml` (created from `config.yaml.example`):

```yaml
wifi:
  ssid: "CARSDR"
  password: "your_password_here"

sdr:
  gain: 40          # 0 = auto-gain; 0–49.6 dB
  ppm_error: 0      # Calibrate with: rtl_test -p
  squelch: 70       # 0 = open squelch; raise if you hear static

scanner:
  dwell_time_ms: 150    # Time per frequency when scanning
  lock_timeout_s: 3.0   # Seconds of silence before moving on
  rms_threshold: 500    # Signal detection sensitivity

frequencies:
  - name: "Ch 7 — Road"
    freq_mhz: 160.425
    enabled: true
  # ... add your regional channels
```

> **Tip:** Run `rtl_test -p` for a few minutes to find your dongle's PPM error and set `ppm_error` accordingly. Even a few PPM off can noticeably degrade NFM audio quality.

---

## Web UI

| Section | Description |
|---------|-------------|
| **Frequency display** | Current frequency in large text, signal strength bars |
| **Audio** | HLS player with volume control. Tap Play once to start. |
| **Scanner controls** | Start / Stop / Resume Scan buttons |
| **Quick Tune** | Manually enter any frequency in MHz |
| **Frequencies** | Toggle channels on/off, add custom frequencies, tune directly |
| **Recordings** | Playback and download past transmissions |

---

## Default Railroad Frequencies

Pre-loaded with common AAR (Association of American Railroads) channels. Enable/disable them from the UI or `config.yaml`.

| Channel | Frequency | Use |
|---------|-----------|-----|
| Ch 1 | 160.215 MHz | Dispatcher (common) |
| Ch 5 | 160.335 MHz | Road |
| Ch 7 | 160.425 MHz | Road (most widely used) |
| Ch 7A | 160.470 MHz | Road |
| Ch 20 | 161.100 MHz | Road |
| Ch 22 | 161.160 MHz | Road |
| Ch 36 | 161.550 MHz | FRED/EOT end-of-train telemetry |

Add your railroad's specific channels via the UI or directly in `config.yaml`.

---

## Updating

```bash
cd /home/pi/carsdr
git pull
sudo bash setup/install.sh
sudo systemctl restart carsdr
```

---

## Service Management

```bash
# Check status
sudo systemctl status carsdr

# View live logs
journalctl -u carsdr -f

# Restart
sudo systemctl restart carsdr

# Disable auto-start
sudo systemctl disable carsdr
```

---

## Troubleshooting

**No audio / stream not loading**
- Check the service is running: `sudo systemctl status carsdr`
- Confirm RTL-SDR is detected: `rtl_test`
- Make sure no other process is using the dongle: `sudo lsof /dev/bus/usb`

**Static / poor audio**
- Increase `sdr.squelch` in `config.yaml` (try 100–200)
- Tune `sdr.ppm_error` using `rtl_test -p`
- Try adjusting `sdr.gain` (40 is a good starting point; lower if overloading)

**Wi-Fi hotspot not appearing**
- Check hostapd: `sudo systemctl status hostapd`
- Verify the Pi's wlan0 has the static IP: `ip addr show wlan0`

**Phone can't reach 10.0.0.1**
- Confirm dnsmasq is running: `sudo systemctl status dnsmasq`
- Check your phone received a DHCP address in the 10.0.0.x range

---

## Versioning

This project uses [semantic versioning](https://semver.org). See [CHANGELOG.md](CHANGELOG.md) for release history.
