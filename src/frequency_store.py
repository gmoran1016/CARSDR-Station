"""
frequency_store.py — Persistent frequency list

Stores the user's frequency list in data/frequencies.json so changes
made via the web UI (add, remove, toggle, import) survive reboots.

On first run (no JSON file), seeds the list from config.yaml.
"""

import os
import json
import threading
import logging

logger = logging.getLogger(__name__)


class FrequencyStore:
    def __init__(self, config: dict, data_dir: str):
        self._path = os.path.join(data_dir, 'frequencies.json')
        self._lock = threading.Lock()
        os.makedirs(data_dir, exist_ok=True)
        self._freqs = self._load(config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all(self) -> list:
        with self._lock:
            return list(self._freqs)

    def add(self, name: str, freq_mhz: float, enabled: bool = True) -> dict:
        entry = {'name': name, 'freq_mhz': round(float(freq_mhz), 5), 'enabled': enabled}
        with self._lock:
            self._freqs.append(entry)
            self._save()
        return entry

    def remove(self, freq_mhz: float):
        freq_mhz = round(float(freq_mhz), 5)
        with self._lock:
            before = len(self._freqs)
            self._freqs = [f for f in self._freqs if round(f['freq_mhz'], 5) != freq_mhz]
            if len(self._freqs) < before:
                self._save()

    def toggle(self, freq_mhz: float) -> bool:
        freq_mhz = round(float(freq_mhz), 5)
        with self._lock:
            for f in self._freqs:
                if round(f['freq_mhz'], 5) == freq_mhz:
                    f['enabled'] = not f['enabled']
                    self._save()
                    return f['enabled']
        raise KeyError(f"Frequency {freq_mhz} not found")

    def bulk_add(self, entries: list) -> int:
        """
        Add multiple frequencies, skipping exact duplicates (same freq_mhz).
        Returns the number of new entries added.
        """
        with self._lock:
            existing = {round(f['freq_mhz'], 5) for f in self._freqs}
            added = 0
            for e in entries:
                mhz = round(float(e['freq_mhz']), 5)
                if mhz not in existing:
                    self._freqs.append({
                        'name': e.get('name', f"{mhz} MHz"),
                        'freq_mhz': mhz,
                        'enabled': True,
                    })
                    existing.add(mhz)
                    added += 1
            if added:
                self._save()
        return added

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self, config: dict) -> list:
        if os.path.exists(self._path):
            try:
                with open(self._path, 'r') as f:
                    freqs = json.load(f)
                logger.info(f"Loaded {len(freqs)} frequencies from {self._path}")
                return freqs
            except Exception as e:
                logger.warning(f"Could not read {self._path}: {e} — seeding from config")

        # First run: seed from config.yaml frequencies list
        freqs = [
            {
                'name': str(f['name']),
                'freq_mhz': round(float(f['freq_mhz']), 5),
                'enabled': bool(f['enabled']),
            }
            for f in config.get('frequencies', [])
        ]
        self._freqs = freqs
        self._save()
        logger.info(f"Seeded {len(freqs)} frequencies from config.yaml → {self._path}")
        return freqs

    def _save(self):
        try:
            with open(self._path, 'w') as f:
                json.dump(self._freqs, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save frequencies: {e}")
