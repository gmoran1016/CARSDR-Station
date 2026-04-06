"""
web_server.py — Flask web server

Serves the phone UI and provides REST API endpoints for scanner control.
Also serves HLS audio segments from /tmp/carsdr/hls/.
"""

import os
import logging
from flask import Flask, jsonify, request, send_from_directory, send_file, abort

logger = logging.getLogger(__name__)

# Paths resolved relative to this file's location
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_WEB_DIR = os.path.join(_SRC_DIR, '..', 'web')


def create_app(scanner, recorder, audio_pipeline, config: dict):
    app = Flask(__name__, static_folder=None)
    app.config['JSON_SORT_KEYS'] = False

    recordings_path = config['recordings']['path']
    hls_dir = audio_pipeline.hls_dir

    # ------------------------------------------------------------------
    # Static web UI
    # ------------------------------------------------------------------

    @app.route('/')
    def index():
        return send_from_directory(_WEB_DIR, 'index.html')

    @app.route('/<path:filename>')
    def static_files(filename):
        return send_from_directory(_WEB_DIR, filename)

    # ------------------------------------------------------------------
    # HLS audio stream
    # ------------------------------------------------------------------

    @app.route('/hls/stream.m3u8')
    def hls_manifest():
        manifest = os.path.join(hls_dir, 'stream.m3u8')
        if not os.path.exists(manifest):
            abort(404)
        response = send_file(manifest, mimetype='application/vnd.apple.mpegurl')
        response.headers['Cache-Control'] = 'no-cache, no-store'
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response

    @app.route('/hls/<path:segment>')
    def hls_segment(segment):
        if not segment.endswith('.ts'):
            abort(404)
        response = send_from_directory(hls_dir, segment, mimetype='video/mp2t')
        response.headers['Cache-Control'] = 'no-cache, no-store'
        return response

    # ------------------------------------------------------------------
    # API: status
    # ------------------------------------------------------------------

    @app.route('/api/status')
    def api_status():
        return jsonify({
            'state': scanner.state,
            'current_freq': scanner.current_freq,
            'signal_level': round(scanner.signal_level, 1),
            'is_recording': recorder.is_recording,
            'recording_file': recorder.current_filename,
        })

    # ------------------------------------------------------------------
    # API: scanner control
    # ------------------------------------------------------------------

    @app.route('/api/scanner/start', methods=['POST'])
    def api_scanner_start():
        scanner.start()
        return jsonify({'ok': True, 'state': scanner.state})

    @app.route('/api/scanner/stop', methods=['POST'])
    def api_scanner_stop():
        scanner.stop()
        return jsonify({'ok': True, 'state': scanner.state})

    @app.route('/api/tune', methods=['POST'])
    def api_tune():
        data = request.get_json(force=True)
        freq_mhz = data.get('freq_mhz')
        if freq_mhz is None:
            return jsonify({'error': 'freq_mhz required'}), 400
        try:
            freq_mhz = float(freq_mhz)
        except (TypeError, ValueError):
            return jsonify({'error': 'freq_mhz must be a number'}), 400
        scanner.tune(freq_mhz)
        return jsonify({'ok': True, 'freq_mhz': freq_mhz})

    @app.route('/api/scanner/resume', methods=['POST'])
    def api_scanner_resume():
        scanner.resume_scan()
        return jsonify({'ok': True, 'state': scanner.state})

    # ------------------------------------------------------------------
    # API: frequencies
    # ------------------------------------------------------------------

    @app.route('/api/frequencies', methods=['GET'])
    def api_get_frequencies():
        return jsonify(scanner.get_frequencies())

    @app.route('/api/frequencies', methods=['POST'])
    def api_add_frequency():
        data = request.get_json(force=True)
        name = data.get('name', '').strip()
        freq_mhz = data.get('freq_mhz')
        if not name or freq_mhz is None:
            return jsonify({'error': 'name and freq_mhz required'}), 400
        try:
            freq_mhz = float(freq_mhz)
        except (TypeError, ValueError):
            return jsonify({'error': 'freq_mhz must be a number'}), 400
        scanner.add_frequency(name, freq_mhz, enabled=True)
        return jsonify({'ok': True}), 201

    @app.route('/api/frequencies/<freq_mhz>', methods=['DELETE'])
    def api_remove_frequency(freq_mhz):
        try:
            freq_mhz = float(freq_mhz)
        except ValueError:
            return jsonify({'error': 'invalid freq_mhz'}), 400
        scanner.remove_frequency(freq_mhz)
        return jsonify({'ok': True})

    @app.route('/api/frequencies/<freq_mhz>/toggle', methods=['POST'])
    def api_toggle_frequency(freq_mhz):
        try:
            freq_mhz = float(freq_mhz)
        except ValueError:
            return jsonify({'error': 'invalid freq_mhz'}), 400
        try:
            new_state = scanner.toggle_frequency(freq_mhz)
        except KeyError:
            return jsonify({'error': 'frequency not found'}), 404
        return jsonify({'ok': True, 'enabled': new_state})

    # ------------------------------------------------------------------
    # API: recordings
    # ------------------------------------------------------------------

    @app.route('/api/recordings', methods=['GET'])
    def api_list_recordings():
        return jsonify(recorder.list_recordings())

    @app.route('/api/recordings/<path:filename>', methods=['GET'])
    def api_get_recording(filename):
        # Sanitize — only allow simple filenames, no path traversal
        if '/' in filename or '\\' in filename or '..' in filename:
            abort(400)
        fpath = os.path.join(recordings_path, filename)
        if not os.path.exists(fpath):
            abort(404)
        return send_file(fpath, mimetype='audio/wav', as_attachment=True,
                         download_name=filename)

    return app
