import http.client
import json
import threading

import pytest

from netreach import db, webapp
from netreach.models import Contact


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "web.db"
    conn = db.connect(path)
    db.import_contacts(conn, [
        Contact(first_name="Jane", last_name="Doe", public_id="jane", company="Acme", position="COO",
                email="jane@example.com", connected_on="2024-03-15", source="csv"),
        Contact(first_name="John", last_name="Smith", public_id="john", company="Globex",
                headline="<img src=x onerror=alert(1)>", connected_on="2023-01-02", source="linkedin"),
    ])
    db.add_draft(conn, 1, "launch", "Hi Jane", "high", "Runs ops")
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def app(db_path):
    return webapp.App(db_path)


def call(app, method, path, query=None, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    status, content_type, payload, headers = app.handle(method, path, query or {}, raw)
    if content_type == "application/json":
        return status, json.loads(payload)
    return status, payload


def test_serves_the_page_with_a_strict_content_security_policy(app):
    status, content_type, payload, headers = app.handle("GET", "/", {}, b"")
    assert status == 200 and b"/app.js" in payload
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]
    assert app.handle("GET", "/app.js", {}, b"")[0] == 200
    assert app.handle("GET", "/style.css", {}, b"")[0] == 200


def test_list_search_and_paging(app):
    status, data = call(app, "GET", "/api/contacts")
    assert status == 200 and data["total"] == 2
    assert [c["first_name"] for c in data["contacts"]] == ["Jane", "John"]
    assert "notes" not in data["contacts"][0]  # the list stays light

    status, data = call(app, "GET", "/api/contacts", {"company": ["globex"]})
    assert [c["first_name"] for c in data["contacts"]] == ["John"]

    status, data = call(app, "GET", "/api/contacts", {"limit": ["1"], "offset": ["1"], "sort": ["name"]})
    assert data["total"] == 2 and [c["first_name"] for c in data["contacts"]] == ["John"]

    assert call(app, "GET", "/api/contacts", {"sort": ["bogus"]})[0] == 400
    assert call(app, "GET", "/api/contacts", {"limit": ["many"]})[0] == 400


def test_tagging_notes_and_detail(app):
    status, data = call(app, "POST", "/api/contacts/1/tags", body={"add": ["warm", "investor"]})
    assert status == 200 and data["tags"] == ["investor", "warm"]
    assert call(app, "POST", "/api/contacts/1/notes", body={"notes": "Coffee in May"})[0] == 200

    status, detail = call(app, "GET", "/api/contacts/1")
    assert detail["tags"] == ["investor", "warm"]
    assert detail["notes"] == "Coffee in May"
    assert detail["outreach"][0]["campaign"] == "launch"
    assert "key" not in detail

    status, data = call(app, "GET", "/api/contacts", {"keyword": ["coffee"]})
    assert [c["first_name"] for c in data["contacts"]] == ["Jane"]
    status, data = call(app, "GET", "/api/contacts", {"untagged": ["1"]})
    assert [c["first_name"] for c in data["contacts"]] == ["John"]

    assert call(app, "GET", "/api/contacts/999")[0] == 404
    assert call(app, "POST", "/api/contacts/1/tags", body={"add": ["a,b"]})[0] == 400
    assert call(app, "POST", "/api/contacts/1/notes", body={"notes": 5})[0] == 400


def test_bulk_tags_select_all_matching_and_tag_admin(app):
    status, data = call(app, "GET", "/api/contact-ids", {"keyword": ["o"]})
    assert sorted(data["ids"]) == [1, 2]
    assert call(app, "POST", "/api/bulk-tags", body={"contact_ids": data["ids"], "add": ["2024 event"]})[0] == 200

    status, data = call(app, "GET", "/api/tags")
    assert data["tags"] == [{"name": "2024 event", "count": 2}]
    assert call(app, "POST", "/api/tags/rename", body={"old": "2024 event", "new": "Event"})[0] == 200
    assert call(app, "POST", "/api/tags/rename", body={"old": "missing", "new": "x"})[0] == 404
    assert call(app, "GET", "/api/stats")[1] == {"total": 2, "untagged": 0}

    assert call(app, "POST", "/api/tags/delete", body={"name": "Event"})[0] == 200
    assert call(app, "GET", "/api/tags")[1]["tags"] == []
    assert call(app, "POST", "/api/bulk-tags", body={"contact_ids": ["1"], "add": ["x"]})[0] == 400


def test_export_csv_includes_tags_and_notes(app):
    call(app, "POST", "/api/contacts/1/tags", body={"add": ["warm"]})
    call(app, "POST", "/api/contacts/1/notes", body={"notes": "Coffee"})
    status, payload = call(app, "GET", "/export.csv", {"tag": ["warm"]})
    lines = payload.decode().splitlines()
    assert status == 200 and len(lines) == 2
    assert "tags" in lines[0] and "notes" in lines[0]
    assert "warm" in lines[1] and "Coffee" in lines[1]


@pytest.fixture
def server(db_path):
    srv = webapp.make_server(db_path, 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    return response.status, response.read()


def test_real_server_round_trip(server):
    status, payload = request(server, "GET", "/api/contacts")
    assert status == 200 and json.loads(payload)["total"] == 2
    status, _ = request(server, "POST", "/api/contacts/1/notes", json.dumps({"notes": "hi"}),
                        {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{server}"})
    assert status == 200


def test_rejects_other_host_names(server):
    # A page on evil.example that re-points its DNS at 127.0.0.1 still sends Host: evil.example.
    status, _ = request(server, "GET", "/api/contacts", headers={"Host": f"evil.example:{server}"})
    assert status == 403


def test_rejects_cross_site_writes(server):
    body = json.dumps({"notes": "pwned"})
    # Plain form posts (no JSON content type) are refused...
    status, _ = request(server, "POST", "/api/contacts/1/notes", body, {"Content-Type": "text/plain"})
    assert status == 415
    # ...and so is JSON from another origin.
    status, _ = request(server, "POST", "/api/contacts/1/notes", body,
                        {"Content-Type": "application/json", "Origin": "https://evil.example"})
    assert status == 403
    status, payload = request(server, "GET", "/api/contacts/1")
    assert json.loads(payload)["notes"] == ""
