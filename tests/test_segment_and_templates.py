import pytest

from netreach import db, templates
from netreach.models import Contact
from netreach.segment import Filters, select_contacts


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.import_contacts(c, [
        Contact(first_name="Jane", last_name="Doe", public_id="jane", company="Acme", position="Founder & CEO",
                email="jane@example.com", connected_on="2024-03-15", location="Austin"),
        Contact(first_name="John", last_name="Smith", public_id="john", company="Globex", position="VP Sales",
                connected_on="2021-06-01"),
        Contact(first_name="Ann", last_name="Lee", public_id="ann", headline="Owner at Lee Bakery",
                connected_on="2019-01-01"),
    ])
    yield c
    c.close()


def names(rows):
    return sorted(r["first_name"] for r in rows)


def test_title_matches_position_or_headline_with_any_of_terms(conn):
    assert names(select_contacts(conn, Filters(title="founder,owner"))) == ["Ann", "Jane"]


def test_filters_combine_with_and(conn):
    assert names(select_contacts(conn, Filters(title="founder,owner", connected_after="2020-01-01"))) == ["Jane"]
    assert names(select_contacts(conn, Filters(has_email=True))) == ["Jane"]
    assert names(select_contacts(conn, Filters(company="globex"))) == ["John"]
    assert names(select_contacts(conn, Filters(connected_before="2020-01-01"))) == ["Ann"]


def test_limit_and_newest_first_order(conn):
    rows = select_contacts(conn, Filters(limit=2))
    assert [r["first_name"] for r in rows] == ["Jane", "John"]


def test_render_uses_values_and_fallbacks(conn):
    rows = {r["first_name"]: r for r in select_contacts(conn, Filters())}
    template = "Hi {first_name}, how is {company|your business}? ({full_name})"
    assert templates.render(template, rows["Jane"]) == "Hi Jane, how is Acme? (Jane Doe)"
    assert templates.render(template, rows["Ann"]) == "Hi Ann, how is your business? (Ann Lee)"


def test_unknown_placeholders_are_rejected():
    with pytest.raises(templates.TemplateError, match="frist_name"):
        templates.check("Hi {frist_name}")
