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
