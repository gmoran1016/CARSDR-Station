"""
recorder.py — Per-transmission WAV recorder

Triggered by scanner lock/unlock events. Each time the scanner locks
onto a frequency, a new WAV file is created and filled with raw PCM
audio. The file is finalized when the scanner unlocks.

Filename format: YYYY-MM-DD_HH-MM-SS_160.425MHz.wav
"""

import os
import wave
import queue
import threading
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Recorder:
    def __init__(self, audio_queue: queue.Queue, recordings_path: str,
                 sample_rate: int = 48000, max_files: int = 100):
        self._source_queue = audio_queue
        self._recordings_path = recordings_path
        self._sample_rate = sample_rate
        self._max_files = max_files

        self._recording = False
        self._current_freq: float = 0.0
        self._wav_file: wave.Wave_write | None = None
        self._current_filename: str | None = None
        self._lock = threading.Lock()

        os.makedirs(recordings_path, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API (called by scanner callbacks)
    # ------------------------------------------------------------------

    def on_lock(self, freq_mhz: float):
        """Called by scanner when it locks onto a signal."""
        with self._lock:
            if self._recording:
                self._finalize()
            self._start_recording(freq_mhz)

    def on_unlock(self):
        """Called by scanner when it leaves a locked frequency."""
        with self._lock:
            if self._recording:
                self._finalize()

    def write_audio(self, data: bytes):
        """Write PCM audio bytes to the current recording (if active)."""
        with self._lock:
            if self._recording and self._wav_file:
                try:
                    self._wav_file.writeframes(data)
                except Exception as e:
                    logger.error(f"WAV write error: {e}")

    def list_recordings(self) -> list:
        """Return list of recording metadata dicts, newest first."""
        try:
            files = [
                f for f in os.listdir(self._recordings_path)
                if f.endswith('.wav')
            ]
        except FileNotFoundError:
            return []
        files.sort(reverse=True)
        result = []
        for fname in files:
            fpath = os.path.join(self._recordings_path, fname)
            try:
                stat = os.stat(fpath)
                size_kb = stat.st_size // 1024
                # Parse freq from filename: YYYY-MM-DD_HH-MM-SS_160.425MHz.wav
                parts = fname.replace('.wav', '').split('_')
                freq_str = parts[2] if len(parts) >= 3 else 'unknown'
                result.append({
                    'filename': fname,
                    'freq': freq_str,
                    'size_kb': size_kb,
                    'timestamp': f"{parts[0]} {parts[1].replace('-', ':')}",
                })
            except Exception:
                continue
        return result

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_filename(self) -> str | None:
        return self._current_filename

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_recording(self, freq_mhz: float):
        self._current_freq = freq_mhz
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fname = f"{timestamp}_{freq_mhz}MHz.wav"
        fpath = os.path.join(self._recordings_path, fname)
        try:
            self._wav_file = wave.open(fpath, 'wb')
            self._wav_file.setnchannels(1)
            self._wav_file.setsampwidth(2)  # 16-bit
            self._wav_file.setframerate(self._sample_rate)
            self._current_filename = fname
            self._recording = True
            logger.info(f"Recording started: {fname}")
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            self._recording = False

    def _finalize(self):
        if self._wav_file:
            try:
                self._wav_file.close()
                logger.info(f"Recording saved: {self._current_filename}")
            except Exception as e:
                logger.error(f"Failed to finalize recording: {e}")
            self._wav_file = None
        self._recording = False
        self._current_filename = None
        self._enforce_max_files()

    def _enforce_max_files(self):
        """Delete oldest recordings if over the limit."""
        try:
            files = sorted([
                f for f in os.listdir(self._recordings_path)
                if f.endswith('.wav')
            ])
            while len(files) > self._max_files:
                oldest = os.path.join(self._recordings_path, files.pop(0))
                os.remove(oldest)
                logger.info(f"Deleted old recording: {oldest}")
        except Exception as e:
            logger.warning(f"Error enforcing recording limit: {e}")
