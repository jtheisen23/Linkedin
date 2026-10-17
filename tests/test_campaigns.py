import json
import time

import pytest

from netreach import ai_drafts, db, webapp
from netreach.models import Contact


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "camp.db"
    conn = db.connect(path)
    db.import_contacts(conn, [
        Contact(first_name="Jane", last_name="Doe", public_id="jane", company="Acme", position="Founder"),
        Contact(first_name="John", last_name="Smith", public_id="john", company="Globex", position="CEO"),
        Contact(first_name="Ann", last_name="Lee", public_id="ann", company="Initech", position="Engineer"),
    ])
    db.change_tags(conn, [1, 2], add=["Founders"])
    conn.close()
    return path


@pytest.fixture
def fake_claude(monkeypatch):
    calls = []

    def write_draft(client, brief, contact, model, effort):
        calls.append(contact["first_name"])
        if contact["first_name"] == "John":
            raise ai_drafts.DraftError("Claude declined to write this one.")
        return ai_drafts.Draft(f"Hi {contact['first_name']}!", "high", "Founder of a small company.")

    monkeypatch.setattr(ai_drafts, "make_client", lambda: object())
    monkeypatch.setattr(ai_drafts, "write_draft", write_draft)
    return calls


def call(app, method, path, query=None, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    status, _, payload, _ = app.handle(method, path, query or {}, raw)
    return status, json.loads(payload)


def wait_for_job(app, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = app.drafting.state()
        if not job["running"]:
            return job
        time.sleep(0.02)
    raise AssertionError("drafting job didn't finish")


def test_plan_counts_people_and_cost(db_path):
    app = webapp.App(db_path)
    status, plan = call(app, "POST", "/api/campaigns/plan", body={"campaign": "Oct", "tag": "Founders", "limit": 25})
    assert status == 200 and plan["count"] == 2 and plan["already"] == 0
    assert plan["cost"] == [0.02, 0.08]

    assert call(app, "POST", "/api/campaigns/plan", body={"campaign": "", "tag": "Founders"})[0] == 400
    assert call(app, "POST", "/api/campaigns/plan", body={"campaign": "Oct", "limit": 0})[0] == 400


def test_draft_run_saves_drafts_in_the_background(db_path, fake_claude):
    app = webapp.App(db_path)
    status, data = call(app, "POST", "/api/campaigns/draft",
                        body={"campaign": " Oct  launch ", "tag": "Founders", "limit": 25,
                              "brief": "<!-- note -->Selling dashboards.", "effort": "low"})
    assert status == 200 and data["job"]["campaign"] == "Oct launch"
    job = wait_for_job(app)
    assert (job["done"], job["failed"], job["error"]) == (1, 1, "")
    assert "John Smith: Claude declined" in job["errors"][0]
    assert sorted(fake_claude) == ["Jane", "John"]

    status, data = call(app, "GET", "/api/campaigns")
    campaign = data["campaigns"][0]
    assert campaign["name"] == "Oct launch" and campaign["draft"] == 1 and campaign["brief"] == "Selling dashboards."

    # A second run only goes to people not in the campaign yet: John, who failed.
    fake_claude.clear()
    status, plan = call(app, "POST", "/api/campaigns/plan", body={"campaign": "Oct launch", "tag": "Founders"})
    assert (plan["count"], plan["already"]) == (1, 1)
    call(app, "POST", "/api/campaigns/draft", body={"campaign": "Oct launch", "tag": "Founders", "brief": "x"})
    wait_for_job(app)
    assert fake_claude == ["John"]


def test_draft_run_validation(db_path, fake_claude):
    app = webapp.App(db_path)
    assert call(app, "POST", "/api/campaigns/draft", body={"campaign": "A", "brief": " "})[0] == 400
    assert call(app, "POST", "/api/campaigns/draft", body={"campaign": "A", "brief": "b", "effort": "max"})[0] == 400
    call(app, "POST", "/api/campaigns/draft", body={"campaign": "A", "brief": "b", "tag": "Founders"})
    wait_for_job(app)
    status, data = call(app, "POST", "/api/campaigns/draft", body={"campaign": "A", "brief": "b", "tag": "nobody"})
    assert status == 400 and "No one new" in data["error"]


def test_only_one_run_at_a_time_and_stop(db_path, monkeypatch):
    import threading

    release = threading.Event()

    def slow_draft(client, brief, contact, model, effort):
        release.wait(5)
        return ai_drafts.Draft("Hi", "low", "")

    monkeypatch.setattr(ai_drafts, "make_client", lambda: object())
    monkeypatch.setattr(ai_drafts, "write_draft", slow_draft)
    app = webapp.App(db_path)
    assert call(app, "POST", "/api/campaigns/draft", body={"campaign": "A", "brief": "b", "limit": 3})[0] == 200
    status, data = call(app, "POST", "/api/campaigns/draft", body={"campaign": "B", "brief": "b"})
    assert status == 409 and "already writing" in data["error"]

    call(app, "POST", "/api/drafting/stop", body={})
    release.set()
    job = wait_for_job(app)
    assert job["stopped"] and job["done"] == 1  # stops after the first draft that finishes


def test_fatal_errors_are_reported(db_path, monkeypatch):
    def broken(client, brief, contact, model, effort):
        raise ai_drafts.FatalDraftError("The Anthropic API rejected your credentials.")

    monkeypatch.setattr(ai_drafts, "make_client", lambda: object())
    monkeypatch.setattr(ai_drafts, "write_draft", broken)
    app = webapp.App(db_path)
    call(app, "POST", "/api/campaigns/draft", body={"campaign": "A", "brief": "b"})
    job = wait_for_job(app)
    assert job["error"].startswith("The Anthropic API rejected") and job["done"] == 0


def test_outreach_list_edit_and_status(db_path):
    conn = db.connect(db_path)
    db.add_draft(conn, 1, "Oct", "Hi Jane", "medium", "")
    db.add_draft(conn, 2, "Oct", "Hi John", "high", "")
    conn.commit()
    conn.close()
    app = webapp.App(db_path)

    status, data = call(app, "GET", "/api/outreach", {"campaign": ["Oct"], "status": ["draft"]})
    assert data["total"] == 2 and [i["first_name"] for i in data["items"]] == ["John", "Jane"]  # best fit first
    jane = data["items"][1]["id"]

    assert call(app, "POST", f"/api/outreach/{jane}", body={"message": "Hey Jane"})[0] == 200
    status, data = call(app, "POST", f"/api/outreach/{jane}", body={"status": "sent"})
    assert data["sent_today"] == 1
    status, data = call(app, "GET", "/api/outreach", {"campaign": ["Oct"], "status": ["sent"]})
    assert data["items"][0]["message"] == "Hey Jane" and data["items"][0]["sent_at"]

    # Back to drafts clears the sent date, so it no longer counts toward today's pace.
    call(app, "POST", f"/api/outreach/{jane}", body={"status": "draft"})
    assert call(app, "GET", "/api/campaigns")[1]["sent_today"] == 0

    assert call(app, "POST", f"/api/outreach/{jane}", body={"message": "  "})[0] == 400
    assert call(app, "POST", f"/api/outreach/{jane}", body={"status": "spam"})[0] == 400
    assert call(app, "POST", "/api/outreach/999", body={"status": "sent"})[0] == 404
    assert call(app, "GET", "/api/outreach", {"campaign": ["Oct"], "status": ["nope"]})[0] == 400


def test_replied_keeps_the_original_sent_date(db_path):
    conn = db.connect(db_path)
    db.add_draft(conn, 1, "Oct", "Hi", "", "")
    db.set_outreach_status(conn, [1], "sent")
    sent_at = conn.execute("SELECT sent_at FROM outreach").fetchone()[0]
    db.set_outreach_status(conn, [1], "replied")
    assert conn.execute("SELECT sent_at FROM outreach").fetchone()[0] == sent_at != ""
    assert db.sent_today(conn) == 1


def test_campaigns_from_the_cli_appear_without_a_brief(db_path):
    conn = db.connect(db_path)
    db.add_draft(conn, 1, "template-run", "Hi", "", "")
    db.save_campaign(conn, "empty-one", "")
    db.save_campaign(conn, "briefed", "First brief")
    db.save_campaign(conn, "briefed", "")  # an empty brief never overwrites
    campaigns = {c["name"]: c for c in db.list_campaigns(conn)}
    assert campaigns["template-run"]["draft"] == 1 and campaigns["template-run"]["brief"] == ""
    assert campaigns["empty-one"]["total"] == 0
    assert campaigns["briefed"]["brief"] == "First brief"
