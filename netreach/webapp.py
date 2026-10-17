"""A local web page for searching, tagging and taking notes on your network.

It listens on 127.0.0.1 only, so nothing else on your network can reach it. It also
refuses requests addressed to any other host name (which blocks DNS-rebinding
tricks), and only accepts changes sent as JSON from the page itself, so other
websites open in your browser can't read or change your data.
"""

import io
import json
import os
import re
import sqlite3
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from netreach import ai_drafts, db
from netreach.export import write_contacts_csv
from netreach.segment import ORDERS, Filters, contact_ids, count_contacts, select_contacts

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
PAGE_SIZE, MAX_PAGE_SIZE = 100, 500
MAX_BODY = 1_000_000
LIST_FIELDS = ("id", "first_name", "last_name", "headline", "company", "position", "location",
               "email", "connected_on", "profile_url")
_CONTACT_PATH = re.compile(r"^/api/contacts/(\d+)(/notes|/tags)?$")
_OUTREACH_PATH = re.compile(r"^/api/outreach/(\d+)$")
MAX_DRAFTS_PER_RUN = 1000


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _flag(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


def filters_from_query(query: dict, paged: bool = True) -> Filters:
    def get(key):
        return (query.get(key) or [""])[0].strip()

    try:
        limit = min(max(int(get("limit") or PAGE_SIZE), 1), MAX_PAGE_SIZE)
        offset = max(int(get("offset") or 0), 0)
    except ValueError:
        raise ApiError(400, "limit and offset must be numbers") from None
    return Filters(
        keyword=get("keyword"), title=get("title"), company=get("company"),
        location=get("location"), tag=get("tag"), untagged=_flag(get("untagged")),
        connected_after=get("connected_after"), connected_before=get("connected_before"),
        has_email=_flag(get("has_email")),
        limit=limit if paged else None, offset=offset if paged else 0,
    )


def _order(query: dict) -> str:
    order = (query.get("sort") or ["recent"])[0]
    if order not in ORDERS:
        raise ApiError(400, f"sort must be one of {', '.join(ORDERS)}")
    return order


def _names(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ApiError(400, "tag lists must be lists of strings")
    return value


class DraftJob:
    """Has Claude write a campaign's drafts in a background thread, one run at a time.

    Each draft is saved as it arrives, so stopping (or closing netreach) keeps what's done.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._state = {"running": False}

    def state(self) -> dict:
        with self._lock:
            return {**self._state, "errors": list(self._state.get("errors", []))}

    def start(self, campaign: str, brief: str, contacts: list[dict], effort: str) -> None:
        with self._lock:
            if self._state.get("running"):
                raise ApiError(409, "Claude is already writing drafts. Wait for it to finish, or stop it first.")
            self._stop.clear()
            self._state = {
                "running": True, "campaign": campaign, "total": len(contacts), "done": 0, "failed": 0,
                "errors": [], "error": "", "stopped": False, "started_at": db.now(), "finished_at": "",
            }
        threading.Thread(target=self._run, args=(campaign, brief, contacts, effort), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, campaign: str, brief: str, contacts: list[dict], effort: str) -> None:
        conn = db.connect(self.db_path)
        drafts = None
        try:
            drafts = ai_drafts.draft_many(ai_drafts.make_client(), brief, contacts, effort=effort)
            for contact, result in drafts:
                if isinstance(result, ai_drafts.DraftError):
                    name = f"{contact['first_name']} {contact['last_name']}".strip()
                    with self._lock:
                        self._state["failed"] += 1
                        self._state["errors"] = (self._state["errors"] + [f"{name}: {result}"])[-5:]
                else:
                    with conn:
                        db.add_draft(conn, contact["id"], campaign, result.message, result.relevance, result.reason)
                    with self._lock:
                        self._state["done"] += 1
                if self._stop.is_set():
                    with self._lock:
                        self._state["stopped"] = True
                    break
        except ai_drafts.FatalDraftError as exc:
            with self._lock:
                self._state["error"] = str(exc)
        except Exception as exc:  # report anything unexpected on the page instead of dying silently
            with self._lock:
                self._state["error"] = f"Something went wrong: {exc!r}"
        finally:
            if drafts is not None:
                drafts.close()
            conn.close()
            with self._lock:
                self._state["running"] = False
                self._state["finished_at"] = db.now()


def _audience(conn: sqlite3.Connection, campaign: str, data: dict) -> tuple[list[dict], int]:
    """People a run would draft for: matching the tag/title, not already in the campaign, up to the limit.

    Returns (contacts, how many matching people were skipped because they're already in it).
    """
    try:
        limit = int(data.get("limit", 25))
    except (TypeError, ValueError):
        raise ApiError(400, "limit must be a number") from None
    if not 1 <= limit <= MAX_DRAFTS_PER_RUN:
        raise ApiError(400, f"Choose between 1 and {MAX_DRAFTS_PER_RUN} people per run.")
    filters = Filters(tag=str(data.get("tag") or ""), title=str(data.get("title") or ""))
    already = db.campaign_contact_ids(conn, campaign)
    matching = select_contacts(conn, filters)
    fresh = [dict(row) for row in matching if row["id"] not in already]
    return fresh[:limit], len(matching) - len(fresh)


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


class App:
    """Routes requests to handlers. Kept separate from HTTP plumbing so it's easy to test."""

    def __init__(self, db_path: str | Path):
        self.db_path = db_path
        self.drafting = DraftJob(db_path)

    def handle(self, method: str, path: str, query: dict, body: bytes):
        """Return (status, content_type, payload_bytes, extra_headers)."""
        try:
            if method == "GET" and path in STATIC_FILES:
                name, content_type = STATIC_FILES[path]
                headers = {"Content-Security-Policy": CSP} if name.endswith(".html") else {}
                return 200, content_type, (STATIC_DIR / name).read_bytes(), headers
            data = json.loads(body) if body else {}
            if not isinstance(data, dict):
                raise ApiError(400, "request body must be a JSON object")
            conn = db.connect(self.db_path)
            try:
                return self._route(conn, method, path, query, data)
            finally:
                conn.close()
        except ApiError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except ValueError as exc:  # bad JSON, invalid tag names
            return self._json({"error": str(exc)}, 400)
        except KeyError as exc:
            return self._json({"error": f"not found: {exc.args[0]}"}, 404)

    @staticmethod
    def _json(payload, status: int = 200):
        return status, "application/json", json.dumps(payload).encode(), {}

    def _route(self, conn: sqlite3.Connection, method: str, path: str, query: dict, data: dict):
        if method == "GET" and path == "/api/contacts":
            filters = filters_from_query(query)
            rows = select_contacts(conn, filters, _order(query))
            tags = db.tags_for(conn, [row["id"] for row in rows])
            contacts = [{**{k: row[k] for k in LIST_FIELDS}, "tags": tags[row["id"]]} for row in rows]
            return self._json({"total": count_contacts(conn, filters), "contacts": contacts})

        if method == "GET" and path == "/api/contact-ids":
            return self._json({"ids": contact_ids(conn, filters_from_query(query, paged=False))})

        if method == "GET" and path == "/api/stats":
            total = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            return self._json({"total": total, "untagged": count_contacts(conn, Filters(untagged=True))})

        if method == "GET" and path == "/api/tags":
            return self._json({"tags": db.list_tags(conn)})

        if method == "GET" and path == "/export.csv":
            filters = filters_from_query(query, paged=False)
            rows = select_contacts(conn, filters, _order(query))
            out = io.StringIO()
            write_contacts_csv(conn, rows, out)
            return 200, "text/csv; charset=utf-8", out.getvalue().encode("utf-8"), {
                "Content-Disposition": 'attachment; filename="netreach-contacts.csv"'
            }

        if method == "POST" and path == "/api/bulk-tags":
            ids = data.get("contact_ids")
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
                raise ApiError(400, "contact_ids must be a list of numbers")
            db.change_tags(conn, ids, add=_names(data.get("add")), remove=_names(data.get("remove")))
            return self._json({"ok": True, "count": len(ids)})

        if method == "POST" and path == "/api/tags/rename":
            db.rename_tag(conn, str(data.get("old", "")), str(data.get("new", "")))
            return self._json({"ok": True})

        if method == "POST" and path == "/api/tags/delete":
            db.delete_tag(conn, str(data.get("name", "")))
            return self._json({"ok": True})

        if method == "GET" and path == "/api/campaigns":
            return self._json({
                "campaigns": db.list_campaigns(conn),
                "sent_today": db.sent_today(conn),
                "has_api_key": _has_api_key(),
                "job": self.drafting.state(),
            })

        if method == "GET" and path == "/api/outreach":
            campaign = (query.get("campaign") or [""])[0]
            status = (query.get("status") or ["draft"])[0]
            filters = filters_from_query(query)
            total, items = db.campaign_outreach(conn, campaign, status, filters.limit, filters.offset)
            return self._json({"total": total, "items": items})

        if method == "POST" and path in ("/api/campaigns/plan", "/api/campaigns/draft"):
            campaign = db.normalize_campaign(str(data.get("campaign", "")))
            contacts, already = _audience(conn, campaign, data)
            low, high = ai_drafts.COST_PER_DRAFT.get(str(data.get("effort") or "medium"), ai_drafts.COST_PER_DRAFT["medium"])
            plan = {"count": len(contacts), "already": already, "cost": [round(low * len(contacts), 2),
                                                                        round(high * len(contacts), 2)]}
            if path.endswith("/plan"):
                return self._json(plan)
            effort = str(data.get("effort") or "medium")
            if effort not in ai_drafts.EFFORTS:
                raise ApiError(400, f"effort must be one of {', '.join(ai_drafts.EFFORTS)}")
            brief = ai_drafts.clean_brief(str(data.get("brief", "")))
            if not brief:
                raise ApiError(400, "Write a brief so Claude knows what you're reaching out about.")
            if not contacts:
                raise ApiError(400, "No one new to write to. Everyone matching is already in this campaign.")
            db.save_campaign(conn, campaign, brief)
            self.drafting.start(campaign, brief, contacts, effort)
            return self._json({**plan, "job": self.drafting.state()})

        if method == "GET" and path == "/api/drafting":
            return self._json({"job": self.drafting.state()})

        if method == "POST" and path == "/api/drafting/stop":
            self.drafting.stop()
            return self._json({"job": self.drafting.state()})

        match = _OUTREACH_PATH.match(path)
        if match and method == "POST":
            message, status = data.get("message"), data.get("status")
            if message is not None and not isinstance(message, str):
                raise ApiError(400, "message must be text")
            if status is not None and not isinstance(status, str):
                raise ApiError(400, "status must be text")
            if not db.update_outreach(conn, int(match.group(1)), message=message, status=status):
                raise ApiError(404, "message not found")
            return self._json({"ok": True, "sent_today": db.sent_today(conn)})

        match = _CONTACT_PATH.match(path)
        if match:
            contact_id, action = int(match.group(1)), match.group(2)
            row = conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
            if row is None:
                raise ApiError(404, "contact not found")
            if method == "GET" and action is None:
                return self._json(self._contact_detail(conn, row))
            if method == "POST" and action == "/notes":
                notes = data.get("notes")
                if not isinstance(notes, str):
                    raise ApiError(400, "notes must be text")
                db.set_notes(conn, contact_id, notes)
                return self._json({"ok": True})
            if method == "POST" and action == "/tags":
                db.change_tags(conn, [contact_id], add=_names(data.get("add")), remove=_names(data.get("remove")))
                return self._json({"tags": db.tags_for(conn, [contact_id])[contact_id]})

        raise ApiError(404, "no such page")

    @staticmethod
    def _contact_detail(conn: sqlite3.Connection, row) -> dict:
        outreach = conn.execute(
            """SELECT campaign, status, relevance, reason, message, updated_at FROM outreach
               WHERE contact_id = ? ORDER BY updated_at DESC""",
            (row["id"],),
        ).fetchall()
        detail = {k: row[k] for k in row.keys() if k not in ("key", "urn_id")}
        detail["tags"] = db.tags_for(conn, [row["id"]])[row["id"]]
        detail["outreach"] = [dict(o) for o in outreach]
        return detail


class Handler(BaseHTTPRequestHandler):
    app: App  # set by make_server
    server_version = "netreach"

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        port = self.server.server_address[1]
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return self._send(403, "text/plain", b"Forbidden host")
        if method == "POST":
            if self.headers.get_content_type() != "application/json":
                return self._send(415, "text/plain", b"Send JSON")
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{host}":
                return self._send(403, "text/plain", b"Forbidden origin")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, "text/plain", b"Bad Content-Length")
        if length > MAX_BODY:
            return self._send(413, "text/plain", b"Too large")
        body = self.rfile.read(length) if length else b""
        url = urlparse(self.path)
        status, content_type, payload, headers = self.app.handle(method, url.path, parse_qs(url.query), body)
        self._send(status, content_type, payload, headers)

    def _send(self, status: int, content_type: str, payload: bytes, headers: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # keep the terminal quiet
        pass


def make_server(db_path: str | Path, port: int) -> ThreadingHTTPServer:
    handler = type("NetreachHandler", (Handler,), {"app": App(db_path)})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(db_path: str | Path, port: int, open_browser: bool = True) -> None:
    server = make_server(db_path, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"netreach is running at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
