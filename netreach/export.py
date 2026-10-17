"""CSV export of contacts, shared by the CLI and the web page."""

import csv

from netreach import db

EXPORT_COLUMNS = (
    "first_name", "last_name", "email", "phone", "company", "position", "headline",
    "location", "profile_url", "connected_on", "tags", "notes", "sources",
)


def write_contacts_csv(conn, rows, f) -> None:
    tags = db.tags_for(conn, [row["id"] for row in rows])
    writer = csv.writer(f)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            ", ".join(tags[row["id"]]) if column == "tags" else row[column] for column in EXPORT_COLUMNS
        )
