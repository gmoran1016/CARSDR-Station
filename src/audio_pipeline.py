"""
audio_pipeline.py — HLS audio streaming pipeline

Reads raw PCM audio from the scanner's queue and feeds it into ffmpeg,
which encodes to AAC and writes HLS segments (.m3u8 + .ts files).

iOS Safari has native HLS support, so the web UI can play the stream
directly with an <audio> tag pointing at stream.m3u8.

HLS latency: ~4 seconds (1s segments × 4 in playlist).
"""

import subprocess
import threading
import queue
import time
import os
import logging

logger = logging.getLogger(__name__)

HLS_DIR = "/tmp/carsdr/hls"
HLS_MANIFEST = os.path.join(HLS_DIR, "stream.m3u8")

SILENCE_CHUNK = b'\x00' * 4096


class AudioPipeline:
    def __init__(self, audio_queue: queue.Queue, sample_rate: int = 48000):
        self._queue = audio_queue
        self._sample_rate = sample_rate
        self._proc: subprocess.Popen | None = None
        self._writer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self):
        os.makedirs(HLS_DIR, exist_ok=True)
        self._stop_event.clear()
        self._start_ffmpeg()
        self._writer_thread = threading.Thread(target=self._write_loop, daemon=True)
        self._writer_thread.start()
        logger.info(f"Audio pipeline started — HLS output: {HLS_MANIFEST}")

    def stop(self):
        self._stop_event.set()
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        logger.info("Audio pipeline stopped")

    @property
    def hls_manifest(self) -> str:
        return HLS_MANIFEST

    @property
    def hls_dir(self) -> str:
        return HLS_DIR

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _start_ffmpeg(self):
        """Launch ffmpeg: stdin PCM → AAC HLS."""
        cmd = [
            'ffmpeg',
            '-loglevel', 'error',
            '-f', 's16le',
            '-ar', str(self._sample_rate),
            '-ac', '1',
            '-i', 'pipe:0',
            # Audio codec
            '-c:a', 'aac',
            '-b:a', '64k',
            # HLS output
            '-f', 'hls',
            '-hls_time', '1',
            '-hls_list_size', '10',
            '-hls_flags', 'delete_segments+append_list',
            '-hls_segment_filename', os.path.join(HLS_DIR, 'seg%05d.ts'),
            HLS_MANIFEST,
        ]
        logger.debug(f"Starting ffmpeg: {' '.join(cmd)}")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

    def _write_loop(self):
        """Thread: drains audio queue and writes PCM bytes to ffmpeg stdin."""
        silence_counter = 0
        while not self._stop_event.is_set():
            proc = self._proc
            if proc is None or proc.poll() is not None:
                # ffmpeg died — restart it
                logger.warning("ffmpeg process died, restarting...")
                self._start_ffmpeg()
                time.sleep(0.5)
                continue

            try:
                data = self._queue.get(timeout=0.2)
                silence_counter = 0
            except queue.Empty:
                # Keep ffmpeg fed with silence so the HLS stream stays live
                silence_counter += 1
                data = SILENCE_CHUNK
                if silence_counter > 50:
                    # Longer sleep during extended silence to avoid CPU spin
                    time.sleep(0.05)

            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except BrokenPipeError:
                logger.warning("ffmpeg stdin closed (BrokenPipe)")
                self._proc = None
            except Exception as e:
                logger.error(f"Audio write error: {e}")
                self._proc = None
