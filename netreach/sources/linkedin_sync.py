"""Live, read-only sync of your 1st-degree connections via the unofficial `linkedin-api`.

This uses session cookies copied from your browser to call the same internal API
that linkedin.com's web app uses. LinkedIn doesn't permit this, so the sync only
reads, pauses between requests, and caps how much it fetches per run. Run it from
your own computer and home connection, not from a server, VPN or cloud machine.
"""

import random
import re
import time
from datetime import datetime, timezone

import requests

from netreach.models import Contact, public_id_from_url

SOURCE = "linkedin"
CONNECTIONS_PATH = "/relationships/dash/connections"
CONNECTIONS_DECORATION = "com.linkedin.voyager.dash.deco.web.mynetwork.ConnectionListWithProfile-16"
PAGE_SIZE = 40
FIRST_DEGREE_SEARCH = {
    "filters": "List((key:resultType,value:List(PEOPLE)),(key:network,value:List(F)))"
}
_PROFILE_URN_RE = re.compile(r"urn:li:fsd_profile:([A-Za-z0-9_-]+)")


class SyncError(RuntimeError):
    """LinkedIn refused or failed the request; stop rather than retry."""


class UnexpectedResponse(SyncError):
    """LinkedIn answered, but not in the shape this code knows how to read."""


def make_client(li_at: str, jsessionid: str, user_agent: str = ""):
    """Build a `linkedin_api.Linkedin` client from browser session cookies."""
    from linkedin_api import Linkedin
    from requests.cookies import RequestsCookieJar

    li_at = (li_at or "").strip().strip('"')
    jsessionid = (jsessionid or "").strip().strip('"')
    if not li_at or not jsessionid:
        raise SyncError("Set LINKEDIN_LI_AT and LINKEDIN_JSESSIONID first (see .env.example).")

    jar = RequestsCookieJar()
    jar.set("li_at", li_at, domain=".linkedin.com", path="/")
    jar.set("JSESSIONID", f'"{jsessionid}"', domain=".www.linkedin.com", path="/")
    api = Linkedin("", "", cookies=jar)
    if user_agent:
        # Matching the browser the cookies came from looks less like a new device.
        api.client.session.headers["user-agent"] = user_agent
    return api


def _get_json(api, path: str, **params):
    try:
        res = api._fetch(path, params=params or None)
    except requests.RequestException as exc:
        raise SyncError(f"Network error talking to LinkedIn: {exc}") from None
    if res.status_code in (401, 403):
        raise SyncError(
            f"LinkedIn rejected the session (HTTP {res.status_code}). Log in to linkedin.com "
            "in your browser and copy fresh cookies."
        )
    if res.status_code == 429:
        raise SyncError("LinkedIn is rate limiting this account (HTTP 429). Stop for today.")
    if res.status_code >= 400:
        raise SyncError(f"LinkedIn returned HTTP {res.status_code} for {path}.")
    try:
        return res.json()
    except ValueError:
        raise SyncError(
            "LinkedIn didn't return JSON, so the session was probably logged out or hit a "
            "security check. Open linkedin.com in your browser, clear any prompt, and copy "
            "fresh cookies."
        ) from None


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    return value.strip() if isinstance(value, str) else ""


def _urn_id(urn: str) -> str:
    match = _PROFILE_URN_RE.search(urn or "")
    return match.group(1) if match else ""


def _ms_to_date(ms) -> str:
    if not isinstance(ms, (int, float)):
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def whoami(api) -> str:
    """Return the logged-in member's name; raises SyncError if the session is dead."""
    mini = _get_json(api, "/me").get("miniProfile") or {}
    return f"{_text(mini.get('firstName'))} {_text(mini.get('lastName'))}".strip()


