"""
web_server.py — Flask web server

Serves the phone UI and provides REST API endpoints for scanner control.
Also serves HLS audio segments from /tmp/carsdr/hls/.

All frequency mutations go through both the FrequencyStore (persistence)
and the Scanner (in-memory runtime) to keep them in sync.
"""

import os
import logging
from flask import Flask, jsonify, request, send_from_directory, send_file, abort

from rr_client import fetch_frequencies, filter_railroad, normalize_url

logger = logging.getLogger(__name__)

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
_WEB_DIR = os.path.join(_SRC_DIR, '..', 'web')


def create_app(scanner, recorder, audio_pipeline, store, config: dict):
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
        # Don't let this catch /api or /hls routes
        if filename.startswith('api/') or filename.startswith('hls/'):
            abort(404)
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
            'state':          scanner.state,
            'current_freq':   scanner.current_freq,
            'signal_level':   round(scanner.signal_level, 1),
            'is_recording':   recorder.is_recording,
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
    # API: frequencies (all mutations go through store + scanner)
    # ------------------------------------------------------------------

    @app.route('/api/frequencies', methods=['GET'])
    def api_get_frequencies():
        return jsonify(store.get_all())

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
        entry = store.add(name, freq_mhz, enabled=True)
        scanner.add_frequency(entry['name'], entry['freq_mhz'], enabled=True)
        return jsonify({'ok': True}), 201

    @app.route('/api/frequencies/<freq_mhz>', methods=['DELETE'])
    def api_remove_frequency(freq_mhz):
        try:
            freq_mhz = float(freq_mhz)
        except ValueError:
            return jsonify({'error': 'invalid freq_mhz'}), 400
        store.remove(freq_mhz)
        scanner.remove_frequency(freq_mhz)
        return jsonify({'ok': True})

    @app.route('/api/frequencies/<freq_mhz>/toggle', methods=['POST'])
    def api_toggle_frequency(freq_mhz):
        try:
            freq_mhz = float(freq_mhz)
        except ValueError:
            return jsonify({'error': 'invalid freq_mhz'}), 400
        try:
            new_state = store.toggle(freq_mhz)
            scanner.toggle_frequency(freq_mhz)
        except KeyError:
            return jsonify({'error': 'frequency not found'}), 404
        return jsonify({'ok': True, 'enabled': new_state})

    # ------------------------------------------------------------------
    # API: RadioReference import
    # ------------------------------------------------------------------

    @app.route('/api/import/rr/preview', methods=['POST'])
    def api_rr_preview():
        """
        Fetch a RadioReference URL and return parsed frequencies for preview.
        Body: { "url": "https://www.radioreference.com/db/browse/ctid/XXXX" }
        Returns: { "entries": [...], "page_title": "...", "total": N }
        """
        data = request.get_json(force=True)
        url = (data.get('url') or '').strip()
        if not url:
            return jsonify({'error': 'url required'}), 400

        try:
            entries = fetch_frequencies(url)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        except Exception as e:
            logger.error(f"RR fetch error: {e}")
            return jsonify({'error': 'Failed to fetch RadioReference page'}), 502

        # Build unique tag list for the filter UI
        tags = sorted({e['tag'] for e in entries if e['tag']})

        return jsonify({
            'entries': entries,
            'tags': tags,
            'total': len(entries),
            'railroad_count': len(filter_railroad(entries)),
        })

    @app.route('/api/import/rr/confirm', methods=['POST'])
    def api_rr_confirm():
        """
        Import a list of selected frequencies into the persistent store.
        Body: { "entries": [{freq_mhz, name, ...}, ...] }
        Returns: { "added": N, "skipped": N }
        """
        data = request.get_json(force=True)
        entries = data.get('entries', [])
        if not entries:
            return jsonify({'error': 'entries required'}), 400

        added = store.bulk_add(entries)
        skipped = len(entries) - added

        # Sync the running scanner with newly added frequencies
        for entry in entries:
            try:
                scanner.add_frequency(
                    entry.get('name', f"{entry['freq_mhz']} MHz"),
                    float(entry['freq_mhz']),
                    enabled=True,
                )
            except Exception:
                pass

        return jsonify({'ok': True, 'added': added, 'skipped': skipped})

    # ------------------------------------------------------------------
    # API: recordings
    # ------------------------------------------------------------------

    @app.route('/api/recordings', methods=['GET'])
    def api_list_recordings():
        return jsonify(recorder.list_recordings())

    @app.route('/api/recordings/<path:filename>', methods=['GET'])
    def api_get_recording(filename):
        if '/' in filename or '\\' in filename or '..' in filename:
            abort(400)
        fpath = os.path.join(recordings_path, filename)
        if not os.path.exists(fpath):
            abort(404)
        return send_file(fpath, mimetype='audio/wav', as_attachment=True,
                         download_name=filename)

    return app
