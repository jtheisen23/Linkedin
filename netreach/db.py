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
    notes TEXT NOT NULL DEFAULT '',
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
    sent_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(contact_id, campaign)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS contact_tags (
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (contact_id, tag_id)
);
CREATE INDEX IF NOT EXISTS contact_tags_by_tag ON contact_tags(tag_id);

CREATE TABLE IF NOT EXISTS campaigns (
    name TEXT PRIMARY KEY,
    brief TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

# Names and connection dates don't change, so a later source only fills them in
# when they're blank. Everything else is refreshed by whichever source saw it last.
FILL_ONLY_FIELDS = ("first_name", "last_name", "connected_on", "public_id", "urn_id")
REFRESH_FIELDS = ("headline", "company", "position", "location", "email", "phone")

OUTREACH_STATUSES = ("draft", "sent", "replied", "skipped")
MAX_TAG_LENGTH = 40
MAX_CAMPAIGN_LENGTH = 60

# Status changes keep sent_at in step: set the first time a message counts as sent,
# cleared when it goes back to being a draft. Parameters: (status, status, now, status).
_SENT_AT_SQL = """sent_at = CASE
        WHEN ? IN ('sent', 'replied') THEN CASE WHEN sent_at = '' THEN ? ELSE sent_at END
        WHEN ? = 'draft' THEN ''
        ELSE sent_at END"""


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
    added = {"outreach": ("relevance", "reason", "sent_at"), "contacts": ("notes",)}
    with conn:
        for table, new_columns in added.items():
            columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column in new_columns:
                if column not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


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
    ts = now()
    with conn:
        cur = conn.execute(
            f"""UPDATE outreach SET status = ?, updated_at = ?, {_SENT_AT_SQL}
                WHERE id IN ({', '.join('?' * len(ids))})""",
            (status, ts, status, ts, status, *ids),
        )
    return cur.rowcount


def update_outreach(conn: sqlite3.Connection, outreach_id: int, message: str | None = None,
                    status: str | None = None) -> bool:
    """Edit a draft's text and/or move it to another status. Returns False if it doesn't exist."""
    if status is not None and status not in OUTREACH_STATUSES:
        raise ValueError(f"status must be one of {', '.join(OUTREACH_STATUSES)}")
    if message is not None and not message.strip():
        raise ValueError("The message can't be empty.")
    ts = now()
    sets, params = ["updated_at = ?"], [ts]
    if message is not None:
        sets.append("message = ?")
        params.append(message)
    if status is not None:
        sets += ["status = ?", _SENT_AT_SQL]
        params += [status, status, ts, status]
    with conn:
        cur = conn.execute(f"UPDATE outreach SET {', '.join(sets)} WHERE id = ?", (*params, outreach_id))
    return cur.rowcount == 1


def sent_today(conn: sqlite3.Connection) -> int:
    """Messages marked sent since midnight on this computer's clock."""
    return conn.execute(
        """SELECT COUNT(*) FROM outreach
           WHERE sent_at != '' AND date(sent_at, 'localtime') = date('now', 'localtime')"""
    ).fetchone()[0]


# --- campaigns ------------------------------------------------------------------------


def normalize_campaign(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Give the campaign a name.")
    if len(name) > MAX_CAMPAIGN_LENGTH:
        raise ValueError(f"Campaign names can be at most {MAX_CAMPAIGN_LENGTH} characters.")
    return name


def save_campaign(conn: sqlite3.Connection, name: str, brief: str) -> None:
    """Remember a campaign and its brief. An empty brief never overwrites a saved one."""
    with conn:
        conn.execute(
            """INSERT INTO campaigns (name, brief, created_at) VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET brief = excluded.brief WHERE excluded.brief != ''""",
            (name, brief, now()),
        )


def list_campaigns(conn: sqlite3.Connection) -> list[dict]:
    """Every campaign with counts per status, most recently active first."""
    counts = {
        row["name"]: dict(row)
        for row in conn.execute(
            """SELECT campaign AS name, COUNT(*) AS total,
                      SUM(status = 'draft') AS draft, SUM(status = 'sent') AS sent,
                      SUM(status = 'replied') AS replied, SUM(status = 'skipped') AS skipped,
                      MAX(updated_at) AS last_activity
               FROM outreach GROUP BY campaign"""
        )
    }
    saved = {row["name"]: row for row in conn.execute("SELECT name, brief, created_at FROM campaigns")}
    empty = {"total": 0, "draft": 0, "sent": 0, "replied": 0, "skipped": 0, "last_activity": ""}
    campaigns = []
    for name in counts.keys() | saved.keys():
        info = {**empty, **counts.get(name, {}), "name": name}
        info["brief"] = saved[name]["brief"] if name in saved else ""
        info["created_at"] = saved[name]["created_at"] if name in saved else ""
        campaigns.append(info)
    campaigns.sort(key=lambda c: max(c["last_activity"] or "", c["created_at"] or ""), reverse=True)
    return campaigns


def campaign_outreach(conn: sqlite3.Connection, campaign: str, status: str = "draft",
                      limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """One page of a campaign's messages in a status. Drafts come best fit first; the rest newest first."""
    if status not in OUTREACH_STATUSES:
        raise ValueError(f"status must be one of {', '.join(OUTREACH_STATUSES)}")
    total = conn.execute(
        "SELECT COUNT(*) FROM outreach WHERE campaign = ? AND status = ?", (campaign, status)
    ).fetchone()[0]
    order = (
        "CASE o.relevance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, o.id"
        if status == "draft" else "o.updated_at DESC, o.id DESC"
    )
    rows = conn.execute(
        f"""SELECT o.id, o.contact_id, o.message, o.status, o.relevance, o.reason, o.sent_at, o.updated_at,
                   c.first_name, c.last_name, c.headline, c.company, c.position, c.profile_url
            FROM outreach o JOIN contacts c ON c.id = o.contact_id
            WHERE o.campaign = ? AND o.status = ?
            ORDER BY {order} LIMIT ? OFFSET ?""",
        (campaign, status, limit, offset),
    ).fetchall()
    return total, [dict(row) for row in rows]


# --- tags and notes -------------------------------------------------------------------


def normalize_tag(name: str) -> str:
    """Tidy a tag name, or raise ValueError if it can't be one.

    Commas aren't allowed because filters use them to mean "any of these tags".
    """
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Tag names can't be empty.")
    if "," in name:
        raise ValueError("Tag names can't contain commas.")
    if len(name) > MAX_TAG_LENGTH:
        raise ValueError(f"Tag names can be at most {MAX_TAG_LENGTH} characters.")
    return name


def _tag_id(conn: sqlite3.Connection, name: str, create: bool) -> int | None:
    if create:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


def change_tags(conn: sqlite3.Connection, contact_ids, add=(), remove=()) -> None:
    """Add and/or remove tags (by name) on many contacts at once, creating new tags as needed."""
    ids = [int(i) for i in contact_ids]
    add = [normalize_tag(n) for n in add]
    remove = [normalize_tag(n) for n in remove]
    with conn:
        for name in add:
            tag_id = _tag_id(conn, name, create=True)
            conn.executemany(
                """INSERT OR IGNORE INTO contact_tags (contact_id, tag_id)
                   SELECT id, ? FROM contacts WHERE id = ?""",
                [(tag_id, i) for i in ids],
            )
        for name in remove:
            tag_id = _tag_id(conn, name, create=False)
            if tag_id is not None:
                conn.executemany(
                    "DELETE FROM contact_tags WHERE contact_id = ? AND tag_id = ?",
                    [(i, tag_id) for i in ids],
                )


def tags_for(conn: sqlite3.Connection, contact_ids) -> dict[int, list[str]]:
    ids = [int(i) for i in contact_ids]
    result = {i: [] for i in ids}
    # Chunked to stay well under SQLite's limit on query parameters.
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = conn.execute(
            f"""SELECT ct.contact_id, t.name FROM contact_tags ct JOIN tags t ON t.id = ct.tag_id
                WHERE ct.contact_id IN ({", ".join("?" * len(chunk))})
                ORDER BY t.name COLLATE NOCASE""",
            chunk,
        )
        for row in rows:
            result[row["contact_id"]].append(row["name"])
    return result


def list_tags(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT t.name, COUNT(ct.contact_id) AS count
           FROM tags t LEFT JOIN contact_tags ct ON ct.tag_id = t.id
           GROUP BY t.id ORDER BY t.name COLLATE NOCASE"""
    )
    return [dict(row) for row in rows]


def rename_tag(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Rename a tag. Renaming onto an existing tag merges the two."""
    old, new = normalize_tag(old), normalize_tag(new)
    with conn:
        old_id = _tag_id(conn, old, create=False)
        if old_id is None:
            raise KeyError(old)
        new_id = _tag_id(conn, new, create=False)
        if new_id is None or new_id == old_id:
            conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new, old_id))
            return
        conn.execute(
            """INSERT OR IGNORE INTO contact_tags (contact_id, tag_id)
               SELECT contact_id, ? FROM contact_tags WHERE tag_id = ?""",
            (new_id, old_id),
        )
        conn.execute("DELETE FROM tags WHERE id = ?", (old_id,))


def delete_tag(conn: sqlite3.Connection, name: str) -> None:
    with conn:
        conn.execute("DELETE FROM tags WHERE name = ?", (normalize_tag(name),))


def set_notes(conn: sqlite3.Connection, contact_id: int, notes: str) -> bool:
    with conn:
        cur = conn.execute("UPDATE contacts SET notes = ? WHERE id = ?", (notes, contact_id))
    return cur.rowcount == 1
