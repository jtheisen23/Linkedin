"""Command-line entry point: `netreach <command>`."""

import argparse
import csv
import os
import random
import sys
import time
from pathlib import Path

from netreach import db, templates
from netreach.segment import Filters, select_contacts
from netreach.sources import csv_export, linkedin_sync

DEFAULT_DB = "data/netreach.db"
EXPORT_COLUMNS = (
    "first_name", "last_name", "email", "phone", "company", "position", "headline",
    "location", "profile_url", "connected_on", "sources",
)


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from .env without overriding variables already set."""
    env_file = Path(path)
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'"))


def add_filter_args(parser: argparse.ArgumentParser, default_limit: int | None = None) -> None:
    group = parser.add_argument_group("filters (comma-separated values mean 'any of')")
    group.add_argument("--keyword", default="", help="match name, headline, company, position or location")
    group.add_argument("--title", default="", help="match position or headline, e.g. 'founder,ceo'")
    group.add_argument("--company", default="", help="match company or headline")
    group.add_argument("--location", default="")
    group.add_argument("--connected-after", default="", metavar="YYYY-MM-DD")
    group.add_argument("--connected-before", default="", metavar="YYYY-MM-DD")
    group.add_argument("--has-email", action="store_true", help="only people who shared an email")
    group.add_argument("--limit", type=int, default=default_limit)


def filters_from(args) -> Filters:
    return Filters(
        keyword=args.keyword, title=args.title, company=args.company, location=args.location,
        connected_after=args.connected_after, connected_before=args.connected_before,
        has_email=args.has_email, limit=args.limit,
    )


def _truncate(value: str, width: int) -> str:
    value = value or ""
    return value if len(value) <= width else value[: width - 1] + "…"


def print_contacts(rows) -> None:
    widths = (24, 44, 10, 40)
    print(f"{'NAME':<{widths[0]}}  {'ROLE':<{widths[1]}}  {'CONNECTED':<{widths[2]}}  PROFILE")
    for row in rows:
        name = f"{row['first_name']} {row['last_name']}".strip()
        role = " at ".join(p for p in (row["position"], row["company"]) if p) or row["headline"]
        print(
            f"{_truncate(name, widths[0]):<{widths[0]}}  {_truncate(role, widths[1]):<{widths[1]}}  "
            f"{row['connected_on'] or '-':<{widths[2]}}  {row['profile_url']}"
        )


# --- commands -------------------------------------------------------------------------


def cmd_import_csv(conn, args) -> int:
    try:
        contacts = list(csv_export.read_connections(args.path))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    new, updated = db.import_contacts(conn, contacts)
    print(f"Imported {len(contacts)} connections from {args.path}: {new} new, {updated} updated.")
    return 0


def cmd_sync(conn, args) -> int:
    try:
        api = linkedin_sync.make_client(
            os.environ.get("LINKEDIN_LI_AT", ""),
            os.environ.get("LINKEDIN_JSESSIONID", ""),
            os.environ.get("LINKEDIN_USER_AGENT", ""),
        )
        name = linkedin_sync.whoami(api)
    except linkedin_sync.SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Logged in as {name or 'your account'}. Reading connections (this is slow on purpose)...")

    contacts, error = [], None
    try:
        for contact in linkedin_sync.iter_connections(
            api, method=args.method, max_pages=args.max_pages, delay=args.delay
        ):
            contacts.append(contact)
            if len(contacts) % 200 == 0:
                print(f"  ...{len(contacts)} so far")
    except linkedin_sync.SyncError as exc:
        error = exc

    # Save whatever arrived before any error, so a partial run isn't wasted.
    new, updated = db.import_contacts(conn, contacts)
    print(f"Fetched {len(contacts)} connections: {new} new, {updated} updated.")
    if len(contacts) >= args.max_pages * linkedin_sync.PAGE_SIZE:
        print(f"Stopped at --max-pages {args.max_pages}; run again later with a higher value for the rest.")
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.enrich:
        return enrich(conn, api, args.enrich, args.delay)
    return 0


def enrich(conn, api, limit: int, delay: float) -> int:
    rows = conn.execute(
        """SELECT id, public_id FROM contacts
           WHERE enriched_at IS NULL AND public_id != ''
           ORDER BY connected_on DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    print(f"Fetching shared contact info for {len(rows)} connections...")
    found = failures = 0
    for row in rows:
        time.sleep(delay + random.uniform(0, delay))
        try:
            email, phone = linkedin_sync.fetch_contact_info(api, row["public_id"])
        except linkedin_sync.SyncError as exc:
            failures += 1
            print(f"  {exc}", file=sys.stderr)
            if failures >= 3:
                print("error: 3 failures in a row; stopping enrichment.", file=sys.stderr)
                return 1
            continue
        failures = 0
        found += bool(email)
        db.set_contact_info(conn, row["id"], email, phone)
    print(f"Done: {found} of {len(rows)} shared an email address.")
    return 0


def cmd_list(conn, args) -> int:
    rows = select_contacts(conn, filters_from(args))
    print_contacts(rows)
    print(f"\n{len(rows)} shown.")
    return 0


