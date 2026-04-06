"""
main.py — CARSDR Station entry point

Wires together scanner, audio pipeline, recorder, and web server,
then starts the Flask development server on port 5000.

Run with: python3 src/main.py
"""

import os
import sys
import signal
import logging
import threading
import yaml

# Allow imports from src/ regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner import Scanner
from audio_pipeline import AudioPipeline
from recorder import Recorder
from web_server import create_app

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('carsdr')


# ------------------------------------------------------------------
# Config loading
# ------------------------------------------------------------------

def load_config() -> dict:
    config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', 'config.yaml'
    )
    config_path = os.path.normpath(config_path)
    if not os.path.exists(config_path):
        logger.error(f"config.yaml not found at {config_path}")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main():
    config = load_config()
    logger.info("CARSDR Station starting...")

    # Build components
    scanner = Scanner(config)
    audio_pipeline = AudioPipeline(
        audio_queue=scanner.audio_queue,
        sample_rate=config['sdr']['resample_rate'],
    )
    recorder = Recorder(
        audio_queue=scanner.audio_queue,
        recordings_path=config['recordings']['path'],
        sample_rate=config['sdr']['resample_rate'],
        max_files=config['recordings'].get('max_files', 100),
    )

    # Wire recorder to scanner events
    # The scanner feeds audio to its queue; recorder needs a separate tap.
    # We override scanner callbacks to also forward audio to recorder.
    scanner.on_lock(recorder.on_lock)
    scanner.on_unlock(recorder.on_unlock)

    # Patch scanner's _read_audio to also forward to recorder
    # We do this by wrapping the audio queue put with recorder write
    _original_queue_put = scanner.audio_queue.put_nowait

    def _tee_put(data):
        _original_queue_put(data)
        if recorder.is_recording:
            recorder.write_audio(data)

    scanner.audio_queue.put_nowait = _tee_put

    # Start audio pipeline
    audio_pipeline.start()

    # Start scanner
    scanner.start()

    # Build and run Flask app
    app = create_app(scanner, recorder, audio_pipeline, config)

    # Graceful shutdown on Ctrl+C / SIGTERM
    def shutdown(sig, frame):
        logger.info("Shutting down...")
        scanner.stop()
        audio_pipeline.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Web server starting on http://0.0.0.0:5000")
    logger.info("Connect your phone to the 'CARSDR' Wi-Fi, then open http://10.0.0.1:5000")

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        use_reloader=False,  # Reloader would spawn a second scanner process
        threaded=True,
    )


if __name__ == '__main__':
    main()