def parse_connections_page(data: dict) -> tuple[list[Contact], int]:
    """Read one page of the connections endpoint. Returns (contacts, raw element count).

    Handles both the inline ("decorated") response and LinkedIn's normalized
    response, where profiles live in a separate `included` list.
    """
    if "included" in data:
        by_urn = {item.get("entityUrn"): item for item in data.get("included") or []}
        elements = [by_urn.get(urn, {}) for urn in (data.get("data") or {}).get("*elements", [])]

        def profile_of(element):
            return by_urn.get(element.get("*connectedMember") or element.get("connectedMember"))
    elif "elements" in data:
        elements = data.get("elements") or []

        def profile_of(element):
            return element.get("connectedMemberResolutionResult")
    else:
        raise UnexpectedResponse("no connection list in the response")

    contacts = []
    for element in elements:
        profile = profile_of(element) or {}
        first, last = _text(profile.get("firstName")), _text(profile.get("lastName"))
        if not (first or last):
            continue
        contacts.append(
            Contact(
                first_name=first,
                last_name=last,
                headline=_text(profile.get("headline")),
                public_id=_text(profile.get("publicIdentifier")),
                urn_id=_urn_id(profile.get("entityUrn") or element.get("connectedMember") or ""),
                connected_on=_ms_to_date(element.get("createdAt")),
                source=SOURCE,
            )
        )
    if elements and not contacts:
        raise UnexpectedResponse("connections came back without readable profiles")
    return contacts, len(elements)


def fetch_connections(api, max_pages: int = 100, delay: float = 3.0, sleep=time.sleep):
    """Yield your connections, newest first, from the "My Network" connections list."""
    for page in range(max_pages):
        data = _get_json(
            api,
            CONNECTIONS_PATH,
            decorationId=CONNECTIONS_DECORATION,
            count=PAGE_SIZE,
            q="search",
            sortType="RECENTLY_ADDED",
            start=page * PAGE_SIZE,
        )
        contacts, raw_count = parse_connections_page(data)
        yield from contacts
        if raw_count < PAGE_SIZE:
            return
        sleep(delay + random.uniform(0, delay))


def parse_search_result(item: dict) -> Contact | None:
    name = _text(item.get("title"))
    public_id = public_id_from_url(item.get("navigationUrl") or "")
    urn_id = _urn_id(item.get("entityUrn") or "")
    if not name or name == "LinkedIn Member" or not (public_id or urn_id):
        return None
    first, _, last = name.partition(" ")
    return Contact(
        first_name=first,
        last_name=last,
        public_id=public_id,
        urn_id=urn_id,
        headline=_text(item.get("primarySubtitle")),
        location=_text(item.get("secondarySubtitle")),
        source=SOURCE,
    )


def fetch_connections_via_search(api):
    """Fallback: people search filtered to 1st degree. LinkedIn caps this near 1,000 results."""
    try:
        results = api.search(dict(FIRST_DEGREE_SEARCH))
    except requests.RequestException as exc:
        raise SyncError(f"Network error talking to LinkedIn: {exc}") from None
    except ValueError:
        raise SyncError("LinkedIn search didn't return JSON; copy fresh cookies.") from None
    for item in results:
        contact = parse_search_result(item)
        if contact:
            yield contact


def iter_connections(api, method: str = "auto", max_pages: int = 100, delay: float = 3.0,
                     sleep=time.sleep, log=print):
    """Yield connections using `method`: "connections", "search", or "auto".

    "auto" tries the connections list and falls back to search only if the very
    first page comes back in a shape this code can't read.
    """
    if method in ("auto", "connections"):
        yielded = 0
        try:
            for contact in fetch_connections(api, max_pages=max_pages, delay=delay, sleep=sleep):
                yielded += 1
                yield contact
            return
        except UnexpectedResponse as exc:
            if method == "connections" or yielded:
                raise
            log(f"Connections list didn't work ({exc}); falling back to people search, "
                "which LinkedIn caps at about 1,000 results.")
    yield from fetch_connections_via_search(api)


def fetch_contact_info(api, public_id: str) -> tuple[str, str]:
    """Return (email, phone) a connection has chosen to share with you."""
    try:
        info = api.get_profile_contact_info(public_id=public_id)
    except requests.RequestException as exc:
        raise SyncError(f"Network error talking to LinkedIn: {exc}") from None
    except (ValueError, KeyError, TypeError) as exc:
        raise SyncError(f"Couldn't read contact info for {public_id}: {exc!r}") from None
    phones = info.get("phone_numbers") or []
    phone = (phones[0].get("number") or "") if phones else ""
    return info.get("email_address") or "", phone
