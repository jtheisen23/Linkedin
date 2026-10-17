from pathlib import Path

import pytest

from netreach import cli
from netreach.sources import linkedin_sync

FIXTURE = Path(__file__).parent / "fixtures" / "Connections.csv"


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # keep any real .env out of the tests
    db_path = str(tmp_path / "net.db")

    def _run(*args):
        return cli.main(["--db", db_path, *args])

    return _run


def test_import_list_draft_queue_mark(run, capsys, tmp_path):
    assert run("import-csv", str(FIXTURE)) == 0
    assert "3 new" in capsys.readouterr().out

    assert run("list", "--title", "founder,engineer") == 0
    out = capsys.readouterr().out
    assert "Jane Doe" in out and "José García" in out and "John Smith" not in out

    assert run("draft", "launch", "--message", "Hi {first_name} at {company|your team}", "--title", "founder") == 0
    assert "1 drafts added" in capsys.readouterr().out
    assert run("draft", "launch", "--message", "Hi {first_name}", "--title", "founder") == 0
    assert "1 already in it" in capsys.readouterr().out

    assert run("queue", "launch") == 0
    out = capsys.readouterr().out
    assert "Hi Jane at Acme Corp" in out and "https://www.linkedin.com/in/jane-doe-123" in out

    assert run("mark", "1", "--status", "sent") == 0
    assert run("queue", "launch") == 0
    assert "0 draft" in capsys.readouterr().out

    out_csv = tmp_path / "out.csv"
    assert run("export", str(out_csv), "--has-email") == 0
    lines = out_csv.read_text().splitlines()
    assert lines[0].startswith("first_name,last_name,email")
    assert len(lines) == 2 and "jane@example.com" in lines[1]


def test_draft_rejects_bad_template(run, capsys):
    run("import-csv", str(FIXTURE))
    assert run("draft", "x", "--message", "Hi {frist_name}") == 1
    assert "Unknown placeholder" in capsys.readouterr().err


def test_import_csv_reports_bad_files(run, capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("nope\n")
    assert run("import-csv", str(bad)) == 1
    assert "error:" in capsys.readouterr().err


def test_sync_saves_partial_results_before_an_error(run, capsys, monkeypatch):
    from netreach.models import Contact

    def fake_iter(api, **kwargs):
        yield Contact(first_name="Ann", last_name="Lee", public_id="ann", source="linkedin")
        raise linkedin_sync.SyncError("LinkedIn is rate limiting this account (HTTP 429). Stop for today.")

    monkeypatch.setattr(linkedin_sync, "make_client", lambda *a: object())
    monkeypatch.setattr(linkedin_sync, "whoami", lambda api: "Pat")
    monkeypatch.setattr(linkedin_sync, "iter_connections", fake_iter)

    assert run("sync") == 1
    captured = capsys.readouterr()
    assert "1 new" in captured.out and "429" in captured.err
    assert run("list") == 0
    assert "Ann Lee" in capsys.readouterr().out


def test_sync_without_cookies_explains_setup(run, capsys, monkeypatch):
    monkeypatch.delenv("LINKEDIN_LI_AT", raising=False)
    monkeypatch.delenv("LINKEDIN_JSESSIONID", raising=False)
    assert run("sync") == 1
    assert ".env.example" in capsys.readouterr().err


def test_draft_with_claude(run, capsys, monkeypatch, tmp_path):
    from netreach import ai_drafts

    calls = []

    def fake_write_draft(client, brief, contact, model, effort):
        calls.append((contact["first_name"], model, effort, brief))
        if contact["first_name"] == "John":
            raise ai_drafts.DraftError("Claude declined to write this one.")
        relevance = "high" if contact["first_name"] == "José" else "low"
        return ai_drafts.Draft(f"Hey {contact['first_name']}!", relevance, f"{contact['position']} fit")

    monkeypatch.setattr(ai_drafts, "make_client", lambda: object())
    monkeypatch.setattr(ai_drafts, "write_draft", fake_write_draft)
    brief = tmp_path / "brief.md"
    brief.write_text("<!-- note to self -->\nSelling dashboards.")

    run("import-csv", str(FIXTURE))
    capsys.readouterr()
    assert run("draft", "launch", "--brief", str(brief), "--effort", "low", "--workers", "2") == 0
    out = capsys.readouterr()
    assert "2 drafts added, 1 failed" in out.out and "skipped John Smith" in out.err
    assert sorted(c[0] for c in calls) == ["Jane", "John", "José"]
    assert all(c[1:] == ("claude-opus-5-5", "low", "Selling dashboards.") for c in calls)

    # Rerunning only retries the contact that failed.
    calls.clear()
    assert run("draft", "launch", "--brief", str(brief)) == 0
    assert [c[0] for c in calls] == ["John"]
    capsys.readouterr()

    # The queue shows the best fits first, with Claude's reasoning.
    assert run("queue", "launch") == 0
    out = capsys.readouterr().out
    assert out.index("José") < out.index("Jane")
    assert "fit: high (Software Engineer fit)" in out


def test_draft_with_claude_stops_on_fatal_errors(run, capsys, monkeypatch, tmp_path):
    from netreach import ai_drafts

    def fake_write_draft(*args):
        raise ai_drafts.FatalDraftError("The Anthropic API rejected your credentials.")

    monkeypatch.setattr(ai_drafts, "make_client", lambda: object())
    monkeypatch.setattr(ai_drafts, "write_draft", fake_write_draft)
    brief = tmp_path / "brief.md"
    brief.write_text("Selling dashboards.")
    run("import-csv", str(FIXTURE))
    assert run("draft", "launch", "--brief", str(brief), "--workers", "1") == 1
    assert "rejected your credentials" in capsys.readouterr().err
    assert run("queue", "launch") == 0
    assert "0 draft" in capsys.readouterr().out


def test_old_databases_get_new_outreach_columns(tmp_path):
    import sqlite3

    from netreach import db

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE outreach (id INTEGER PRIMARY KEY, contact_id INTEGER NOT NULL, campaign TEXT NOT NULL,
            message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(contact_id, campaign));
    """)
    old.close()
    conn = db.connect(path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(outreach)")}
    assert {"relevance", "reason"} <= columns