def cmd_stats(conn, args) -> int:
    total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    with_email = conn.execute("SELECT COUNT(*) FROM contacts WHERE email != ''").fetchone()[0]
    print(f"Connections: {total}   with shared email: {with_email}\n")

    print("Top companies:")
    for row in conn.execute(
        """SELECT company, COUNT(*) n FROM contacts WHERE company != ''
           GROUP BY company ORDER BY n DESC, company LIMIT 15"""
    ):
        print(f"  {row['n']:>5}  {row['company']}")

    print("\nConnections by year:")
    for row in conn.execute(
        """SELECT substr(connected_on, 1, 4) year, COUNT(*) n FROM contacts
           WHERE connected_on != '' GROUP BY year ORDER BY year"""
    ):
        print(f"  {row['year']}  {row['n']:>5}")

    campaigns = conn.execute(
        "SELECT campaign, status, COUNT(*) n FROM outreach GROUP BY campaign, status ORDER BY campaign, status"
    ).fetchall()
    if campaigns:
        print("\nOutreach:")
        for row in campaigns:
            print(f"  {row['campaign']:<24} {row['status']:<8} {row['n']:>5}")
    return 0


def cmd_export(conn, args) -> int:
    rows = select_contacts(conn, filters_from(args))
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(EXPORT_COLUMNS)
        for row in rows:
            writer.writerow([row[column] for column in EXPORT_COLUMNS])
    print(f"Wrote {len(rows)} contacts to {args.out}.")
    return 0


def cmd_draft(conn, args) -> int:
    template = Path(args.template).read_text() if args.template else args.message
    try:
        templates.check(template)
    except templates.TemplateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows = select_contacts(conn, filters_from(args))
    added = skipped = 0
    with conn:
        for row in rows:
            message = templates.render(template, row)
            if args.dry_run:
                print(f"--- {row['first_name']} {row['last_name']}  {row['profile_url']}\n{message}\n")
                continue
            if db.add_draft(conn, row["id"], args.campaign, message):
                added += 1
            else:
                skipped += 1
    if args.dry_run:
        print(f"{len(rows)} drafts previewed; nothing saved.")
    else:
        print(f"Campaign '{args.campaign}': {added} drafts added, {skipped} already in it.")
        print(f"Next: netreach queue {args.campaign}")
    return 0


def cmd_queue(conn, args) -> int:
    sql = """SELECT o.id, o.status, o.message, c.first_name, c.last_name, c.profile_url, c.email
             FROM outreach o JOIN contacts c ON c.id = o.contact_id
             WHERE o.campaign = ? AND o.status = ? ORDER BY o.id"""
    params = [args.campaign, args.status]
    if args.limit:
        sql += " LIMIT ?"
        params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(("id", "status", "first_name", "last_name", "profile_url", "email", "message"))
            for row in rows:
                writer.writerow([row[k] for k in ("id", "status", "first_name", "last_name", "profile_url", "email", "message")])
        print(f"Wrote {len(rows)} {args.status} messages to {args.csv}.")
        return 0

    for row in rows:
        print(f"[{row['id']}] {row['first_name']} {row['last_name']}  {row['profile_url']}")
        print(row["message"])
        print()
    print(f"{len(rows)} {args.status} in '{args.campaign}'. After sending: netreach mark <id>... --status sent")
    return 0


def cmd_mark(conn, args) -> int:
    try:
        changed = db.set_outreach_status(conn, args.ids, args.status)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Marked {changed} as {args.status}.")
    return 0


# --- parser ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netreach",
        description="Pull your LinkedIn connections into a local database, segment them, and draft outreach.",
    )
    parser.add_argument("--db", default=os.environ.get("NETREACH_DB", DEFAULT_DB),
                        help=f"SQLite database path (default: {DEFAULT_DB})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-csv", help="import LinkedIn's Connections.csv or the full export ZIP")
    p.add_argument("path")
    p.set_defaults(func=cmd_import_csv)

    p = sub.add_parser("sync", help="read your connections live from LinkedIn (unofficial, read-only)")
    p.add_argument("--method", choices=("auto", "connections", "search"), default="auto")
    p.add_argument("--max-pages", type=int, default=100,
                   help=f"pages of {linkedin_sync.PAGE_SIZE} connections to read at most (default 100)")
    p.add_argument("--delay", type=float, default=3.0,
                   help="extra seconds to wait between requests, plus random jitter (default 3)")
    p.add_argument("--enrich", type=int, default=0, metavar="N",
                   help="also fetch shared email/phone for up to N not-yet-checked connections")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("list", help="show contacts")
    add_filter_args(p, default_limit=50)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats", help="summary of your network and campaigns")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="write contacts to CSV (e.g. for a CRM or email tool)")
    p.add_argument("out")
    add_filter_args(p)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("draft", help="draft a personalized message for each matching contact")
    p.add_argument("campaign")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--template", help="file with the message template")
    source.add_argument("--message", help="message template given inline")
    p.add_argument("--dry-run", action="store_true", help="preview without saving")
    add_filter_args(p)
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("queue", help="show drafts to send by hand")
    p.add_argument("campaign")
    p.add_argument("--status", choices=db.OUTREACH_STATUSES, default="draft")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--csv", metavar="OUT", help="write the queue to a CSV instead of printing it")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("mark", help="update outreach status after you send or hear back")
    p.add_argument("ids", nargs="+", type=int)
    p.add_argument("--status", choices=db.OUTREACH_STATUSES, required=True)
    p.set_defaults(func=cmd_mark)

    return parser


def main(argv=None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    conn = db.connect(args.db)
    try:
        return args.func(conn, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
