"""
rr_client.py — RadioReference.com frequency importer

Fetches any RadioReference county/state/agency page and parses the
frequency tables. No account required — uses public web pages.

Supported URL patterns:
  https://www.radioreference.com/db/browse/ctid/XXXX   (county)
  https://www.radioreference.com/db/browse/stid/XX     (state)
  https://www.radioreference.com/db/browse/aid/XXXX    (agency)
  https://www.radioreference.com/apps/db/?ctid=XXXX    (legacy)

Returns a list of dicts:
  {freq_mhz, name, description, mode, tag, tone}
"""

import re
import logging
import urllib.request
import urllib.error
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Friendly browser UA to avoid bot blocks
_UA = (
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
)

# RadioReference tag values that indicate railroad-related entries
RAILROAD_TAGS = {
    'railroad', 'railways', 'rail', 'transportation',
    'railroad ops', 'railroad dispatch',
}


def normalize_url(url: str) -> str:
    """Convert legacy /apps/db/?ctid= URLs to canonical /db/browse/ctid/ form."""
    url = url.strip()
    # Legacy: ?ctid=, ?stid=, ?aid=
    m = re.search(r'[?&](ctid|stid|aid)=(\d+)', url)
    if m:
        kind, val = m.group(1), m.group(2)
        return f"https://www.radioreference.com/db/browse/{kind}/{val}"
    return url


def fetch_frequencies(url: str) -> list:
    """
    Fetch a RadioReference page and return all frequency entries found.
    Raises ValueError on bad URL, urllib.error.HTTPError on fetch failure.
    """
    url = normalize_url(url)
    if 'radioreference.com' not in url:
        raise ValueError("URL must be a radioreference.com page")

    logger.info(f"Fetching RR page: {url}")
    req = urllib.request.Request(url, headers={'User-Agent': _UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as e:
        raise ValueError(f"RadioReference returned HTTP {e.code}: {e.reason}")
    except Exception as e:
        raise ValueError(f"Could not reach RadioReference: {e}")

    entries = _parse_rrdb_tables(html)
    logger.info(f"Parsed {len(entries)} frequency entries from {url}")
    return entries


def filter_railroad(entries: list) -> list:
    """Return only entries whose tag suggests railroad use."""
    return [
        e for e in entries
        if e.get('tag', '').lower() in RAILROAD_TAGS
        or 'railroad' in e.get('tag', '').lower()
        or 'railroad' in e.get('description', '').lower()
        or 'railway' in e.get('description', '').lower()
    ]


# ---------------------------------------------------------------------------
# HTML parser — no third-party deps (pure stdlib)
# ---------------------------------------------------------------------------

class _RRTableParser(HTMLParser):
    """
    Minimal state-machine parser for RadioReference frequency tables.
    Looks for <table> elements whose class contains 'rrdbTable' and
    extracts rows from them.
    """

    def __init__(self):
        super().__init__()
        self.results: list = []

        self._in_target_table = False
        self._in_header = False
        self._in_cell = False
        self._current_row: list = []
        self._current_cell: str = ''
        self._headers: list = []
        self._depth = 0           # nested table depth counter

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            cls = attrs.get('class', '')
            if 'rrdbTable' in cls and not self._in_target_table:
                self._in_target_table = True
                self._headers = []
                self._depth = 0
            elif self._in_target_table:
                self._depth += 1  # nested table inside rrdbTable

        if not self._in_target_table:
            return

        if tag == 'th':
            self._in_header = True
            self._current_cell = ''
        elif tag == 'td':
            self._in_cell = True
            self._current_cell = ''

    def handle_endtag(self, tag):
        if tag == 'table' and self._in_target_table:
            if self._depth > 0:
                self._depth -= 1
            else:
                self._in_target_table = False

        if not self._in_target_table:
            return

        if tag == 'th' and self._in_header:
            self._headers.append(self._current_cell.strip())
            self._in_header = False
        elif tag == 'td' and self._in_cell:
            self._current_row.append(self._current_cell.strip())
            self._in_cell = False
        elif tag == 'tr':
            if self._current_row:
                self._process_row(self._current_row)
            self._current_row = []

    def handle_data(self, data):
        if self._in_header:
            self._current_cell += data
        elif self._in_cell:
            self._current_cell += data

    def _process_row(self, cells: list):
        if not self._headers or 'Frequency' not in self._headers:
            return

        def get(col):
            try:
                idx = self._headers.index(col)
                return cells[idx].strip() if idx < len(cells) else ''
            except ValueError:
                return ''

        freq_str = get('Frequency')
        # Strip any non-numeric suffix (e.g. "160.4250 W")
        freq_str = re.sub(r'[^\d.]', '', freq_str)
        try:
            freq_mhz = float(freq_str)
        except ValueError:
            return
        if freq_mhz <= 0:
            return

        alpha  = get('Alpha Tag')
        desc   = get('Description')
        mode   = get('Mode')
        tag    = get('Tag')
        tone   = get('Tone')

        name = alpha if alpha else desc if desc else f"{freq_mhz} MHz"

        self.results.append({
            'freq_mhz':    round(freq_mhz, 5),
            'name':        name,
            'description': desc,
            'mode':        mode,
            'tag':         tag,
            'tone':        tone,
        })


def _parse_rrdb_tables(html: str) -> list:
    parser = _RRTableParser()
    parser.feed(html)
    return parser.results
