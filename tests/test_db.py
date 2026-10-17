import pytest

from netreach import db
from netreach.models import Contact


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


def get(conn, key):
    return conn.execute("SELECT * FROM contacts WHERE key = ?", (key,)).fetchone()


def test_merges_csv_and_live_records_for_the_same_person(conn):
    csv_row = Contact(first_name="Jane", last_name="Doe", public_id="jane-doe", company="Acme",
                      position="CEO", email="jane@example.com", connected_on="2024-03-15", source="csv")
    live_row = Contact(first_name="J.", last_name="Doe", public_id="Jane-Doe", urn_id="ACoAA1",
                       headline="CEO at Acme | Speaker", connected_on="2024-03-16", source="linkedin")

    assert db.import_contacts(conn, [csv_row]) == (1, 0)
    assert db.import_contacts(conn, [live_row]) == (0, 1)

    row = get(conn, "in:jane-doe")
    assert row["first_name"] == "Jane"          # names only fill blanks
    assert row["connected_on"] == "2024-03-15"  # so do connection dates
    assert row["urn_id"] == "ACoAA1"
    assert row["headline"] == "CEO at Acme | Speaker"
    assert row["email"] == "jane@example.com"   # blanks never erase data
    assert row["sources"] == "csv,linkedin"
    assert row["profile_url"] == "https://www.linkedin.com/in/jane-doe"
    assert conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == 1


def test_refresh_fields_take_the_newest_non_blank_value(conn):
    db.import_contacts(conn, [Contact(first_name="A", public_id="a", company="Old Co", source="csv")])
    db.import_contacts(conn, [Contact(first_name="A", public_id="a", company="New Co", source="csv")])
    assert get(conn, "in:a")["company"] == "New Co"


def test_drafts_are_unique_per_campaign(conn):
    db.import_contacts(conn, [Contact(first_name="A", public_id="a")])
    contact_id = get(conn, "in:a")["id"]
    assert db.add_draft(conn, contact_id, "launch", "hi") is True
    assert db.add_draft(conn, contact_id, "launch", "hi again") is False
    assert db.add_draft(conn, contact_id, "followup", "hi") is True


def test_set_outreach_status_validates(conn):
    db.import_contacts(conn, [Contact(first_name="A", public_id="a")])
    db.add_draft(conn, get(conn, "in:a")["id"], "launch", "hi")
    assert db.set_outreach_status(conn, [1], "sent") == 1
    with pytest.raises(ValueError):
        db.set_outreach_status(conn, [1], "spammed")


def test_set_contact_info_keeps_existing_values_when_blank(conn):
    db.import_contacts(conn, [Contact(first_name="A", public_id="a", email="a@example.com")])
    contact_id = get(conn, "in:a")["id"]
    db.set_contact_info(conn, contact_id, "", "+1 555 0100")
    row = get(conn, "in:a")
    assert row["email"] == "a@example.com"
    assert row["phone"] == "+1 555 0100"
    assert row["enriched_at"]
