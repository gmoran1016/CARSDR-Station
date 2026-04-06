#!/usr/bin/env bash
# CARSDR Station — Raspberry Pi setup script
# Run once as root (or with sudo) on a fresh Raspberry Pi OS installation.
#
# Quick start on a fresh Pi:
#   git clone https://github.com/Griffinmoran1016/CARSDR-Station.git /home/pi/carsdr
#   cd /home/pi/carsdr
#   cp config.yaml.example config.yaml
#   nano config.yaml          # Set your Wi-Fi password and adjust frequencies
#   sudo bash setup/install.sh
#
# To update to a newer version:
#   cd /home/pi/carsdr && git pull
#   sudo bash setup/install.sh

set -euo pipefail

PROJECT_DIR="/home/pi/carsdr"
RECORDINGS_DIR="/home/pi/carsdr_recordings"
SERVICE_USER="pi"

VERSION=$(cat "$PROJECT_DIR/VERSION" 2>/dev/null || echo "unknown")
echo "========================================"
echo "  CARSDR Station v$VERSION — Pi Setup"
echo "========================================"

# ── System packages ────────────────────────────────────────────────────
echo "[1/8] Installing system packages..."
apt-get update -qq
apt-get install -y \
    rtl-sdr \
    ffmpeg \
    python3-pip \
    hostapd \
    dnsmasq \
    curl

# ── Python dependencies ────────────────────────────────────────────────
echo "[2/8] Installing Python packages..."
pip3 install --break-system-packages flask pyyaml 2>/dev/null \
  || pip3 install flask pyyaml

# ── RTL-SDR kernel module blacklist ────────────────────────────────────
echo "[3/8] Blacklisting RTL-SDR kernel modules..."
cat > /etc/modprobe.d/blacklist-rtl.conf << 'EOF'
# Prevent the OS from claiming the RTL-SDR dongle
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
modprobe -r dvb_usb_rtl28xxu rtl2832 rtl2830 2>/dev/null || true

# ── HLS.js (bundled locally — no CDN needed offline) ──────────────────
echo "[4/8] Downloading HLS.js..."
HLS_JS_URL="https://cdn.jsdelivr.net/npm/hls.js@1.5.15/dist/hls.min.js"
curl -sL "$HLS_JS_URL" -o "$PROJECT_DIR/web/hls.min.js" \
  || echo "WARNING: Could not download hls.min.js — run with internet access, or copy manually"

# ── Static IP for wlan0 ────────────────────────────────────────────────
echo "[5/8] Configuring static IP for wlan0..."
# Tell dhcpcd to ignore wlan0 (hostapd manages it)
if ! grep -q "interface wlan0" /etc/dhcpcd.conf; then
    cat >> /etc/dhcpcd.conf << 'EOF'

# CARSDR: static IP for Wi-Fi hotspot interface
interface wlan0
    static ip_address=10.0.0.1/24
    nohook wpa_supplicant
EOF
fi

# ── hostapd config ─────────────────────────────────────────────────────
echo "[6/8] Installing hostapd config..."

# Read SSID/password from project config.yaml
SSID=$(python3 -c "import yaml; c=yaml.safe_load(open('$PROJECT_DIR/config.yaml')); print(c['wifi']['ssid'])")
PASS=$(python3 -c "import yaml; c=yaml.safe_load(open('$PROJECT_DIR/config.yaml')); print(c['wifi']['password'])")

cp "$PROJECT_DIR/setup/hostapd.conf" /etc/hostapd/hostapd.conf
sed -i "s/__SSID__/$SSID/" /etc/hostapd/hostapd.conf
sed -i "s/__PASSWORD__/$PASS/" /etc/hostapd/hostapd.conf

# Point hostapd to its config
sed -i 's|#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

# ── dnsmasq config ─────────────────────────────────────────────────────
# Back up existing config and install ours
[ -f /etc/dnsmasq.conf ] && mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak
cp "$PROJECT_DIR/setup/dnsmasq.conf" /etc/dnsmasq.conf

# ── Recordings directory ───────────────────────────────────────────────
mkdir -p "$RECORDINGS_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$RECORDINGS_DIR"

# ── systemd service ────────────────────────────────────────────────────
echo "[7/8] Installing systemd service..."
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__SERVICE_USER__|$SERVICE_USER|g" \
    "$PROJECT_DIR/setup/carsdr.service" \
    > /etc/systemd/system/carsdr.service
systemctl daemon-reload
systemctl enable hostapd dnsmasq carsdr
systemctl unmask hostapd

# ── Done ───────────────────────────────────────────────────────────────
echo "[8/8] Setup complete!"
echo ""
echo "  Reboot to activate all changes:"
echo "    sudo reboot"
echo ""
echo "  After reboot:"
echo "  1. Connect iPhone to Wi-Fi: '$(echo $SSID)'"
echo "  2. Open Safari:  http://10.0.0.1:5000"
echo ""
echo "  To check service status:"
echo "    sudo systemctl status carsdr"
echo "    journalctl -u carsdr -f"
