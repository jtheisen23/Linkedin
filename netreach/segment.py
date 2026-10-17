"""Filters for picking a slice of your network."""

from dataclasses import dataclass


@dataclass
class Filters:
    """Comma-separated values in a text filter mean "any of", e.g. title="founder,ceo"."""

    keyword: str = ""
    title: str = ""
    company: str = ""
    location: str = ""
    connected_after: str = ""
    connected_before: str = ""
    has_email: bool = False
    limit: int | None = None


def _any_like(columns: tuple[str, ...], raw: str) -> tuple[str, list]:
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    clauses, params = [], []
    for term in terms:
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
        add(_any_like(("first_name", "last_name", "headline", "company", "position", "location"), filters.keyword))
    if filters.title:
        add(_any_like(("position", "headline"), filters.title))
    if filters.company:
        add(_any_like(("company", "headline"), filters.company))
    if filters.location:
        add(_any_like(("location",), filters.location))
    if filters.connected_after:
        clauses.append("c.connected_on != '' AND c.connected_on >= ?")
        params.append(filters.connected_after)
    if filters.connected_before:
        clauses.append("c.connected_on != '' AND c.connected_on <= ?")
        params.append(filters.connected_before)
    if filters.has_email:
        clauses.append("c.email != ''")

    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def select_contacts(conn, filters: Filters):
    where, params = where_clause(filters)
    sql = f"SELECT c.* FROM contacts c{where} ORDER BY c.connected_on DESC, c.last_name, c.first_name"
    if filters.limit:
        sql += " LIMIT ?"
        params.append(filters.limit)
    return conn.execute(sql, params).fetchall()
