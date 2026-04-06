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
import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scanner import Scanner
from audio_pipeline import AudioPipeline
from recorder import Recorder
from frequency_store import FrequencyStore
from web_server import create_app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger('carsdr')

_SRC_DIR   = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR  = os.path.normpath(os.path.join(_SRC_DIR, '..'))
_DATA_DIR  = os.path.join(_ROOT_DIR, 'data')


def load_config() -> dict:
    config_path = os.path.join(_ROOT_DIR, 'config.yaml')
    if not os.path.exists(config_path):
        logger.error(f"config.yaml not found at {config_path}")
        logger.error("Copy config.yaml.example to config.yaml and edit it first.")
        sys.exit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


def main():
    config = load_config()
    logger.info("CARSDR Station starting...")

    # Frequency persistence — loads from data/frequencies.json, seeds from config on first run
    store = FrequencyStore(config, _DATA_DIR)

    # Build scanner with frequencies from persistent store
    scanner = Scanner(config, initial_frequencies=store.get_all())

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
    scanner.on_lock(recorder.on_lock)
    scanner.on_unlock(recorder.on_unlock)

    # Tee audio to recorder when recording is active
    _original_put = scanner.audio_queue.put_nowait
    def _tee_put(data):
        _original_put(data)
        if recorder.is_recording:
            recorder.write_audio(data)
    scanner.audio_queue.put_nowait = _tee_put

    audio_pipeline.start()
    scanner.start()

    app = create_app(scanner, recorder, audio_pipeline, store, config)

    def shutdown(sig, frame):
        logger.info("Shutting down...")
        scanner.stop()
        audio_pipeline.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("Web server on http://0.0.0.0:5000")
    logger.info("Connect to 'CARSDR' Wi-Fi → open http://10.0.0.1:5000")

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


if __name__ == '__main__':
    main()
