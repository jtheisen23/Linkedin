"""Read LinkedIn's own data export (Connections.csv, or the whole export ZIP)."""

import csv
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from netreach.models import Contact, public_id_from_url

SOURCE = "csv"
_HEADER_RE = re.compile(r"^First Name,", re.MULTILINE)
_DATE_FORMATS = ("%d %b %Y", "%d-%b-%y", "%d %B %Y", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d")


def parse_date(raw: str) -> str:
    raw = (raw or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _read_text(path: Path) -> str:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith("connections.csv")]
            if not names:
                raise ValueError(f"No Connections.csv inside {path}")
            return archive.read(names[0]).decode("utf-8-sig")
    return path.read_text(encoding="utf-8-sig")


def read_connections(path: str | Path):
    """Yield a Contact for each row of a LinkedIn connections export.

    The export starts with a "Notes:" preamble before the real header row, so
    parsing begins at the line that starts with "First Name,".
    """
    text = _read_text(Path(path))
    header = _HEADER_RE.search(text)
    if header is None:
        raise ValueError(f"{path} doesn't look like a LinkedIn Connections export")

    for row in csv.DictReader(io.StringIO(text[header.start():])):
        def get(column):
            return (row.get(column) or "").strip()

        if not (get("First Name") or get("Last Name") or get("URL")):
            continue
        yield Contact(
            first_name=get("First Name"),
            last_name=get("Last Name"),
            public_id=public_id_from_url(get("URL")),
            email=get("Email Address"),
            company=get("Company"),
            position=get("Position"),
            connected_on=parse_date(get("Connected On")),
            source=SOURCE,
        )
