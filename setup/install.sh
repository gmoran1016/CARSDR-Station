#!/usr/bin/env bash
# CARSDR Station — Raspberry Pi setup script
# Run once as root (or with sudo) on a fresh Raspberry Pi OS installation.
#
# Quick start on a fresh Pi:
#   git clone https://github.com/gmoran1016/CARSDR-Station.git /home/pi/carsdr
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

# ── Detect Wi-Fi backend early ─────────────────────────────────────────
if nmcli -t -f RUNNING general 2>/dev/null | grep -q "running"; then
    WIFI_BACKEND="nm"
    echo "  Wi-Fi backend: NetworkManager (Bookworm/Trixie)"
else
    WIFI_BACKEND="classic"
    echo "  Wi-Fi backend: classic (hostapd + dhcpcd)"
fi

# ── System packages ────────────────────────────────────────────────────
echo "[1/9] Installing system packages..."
apt-get update -qq
apt-get install -y \
    rtl-sdr \
    ffmpeg \
    python3-pip \
    hostapd \
    dnsmasq \
    avahi-daemon \
    wpasupplicant \
    curl

# ── Python dependencies ────────────────────────────────────────────────
echo "[2/9] Installing Python packages..."
pip3 install --break-system-packages flask pyyaml 2>/dev/null \
  || pip3 install flask pyyaml

# ── RTL-SDR kernel module blacklist ────────────────────────────────────
echo "[3/9] Blacklisting RTL-SDR kernel modules..."
cat > /etc/modprobe.d/blacklist-rtl.conf << 'EOF'
# Prevent the OS from claiming the RTL-SDR dongle
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF
modprobe -r dvb_usb_rtl28xxu rtl2832 rtl2830 2>/dev/null || true

# ── HLS.js (bundled locally — no CDN needed offline) ──────────────────
echo "[4/9] Downloading HLS.js..."
HLS_JS_URL="https://cdn.jsdelivr.net/npm/hls.js@1.5.15/dist/hls.min.js"
curl -sL "$HLS_JS_URL" -o "$PROJECT_DIR/web/hls.min.js" \
  || echo "WARNING: Could not download hls.min.js — run with internet access, or copy manually"

# ── Static IP for wlan0 (classic backend only) ────────────────────────
if [ "$WIFI_BACKEND" = "classic" ]; then
    echo "[5/9] Configuring static IP for wlan0 (dhcpcd)..."
    if ! grep -q "CARSDR-BEGIN" /etc/dhcpcd.conf 2>/dev/null && \
       ! grep -q "nohook wpa_supplicant" /etc/dhcpcd.conf 2>/dev/null; then
        cat >> /etc/dhcpcd.conf << 'DHCPCD_EOF'

# CARSDR-BEGIN
interface wlan0
    static ip_address=10.0.0.1/24
    nohook wpa_supplicant
# CARSDR-END
DHCPCD_EOF
    fi
else
    echo "[5/9] Skipping dhcpcd config (NetworkManager system)..."
fi

# ── Hostname + mDNS (for carsdr.local discovery in client mode) ────────
echo "[6/9] Setting hostname and enabling mDNS..."
CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" != "carsdr" ]; then
    echo "carsdr" > /etc/hostname
    sed -i "s/127\.0\.1\.1.*/127.0.1.1\tcarsdr/" /etc/hosts
    hostname carsdr
    echo "  Hostname set to: carsdr (resolves as carsdr.local)"
else
    echo "  Hostname already set to carsdr"
fi
systemctl enable avahi-daemon
systemctl start avahi-daemon 2>/dev/null || true

# ── Wi-Fi switch script + sudoers ─────────────────────────────────────
echo "[7/9] Configuring Wi-Fi switch permissions..."
chmod +x "$PROJECT_DIR/setup/wifi_switch.sh"
echo "$SERVICE_USER ALL=(root) NOPASSWD: $PROJECT_DIR/setup/wifi_switch.sh" \
    > /etc/sudoers.d/carsdr-wifi
