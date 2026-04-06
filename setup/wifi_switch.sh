#!/usr/bin/env bash
# wifi_switch.sh — CARSDR Wi-Fi mode switcher
# Runs as root via: sudo /home/pi/carsdr/setup/wifi_switch.sh <action> [args]
#
# Actions:
#   status              — print JSON of current mode/IP/SSID
#   ap                  — switch to hotspot mode
#   client <ssid> <pw>  — connect to existing Wi-Fi network
#   scan                — list nearby SSIDs (managed mode only)
#
# All output is JSON on stdout. Exit 0 on success, 1 on error.

set -euo pipefail

STATE_FILE="/etc/carsdr/wifi_mode"
DHCPCD_CONF="/etc/dhcpcd.conf"
WPA_CONF="/etc/wpa_supplicant/wpa_supplicant.conf"
ACTION="${1:-status}"

# ── Backend detection ────────────────────────────────────────────────
if command -v nmcli &>/dev/null && systemctl is-active --quiet NetworkManager 2>/dev/null; then
    BACKEND="nm"
else
    BACKEND="classic"
fi

# ── Helpers ──────────────────────────────────────────────────────────

current_ip() {
    ip addr show wlan0 2>/dev/null \
        | grep 'inet ' | awk '{print $2}' | cut -d/ -f1 | head -1 || echo ""
}

current_ssid() {
    iwgetid -r wlan0 2>/dev/null || echo ""
}

write_state() {
    mkdir -p /etc/carsdr
    echo "$1" > "$STATE_FILE"
}

read_state() {
    if [ -f "$STATE_FILE" ]; then cat "$STATE_FILE"; else echo "ap"; fi
}

# Wait up to $1 seconds for wlan0 to get an IP; print it or empty string
wait_for_ip() {
    local max="${1:-15}"
    local elapsed=0
    while [ "$elapsed" -lt "$max" ]; do
        local ip
        ip=$(current_ip)
        if [ -n "$ip" ]; then echo "$ip"; return 0; fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    echo ""
}

# Add or remove the CARSDR static-IP block in dhcpcd.conf
enable_static_ip() {
    if ! grep -q "CARSDR-BEGIN" "$DHCPCD_CONF" 2>/dev/null; then
        cat >> "$DHCPCD_CONF" << 'EOF'

# CARSDR-BEGIN
interface wlan0
    static ip_address=10.0.0.1/24
    nohook wpa_supplicant
# CARSDR-END
EOF
    fi
}

disable_static_ip() {
    # Remove lines between CARSDR-BEGIN and CARSDR-END (inclusive)
    sed -i '/# CARSDR-BEGIN/,/# CARSDR-END/d' "$DHCPCD_CONF" 2>/dev/null || true
}

# ── Restart AP mode (used for error recovery too) ────────────────────
start_ap() {
    if [ "$BACKEND" = "nm" ]; then
        nmcli dev set wlan0 managed no 2>/dev/null || true
    fi
    enable_static_ip
    ip addr flush dev wlan0 2>/dev/null || true
    ip addr add 10.0.0.1/24 dev wlan0 2>/dev/null || true
    ip link set wlan0 up
    systemctl restart hostapd
    systemctl restart dnsmasq
    write_state "ap"
}

# ── Actions ──────────────────────────────────────────────────────────

do_status() {
    local mode ip ssid
    mode=$(read_state)
    ip=$(current_ip)
    ssid=$(current_ssid)
    printf '{"mode":"%s","ip":"%s","ssid":"%s"}\n' "$mode" "$ip" "$ssid"
}

do_ap() {
    # Stop any client association
    if [ "$BACKEND" = "nm" ]; then
        nmcli con down carsdr-client 2>/dev/null || true
        sleep 1
    else
        systemctl stop wpa_supplicant 2>/dev/null || true
        dhclient -r wlan0 2>/dev/null || true
    fi

    start_ap
    printf '{"ok":true,"mode":"ap","ip":"10.0.0.1"}\n'
}

