"""SQLite storage for contacts and outreach drafts."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from netreach.models import Contact

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    public_id TEXT NOT NULL DEFAULT '',
    urn_id TEXT NOT NULL DEFAULT '',
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    headline TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    profile_url TEXT NOT NULL DEFAULT '',
    connected_on TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    enriched_at TEXT
);

CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    campaign TEXT NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    relevance TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(contact_id, campaign)
);
"""

# Names and connection dates don't change, so a later source only fills them in
# when they're blank. Everything else is refreshed by whichever source saw it last.
FILL_ONLY_FIELDS = ("first_name", "last_name", "connected_on", "public_id", "urn_id")
REFRESH_FIELDS = ("headline", "company", "position", "location", "email", "phone")

OUTREACH_STATUSES = ("draft", "sent", "replied", "skipped")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after a database was first created."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(outreach)")}
    with conn:
        for column in ("relevance", "reason"):
            if column not in columns:
                conn.execute(f"ALTER TABLE outreach ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def upsert_contact(conn: sqlite3.Connection, contact: Contact) -> bool:
    """Insert or merge a contact. Returns True when the contact is new."""
    ts = now()
    row = conn.execute("SELECT * FROM contacts WHERE key = ?", (contact.key,)).fetchone()
    if row is None:
        conn.execute(
            """INSERT INTO contacts (key, public_id, urn_id, first_name, last_name, headline,
                   company, position, location, email, phone, profile_url, connected_on,
                   sources, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contact.key, contact.public_id, contact.urn_id, contact.first_name,
                contact.last_name, contact.headline, contact.company, contact.position,
                contact.location, contact.email, contact.phone, contact.profile_url,
                contact.connected_on, contact.source, ts, ts,
            ),
        )
        return True

    updates = {}
    for field in FILL_ONLY_FIELDS:
        value = getattr(contact, field)
        if value and not row[field]:
            updates[field] = value
    for field in REFRESH_FIELDS:
        value = getattr(contact, field)
        if value and value != row[field]:
            updates[field] = value
    if contact.profile_url and not row["profile_url"]:
        updates["profile_url"] = contact.profile_url
    sources = {s for s in row["sources"].split(",") if s}
    if contact.source and contact.source not in sources:
        updates["sources"] = ",".join(sorted(sources | {contact.source}))
    updates["last_seen"] = ts

    assignments = ", ".join(f"{field} = ?" for field in updates)
    conn.execute(
        f"UPDATE contacts SET {assignments} WHERE id = ?", (*updates.values(), row["id"])
    )
    return False


def import_contacts(conn: sqlite3.Connection, contacts) -> tuple[int, int]:
    """Upsert an iterable of contacts in one transaction. Returns (new, updated)."""
    new = updated = 0
    with conn:
        for contact in contacts:
            if upsert_contact(conn, contact):
                new += 1
            else:
                updated += 1
    return new, updated


def set_contact_info(conn: sqlite3.Connection, contact_id: int, email: str, phone: str) -> None:
    with conn:
        conn.execute(
            """UPDATE contacts
               SET email = CASE WHEN ? != '' THEN ? ELSE email END,
                   phone = CASE WHEN ? != '' THEN ? ELSE phone END,
                   enriched_at = ?
               WHERE id = ?""",
            (email, email, phone, phone, now(), contact_id),
        )


def add_draft(conn: sqlite3.Connection, contact_id: int, campaign: str, message: str,
              relevance: str = "", reason: str = "") -> bool:
    """Store a draft unless the contact is already in this campaign. Returns True if added."""
    ts = now()
    cur = conn.execute(
        """INSERT OR IGNORE INTO outreach
               (contact_id, campaign, message, status, relevance, reason, created_at, updated_at)
           VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)""",
        (contact_id, campaign, message, relevance, reason, ts, ts),
    )
    return cur.rowcount == 1


def campaign_contact_ids(conn: sqlite3.Connection, campaign: str) -> set[int]:
    return {row[0] for row in conn.execute("SELECT contact_id FROM outreach WHERE campaign = ?", (campaign,))}


def set_outreach_status(conn: sqlite3.Connection, outreach_ids, status: str) -> int:
    if status not in OUTREACH_STATUSES:
        raise ValueError(f"status must be one of {', '.join(OUTREACH_STATUSES)}")
    ids = list(outreach_ids)
    with conn:
        cur = conn.execute(
            f"UPDATE outreach SET status = ?, updated_at = ? WHERE id IN ({', '.join('?' * len(ids))})",
            (status, now(), *ids),
        )
    return cur.rowcount
