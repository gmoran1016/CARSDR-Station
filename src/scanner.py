"""
scanner.py — RTL-SDR frequency scanner

Manages the rtl_fm subprocess and implements a state machine for
frequency hopping across a list of railroad channels.

States:
  SCANNING — cycling through enabled frequencies, looking for a signal
  LOCKED   — signal detected, staying on current frequency until quiet
  MANUAL   — user manually tuned to a specific frequency, no hopping
  STOPPED  — not running
"""

import subprocess
import threading
import queue
import time
import struct
import math
import logging
import os
import signal

logger = logging.getLogger(__name__)

STATE_STOPPED  = "STOPPED"
STATE_SCANNING = "SCANNING"
STATE_LOCKED   = "LOCKED"
STATE_MANUAL   = "MANUAL"

SILENCE_CHUNK = b'\x00' * 4096  # One chunk of silence for stream continuity


def _rms(data: bytes) -> float:
    """Calculate RMS amplitude of 16-bit signed PCM samples."""
    if not data:
        return 0.0
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f'<{count}h', data[:count * 2])
    mean_sq = sum(s * s for s in samples) / count
    return math.sqrt(mean_sq)


class Scanner:
    def __init__(self, config: dict, initial_frequencies: list = None):
        self._cfg = config
        self._sdr_cfg = config['sdr']
        self._scan_cfg = config['scanner']

        self._frequencies = []  # list of dicts: {name, freq_mhz, enabled}
        if initial_frequencies is not None:
            self._load_frequencies(initial_frequencies)
        else:
            self._load_frequencies(config.get('frequencies', []))

        self._state = STATE_STOPPED
        self._current_freq: float = 0.0
        self._signal_level: float = 0.0
        self._freq_index: int = 0

        self._audio_queue: queue.Queue = queue.Queue(maxsize=200)
        self._lock_callbacks: list = []   # called when LOCKED (freq_mhz)
        self._unlock_callbacks: list = [] # called when leaving LOCKED

        self._rtl_proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._scanner_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def current_freq(self) -> float:
        return self._current_freq

    @property
    def signal_level(self) -> float:
        return self._signal_level

    @property
    def audio_queue(self) -> queue.Queue:
        return self._audio_queue

    def on_lock(self, callback):
        """Register callback(freq_mhz) called when scanner locks onto a signal."""
        self._lock_callbacks.append(callback)

    def on_unlock(self, callback):
        """Register callback() called when scanner leaves a locked frequency."""
        self._unlock_callbacks.append(callback)

    def get_frequencies(self) -> list:
        return list(self._frequencies)

    def set_frequencies(self, frequencies: list):
        """Replace the frequency list. Restarts scanning if currently running."""
        self._frequencies = list(frequencies)
        self._freq_index = 0
        if self._state == STATE_SCANNING:
            self._hop_to_next()

    def add_frequency(self, name: str, freq_mhz: float, enabled: bool = True):
        self._frequencies.append({'name': name, 'freq_mhz': freq_mhz, 'enabled': enabled})

    def remove_frequency(self, freq_mhz: float):
        self._frequencies = [f for f in self._frequencies if f['freq_mhz'] != freq_mhz]

    def toggle_frequency(self, freq_mhz: float) -> bool:
        """Toggle enabled state. Returns new enabled value."""
        for f in self._frequencies:
            if f['freq_mhz'] == freq_mhz:
                f['enabled'] = not f['enabled']
                return f['enabled']
        raise KeyError(f"Frequency {freq_mhz} not found")

    def start(self):
        """Start scanning."""
        if self._state != STATE_STOPPED:
            return
        self._stop_event.clear()
        self._state = STATE_SCANNING
        self._scanner_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scanner_thread.start()
        logger.info("Scanner started")

    def stop(self):
        """Stop scanning and kill rtl_fm."""
        if self._state == STATE_STOPPED:
            return
        self._stop_event.set()
        self._kill_rtl()
        self._state = STATE_STOPPED
        # Drain and unblock audio queue
        try:
            while True:
                self._audio_queue.get_nowait()
        except queue.Empty:
            pass
        logger.info("Scanner stopped")

    def tune(self, freq_mhz: float):
        """Manually tune to a specific frequency and pause scanning."""
        was_running = self._state != STATE_STOPPED
        if was_running:
            self._kill_rtl()
        self._state = STATE_MANUAL
        self._current_freq = freq_mhz
        self._start_rtl(freq_mhz)
        logger.info(f"Manually tuned to {freq_mhz} MHz")

    def resume_scan(self):
        """Return from manual tune back to scanning."""
        if self._state == STATE_MANUAL:
            self._kill_rtl()
            self._state = STATE_SCANNING
            if not self._scanner_thread or not self._scanner_thread.is_alive():
                self._stop_event.clear()
                self._scanner_thread = threading.Thread(target=self._scan_loop, daemon=True)
                self._scanner_thread.start()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_frequencies(self, freq_list: list):
        self._frequencies = [
            {'name': f['name'], 'freq_mhz': float(f['freq_mhz']), 'enabled': bool(f['enabled'])}
            for f in freq_list
        ]

    def _enabled_freqs(self) -> list:
        return [f for f in self._frequencies if f['enabled']]

    def _start_rtl(self, freq_mhz: float):
        """Spawn rtl_fm tuned to freq_mhz."""
        self._kill_rtl()
        freq_hz = int(freq_mhz * 1e6)
        cmd = [
            'rtl_fm',
            '-f', str(freq_hz),
            '-M', 'fm',
            '-s', str(self._sdr_cfg['sample_rate']),
            '-r', str(self._sdr_cfg['resample_rate']),
            '-E', 'deemp',
            '-F', '9',
            '-l', str(self._sdr_cfg['squelch']),
            '-g', str(self._sdr_cfg['gain']),
            '-p', str(self._sdr_cfg['ppm_error']),
        ]
        logger.debug(f"Starting rtl_fm: {' '.join(cmd)}")
        try:
            self._rtl_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            self._current_freq = freq_mhz
            # Start reader thread for this process
            self._reader_thread = threading.Thread(
                target=self._read_audio,
                args=(self._rtl_proc,),
                daemon=True,
            )
            self._reader_thread.start()
        except FileNotFoundError:
            logger.error("rtl_fm not found — is rtl-sdr installed?")
            self._rtl_proc = None

    def _kill_rtl(self):
        """Terminate the rtl_fm process."""
        proc = self._rtl_proc
        self._rtl_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _read_audio(self, proc: subprocess.Popen):
        """Thread: reads raw PCM from rtl_fm stdout and puts it on the audio queue."""
        chunk_size = 4096
        while proc.poll() is None and not self._stop_event.is_set():
            try:
                data = proc.stdout.read(chunk_size)
            except Exception:
                break
            if not data:
                break
            self._signal_level = _rms(data)
            try:
                self._audio_queue.put_nowait(data)
            except queue.Full:
                pass  # Drop oldest by discarding; pipeline is too slow

    def _hop_to_next(self):
        """Switch to the next enabled frequency."""
        enabled = self._enabled_freqs()
        if not enabled:
            logger.warning("No enabled frequencies to scan")
            time.sleep(1)
            return
        self._freq_index = (self._freq_index + 1) % len(enabled)
        next_freq = enabled[self._freq_index]['freq_mhz']
        # Pad with silence so HLS stream doesn't stall during retune (~50ms)
        for _ in range(3):
            try:
                self._audio_queue.put_nowait(SILENCE_CHUNK)
            except queue.Full:
                pass
        self._start_rtl(next_freq)

    def _scan_loop(self):
        """Main scanning state machine loop."""
        dwell = self._scan_cfg['dwell_time_ms'] / 1000.0
        lock_timeout = self._scan_cfg['lock_timeout_s']
        threshold = self._scan_cfg['rms_threshold']

        enabled = self._enabled_freqs()
        if not enabled:
            logger.warning("No enabled frequencies — scanner idle")
            return

        # Start on first enabled frequency
        self._start_rtl(enabled[0]['freq_mhz'])
        last_signal_time = time.monotonic()
        time.sleep(0.1)  # Brief settle time after first tune

        while not self._stop_event.is_set():
            level = self._signal_level

            if self._state == STATE_SCANNING:
                if level >= threshold:
                    # Signal detected — lock onto this frequency
                    self._state = STATE_LOCKED
                    last_signal_time = time.monotonic()
                    logger.info(f"LOCKED: {self._current_freq} MHz (level={level:.0f})")
                    for cb in self._lock_callbacks:
                        try:
                            cb(self._current_freq)
                        except Exception:
                            pass
                else:
                    # No signal — dwell briefly then hop
                    time.sleep(dwell)
                    self._hop_to_next()
                    time.sleep(0.05)  # Settle after retune

            elif self._state == STATE_LOCKED:
                if level >= threshold:
                    last_signal_time = time.monotonic()
                else:
                    if time.monotonic() - last_signal_time >= lock_timeout:
                        logger.info(f"UNLOCKED from {self._current_freq} MHz — resuming scan")
                        self._state = STATE_SCANNING
                        for cb in self._unlock_callbacks:
                            try:
                                cb()
                            except Exception:
                                pass
                        self._hop_to_next()
                        time.sleep(0.05)
                time.sleep(0.1)

            elif self._state == STATE_MANUAL:
                # Manual mode — just keep reading, no hopping
                time.sleep(0.1)