chmod 440 /etc/sudoers.d/carsdr-wifi

# State directory for wifi_manager
mkdir -p /etc/carsdr
echo "ap" > /etc/carsdr/wifi_mode
chown "$SERVICE_USER:$SERVICE_USER" /etc/carsdr/wifi_mode

# ── AP setup ───────────────────────────────────────────────────────────
echo "[8/9] Configuring Wi-Fi access point..."

SSID=$(python3 -c "import yaml; c=yaml.safe_load(open('$PROJECT_DIR/config.yaml')); print(c['wifi']['ssid'])")
PASS=$(python3 -c "import yaml; c=yaml.safe_load(open('$PROJECT_DIR/config.yaml')); print(c['wifi']['password'])")

# Always write hostapd.conf (used by classic backend)
cp "$PROJECT_DIR/setup/hostapd.conf" /etc/hostapd/hostapd.conf
sed -i "s/__SSID__/$SSID/" /etc/hostapd/hostapd.conf
sed -i "s/__PASSWORD__/$PASS/" /etc/hostapd/hostapd.conf
sed -i 's|#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd

[ -f /etc/dnsmasq.conf ] && mv /etc/dnsmasq.conf /etc/dnsmasq.conf.bak
cp "$PROJECT_DIR/setup/dnsmasq.conf" /etc/dnsmasq.conf

mkdir -p "$RECORDINGS_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$RECORDINGS_DIR"

sed "s|__PROJECT_DIR__|$PROJECT_DIR|g; s|__SERVICE_USER__|$SERVICE_USER|g" \
    "$PROJECT_DIR/setup/carsdr.service" \
    > /etc/systemd/system/carsdr.service
systemctl daemon-reload

if [ "$WIFI_BACKEND" = "nm" ]; then
    # Use NetworkManager's built-in AP mode — no standalone hostapd/dnsmasq needed
    echo "  Creating NetworkManager hotspot profile..."
    nmcli con delete carsdr-hotspot 2>/dev/null || true
    nmcli con add type wifi ifname wlan0 con-name carsdr-hotspot \
        ssid "$SSID" \
        wifi.mode ap \
        wifi.band bg \
        wifi.channel 6 \
        ipv4.method shared \
        ipv4.addresses 10.0.0.1/24 \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "$PASS" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100
    echo "  NM hotspot profile 'carsdr-hotspot' created"

    # Disable standalone hostapd/dnsmasq (NM manages AP internally)
    systemctl unmask hostapd 2>/dev/null || true
    systemctl disable hostapd dnsmasq 2>/dev/null || true
    systemctl stop hostapd dnsmasq 2>/dev/null || true

    systemctl enable avahi-daemon carsdr

    # Activate the hotspot now
    nmcli con up carsdr-hotspot 2>/dev/null \
        && echo "  Hotspot '$SSID' is now active at 10.0.0.1" \
        || echo "  WARNING: Could not bring hotspot up now — will activate on next reboot"
else
    systemctl unmask hostapd
    systemctl enable hostapd dnsmasq avahi-daemon carsdr
fi

# ── Done ───────────────────────────────────────────────────────────────
echo "[9/9] Setup complete!"
echo ""
echo "  Reboot to activate all changes:"
echo "    sudo reboot"
echo ""
echo "  After reboot:"
echo "  1. Connect iPhone to Wi-Fi: '$SSID'"
echo "  2. Open Safari:  http://10.0.0.1:5000"
echo ""
echo "  Wi-Fi mode switching:"
echo "    Hotspot → Client: tap the HOTSPOT badge in the web UI"
echo "    When in client mode, find the Pi at: http://carsdr.local:5000"
echo ""
echo "  To check service status:"
echo "    sudo systemctl status carsdr"
echo "    journalctl -u carsdr -f"
