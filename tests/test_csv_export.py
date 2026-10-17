import zipfile
from pathlib import Path

import pytest

from netreach.sources.csv_export import parse_date, read_connections

FIXTURE = Path(__file__).parent / "fixtures" / "Connections.csv"


def test_reads_rows_after_notes_preamble():
    contacts = list(read_connections(FIXTURE))
    assert [c.first_name for c in contacts] == ["Jane", "John", "José"]
    jane = contacts[0]
    assert jane.public_id == "jane-doe-123"
    assert jane.email == "jane@example.com"
    assert jane.company == "Acme Corp"
    assert jane.position == "Founder & CEO"
    assert jane.connected_on == "2024-03-15"
    assert jane.source == "csv"
    assert contacts[1].position == "VP, Sales"


def test_decodes_url_encoded_public_ids():
    jose = list(read_connections(FIXTURE))[2]
    assert jose.public_id == "josé-garcía"
    assert jose.key == "in:josé-garcía"


def test_reads_connections_from_full_export_zip(tmp_path):
    archive = tmp_path / "Basic_LinkedInDataExport.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.write(FIXTURE, "Connections.csv")
        z.writestr("Profile.csv", "First Name,Last Name\nMe,Myself\n")
    assert len(list(read_connections(archive))) == 3


def test_rejects_files_that_are_not_connection_exports(tmp_path):
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError):
        list(read_connections(other))


@pytest.mark.parametrize(
    "raw, expected",
    [("15 Mar 2024", "2024-03-15"), ("15-Mar-24", "2024-03-15"), ("2024-03-15", "2024-03-15"), ("", ""), ("soon", "")],
)
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected
