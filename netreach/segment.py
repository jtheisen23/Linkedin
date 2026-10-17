"""Filters for picking a slice of your network."""

from dataclasses import dataclass

ORDERS = {
    "recent": "c.connected_on DESC, c.last_name COLLATE NOCASE, c.first_name COLLATE NOCASE",
    "name": "c.first_name COLLATE NOCASE, c.last_name COLLATE NOCASE",
    "company": "c.company = '', c.company COLLATE NOCASE, c.first_name COLLATE NOCASE",
}


@dataclass
class Filters:
    """Comma-separated values in a text filter mean "any of", e.g. title="founder,ceo"."""

    keyword: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    tag: str = ""
    untagged: bool = False
    connected_after: str = ""
    connected_before: str = ""
    has_email: bool = False
    limit: int | None = None
    offset: int = 0


def _terms(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def _any_like(columns: tuple[str, ...], raw: str) -> tuple[str, list]:
    clauses, params = [], []
    for term in _terms(raw):
        for column in columns:
            clauses.append(f"c.{column} LIKE ?")
            params.append(f"%{term}%")
    return "(" + " OR ".join(clauses) + ")", params


def where_clause(filters: Filters) -> tuple[str, list]:
    """Build a SQL WHERE clause (over the `contacts c` alias) and its parameters."""
    clauses, params = [], []

    def add(sql_and_params):
        sql, p = sql_and_params
        if p:
            clauses.append(sql)
            params.extend(p)

    if filters.keyword:
        add(_any_like(("first_name", "last_name", "headline", "company", "position", "location", "notes"),
                      filters.keyword))
    if filters.title:
        add(_any_like(("position", "headline"), filters.title))
    if filters.company:
        add(_any_like(("company", "headline"), filters.company))
    if filters.location:
        add(_any_like(("location",), filters.location))
    if tags := _terms(filters.tag):
        clauses.append(
            f"""c.id IN (SELECT ct.contact_id FROM contact_tags ct JOIN tags t ON t.id = ct.tag_id
                         WHERE t.name IN ({", ".join("?" * len(tags))}))"""
        )
        params.extend(tags)
    if filters.untagged:
        clauses.append("c.id NOT IN (SELECT contact_id FROM contact_tags)")
    if filters.connected_after:
        clauses.append("c.connected_on != '' AND c.connected_on >= ?")
        params.append(filters.connected_after)
    if filters.connected_before:
        clauses.append("c.connected_on != '' AND c.connected_on <= ?")
        params.append(filters.connected_before)
    if filters.has_email:
        clauses.append("c.email != ''")

    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def select_contacts(conn, filters: Filters, order: str = "recent"):
    where, params = where_clause(filters)
    sql = f"SELECT c.* FROM contacts c{where} ORDER BY {ORDERS[order]}"
    if filters.limit:
        sql += " LIMIT ? OFFSET ?"
        params.extend((filters.limit, filters.offset))
    return conn.execute(sql, params).fetchall()


def count_contacts(conn, filters: Filters) -> int:
    where, params = where_clause(filters)
    return conn.execute(f"SELECT COUNT(*) FROM contacts c{where}", params).fetchone()[0]


def contact_ids(conn, filters: Filters) -> list[int]:
    where, params = where_clause(filters)
    return [row[0] for row in conn.execute(f"SELECT c.id FROM contacts c{where}", params)]
