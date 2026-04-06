"""
wifi_manager.py — Wi-Fi mode switching manager

Wraps setup/wifi_switch.sh (which runs as root via sudo) with a Python
API. Mode switches run in a background thread so Flask can return
immediately — the phone connection typically drops during AP→Client
switches anyway, so there's no point blocking.

State file /etc/carsdr/wifi_mode persists across reboots and is written
by the shell script after each successful switch.
"""

import json
import logging
import os
import subprocess
import threading

logger = logging.getLogger(__name__)

_SWITCH_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'setup', 'wifi_switch.sh'
)
_SWITCH_SCRIPT = os.path.normpath(_SWITCH_SCRIPT)

_STATE_FILE = '/etc/carsdr/wifi_mode'


class WifiManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._switching = False
        self._last_result: dict | None = None
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_mode(self) -> str:
        """Read current mode from state file ('ap' or 'client')."""
        try:
            with open(_STATE_FILE) as f:
                return f.read().strip()
        except FileNotFoundError:
            return 'ap'

    def get_status(self) -> dict:
        """Return current mode, switch-in-progress flag, and last result."""
        return {
            'mode':        self.get_mode(),
            'switching':   self._switching,
            'last_result': self._last_result,
            'last_error':  self._last_error,
        }

    def switch_to_ap(self) -> bool:
        """Start background switch to hotspot mode. Returns False if already switching."""
        return self._start_switch('ap')

    def switch_to_client(self, ssid: str, password: str) -> bool:
        """Start background switch to client mode. Returns False if already switching."""
        if not ssid or len(ssid) > 32:
            raise ValueError("SSID must be 1–32 characters")
        return self._start_switch('client', ssid, password)

    def scan_networks(self) -> list:
        """Synchronously scan for nearby Wi-Fi networks. Returns list of dicts."""
        result = self._run_script('scan', timeout=20)
        return result.get('ssids', [])

    @property
    def is_switching(self) -> bool:
        return self._switching

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_switch(self, *args) -> bool:
        with self._lock:
            if self._switching:
                return False
            self._switching = True
            self._last_result = None
            self._last_error = None

        thread = threading.Thread(
            target=self._run_switch_thread,
            args=args,
            daemon=True,
        )
        thread.start()
        return True

    def _run_switch_thread(self, *args):
        try:
            result = self._run_script(*args, timeout=45)
            with self._lock:
                self._last_result = result
                if not result.get('ok', True):
                    self._last_error = result.get('error', 'unknown error')
                else:
                    self._last_error = None
        except Exception as e:
            logger.error(f"Wi-Fi switch failed: {e}")
            with self._lock:
                self._last_error = str(e)
                self._last_result = None
        finally:
            with self._lock:
                self._switching = False

    def _run_script(self, *args, timeout: int = 30) -> dict:
        cmd = ['sudo', _SWITCH_SCRIPT] + list(args)
        logger.debug(f"Running: {' '.join(cmd[:3])} ...")  # don't log credentials

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"wifi_switch.sh timed out after {timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"wifi_switch.sh not found at {_SWITCH_SCRIPT}")

        stdout = proc.stdout.strip()
        if proc.returncode != 0 and not stdout:
            raise RuntimeError(f"wifi_switch.sh exited {proc.returncode}: {proc.stderr.strip()}")

        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"Unexpected output from wifi_switch.sh: {stdout[:200]}")