do_client() {
    local ssid="${2:-}"
    local password="${3:-}"

    if [ -z "$ssid" ]; then
        printf '{"ok":false,"error":"ssid_required"}\n'; exit 1
    fi

    # Stop hotspot
    systemctl stop hostapd  2>/dev/null || true
    systemctl stop dnsmasq  2>/dev/null || true

    if [ "$BACKEND" = "nm" ]; then
        # Let NetworkManager manage wlan0
        nmcli dev set wlan0 managed yes 2>/dev/null || true
        disable_static_ip
        systemctl restart NetworkManager
        sleep 2

        # Remove stale client profile if present
        nmcli con delete carsdr-client 2>/dev/null || true

        # Connect
        if [ -n "$password" ]; then
            nmcli dev wifi connect "$ssid" password "$password" ifname wlan0 name carsdr-client 2>&1 || {
                start_ap
                printf '{"ok":false,"error":"connection_failed","mode":"ap"}\n'; exit 1
            }
        else
            nmcli dev wifi connect "$ssid" ifname wlan0 name carsdr-client 2>&1 || {
                start_ap
                printf '{"ok":false,"error":"connection_failed","mode":"ap"}\n'; exit 1
            }
        fi
    else
        # Classic: wpa_supplicant + dhclient/dhcpcd
        disable_static_ip
        ip addr flush dev wlan0 2>/dev/null || true
        ip link set wlan0 up

        # Write wpa_supplicant config
        if [ -n "$password" ]; then
            wpa_passphrase "$ssid" "$password" > "$WPA_CONF"
        else
            cat > "$WPA_CONF" << EOF
network={
    ssid="$ssid"
    key_mgmt=NONE
}
EOF
        fi

        systemctl stop wpa_supplicant 2>/dev/null || true
        wpa_supplicant -B -i wlan0 -c "$WPA_CONF" -D nl80211,wext 2>/dev/null || {
            start_ap
            printf '{"ok":false,"error":"wpa_supplicant_failed","mode":"ap"}\n'; exit 1
        }

        # Get IP via dhclient or dhcpcd
        if command -v dhclient &>/dev/null; then
            dhclient wlan0 2>/dev/null &
        else
            dhcpcd -n wlan0 2>/dev/null &
        fi
    fi

    write_state "client"
    local ip
    ip=$(wait_for_ip 20)

    if [ -z "$ip" ]; then
        # Failed to get IP — fall back to AP
        start_ap
        printf '{"ok":false,"error":"no_dhcp_lease","mode":"ap"}\n'; exit 1
    fi

    printf '{"ok":true,"mode":"client","ip":"%s","ssid":"%s"}\n' "$ip" "$ssid"
}

do_scan() {
    local mode
    mode=$(read_state)

    # In AP mode scanning disrupts clients; do it anyway with a warning
    local results="[]"
    if command -v nmcli &>/dev/null; then
        results=$(nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list --rescan yes 2>/dev/null \
            | grep -v '^[[:space:]]*:' \
            | awk -F: 'NF>=3 && $1!="" {gsub(/"/,"\\\""); printf "{\"ssid\":\"%s\",\"signal\":%s,\"security\":\"%s\"}\n",$1,$2,$3}' \
            | python3 -c "
import sys, json
seen = set()
nets = []
for line in sys.stdin:
    try:
        e = json.loads(line.strip())
        if e['ssid'] not in seen:
            seen.add(e['ssid'])
            nets.append(e)
    except Exception:
        pass
nets.sort(key=lambda x: -int(x.get('signal',0) or 0))
print(json.dumps(nets))
" 2>/dev/null || echo "[]")
    elif command -v iwlist &>/dev/null; then
        results=$(iwlist wlan0 scan 2>/dev/null \
            | python3 -c "
import sys, re, json
nets = []
seen = set()
cur = {}
for line in sys.stdin:
    line = line.strip()
    m = re.search(r'ESSID:\"(.+?)\"', line)
    if m:
        ssid = m.group(1)
        if ssid not in seen:
            seen.add(ssid)
            nets.append({'ssid': ssid, 'signal': 0, 'security': 'unknown'})
    sig = re.search(r'Signal level=(-?\d+)', line)
    if sig and nets:
        nets[-1]['signal'] = int(sig.group(1))
print(json.dumps(nets))
" 2>/dev/null || echo "[]")
    fi

    printf '{"ssids":%s,"scanned_in_ap_mode":%s}\n' \
        "$results" "$([ "$mode" = "ap" ] && echo "true" || echo "false")"
}

# ── Dispatch ─────────────────────────────────────────────────────────
case "$ACTION" in
    status) do_status ;;
    ap)     do_ap ;;
    client) do_client "$@" ;;
    scan)   do_scan ;;
    *)      printf '{"ok":false,"error":"unknown_action:%s"}\n' "$ACTION"; exit 1 ;;
esac
