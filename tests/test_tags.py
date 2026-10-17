import pytest

from netreach import db
from netreach.models import Contact
from netreach.segment import Filters, count_contacts, select_contacts


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.import_contacts(c, [
        Contact(first_name="Jane", public_id="jane", company="Acme"),
        Contact(first_name="John", public_id="john", company="Globex"),
        Contact(first_name="Ann", public_id="ann", company="Initech"),
    ])
    yield c
    c.close()


def ids(conn, *names):
    return [conn.execute("SELECT id FROM contacts WHERE first_name = ?", (n,)).fetchone()[0] for n in names]


def names(rows):
    return sorted(r["first_name"] for r in rows)


def test_add_and_remove_tags_in_bulk(conn):
    db.change_tags(conn, ids(conn, "Jane", "John"), add=["Warm lead", "  investor  "])
    assert names(select_contacts(conn, Filters(tag="warm lead"))) == ["Jane", "John"]  # case-insensitive
    assert db.list_tags(conn) == [{"name": "investor", "count": 2}, {"name": "Warm lead", "count": 2}]

    db.change_tags(conn, ids(conn, "John"), remove=["WARM LEAD"])
    assert names(select_contacts(conn, Filters(tag="Warm lead"))) == ["Jane"]
    assert names(select_contacts(conn, Filters(untagged=True))) == ["Ann"]


def test_tag_filter_means_any_of(conn):
    db.change_tags(conn, ids(conn, "Jane"), add=["a"])
    db.change_tags(conn, ids(conn, "John"), add=["b"])
    assert names(select_contacts(conn, Filters(tag="a,b"))) == ["Jane", "John"]


def test_adding_an_existing_tag_twice_is_harmless(conn):
    db.change_tags(conn, ids(conn, "Jane"), add=["vip"])
    db.change_tags(conn, ids(conn, "Jane"), add=["VIP"])
    assert db.tags_for(conn, ids(conn, "Jane")) == {ids(conn, "Jane")[0]: ["vip"]}


@pytest.mark.parametrize("bad", ["", "   ", "a,b", "x" * 41])
def test_invalid_tag_names(conn, bad):
    with pytest.raises(ValueError):
        db.change_tags(conn, ids(conn, "Jane"), add=[bad])


def test_rename_and_merge(conn):
    db.change_tags(conn, ids(conn, "Jane"), add=["prospect"])
    db.change_tags(conn, ids(conn, "John"), add=["lead"])
    db.rename_tag(conn, "prospect", "Prospect")  # case-only rename keeps the tag
    assert [t["name"] for t in db.list_tags(conn)] == ["lead", "Prospect"]

    db.rename_tag(conn, "Prospect", "LEAD")  # onto an existing tag: merge
    assert db.list_tags(conn) == [{"name": "lead", "count": 2}]
    with pytest.raises(KeyError):
        db.rename_tag(conn, "nope", "x")


def test_delete_tag_removes_it_from_everyone(conn):
    db.change_tags(conn, ids(conn, "Jane", "John"), add=["temp"])
    db.delete_tag(conn, "temp")
    assert db.list_tags(conn) == []
    assert count_contacts(conn, Filters(untagged=True)) == 3


def test_notes_are_searchable_and_survive_reimport(conn):
    jane = ids(conn, "Jane")[0]
    assert db.set_notes(conn, jane, "Met at SaaStr; interested in pilots")
    assert names(select_contacts(conn, Filters(keyword="saastr"))) == ["Jane"]
    db.import_contacts(conn, [Contact(first_name="Jane", public_id="jane", company="Acme 2")])
    assert conn.execute("SELECT notes FROM contacts WHERE id = ?", (jane,)).fetchone()[0].startswith("Met at")


def test_paging_and_sorting(conn):
    first = select_contacts(conn, Filters(limit=2), order="name")
    second = select_contacts(conn, Filters(limit=2, offset=2), order="name")
    assert [r["first_name"] for r in first + second] == ["Ann", "Jane", "John"]
    assert count_contacts(conn, Filters(limit=1)) == 3
