import pytest

from netreach.sources import linkedin_sync as ls


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, str):
            raise ValueError("not JSON")
        return self.payload


class FakeApi:
    """Stands in for linkedin_api.Linkedin; records every request it's asked to make."""

    def __init__(self, pages=None, search_results=None, status_code=200):
        self.pages = list(pages or [])
        self.search_results = search_results or []
        self.status_code = status_code
        self.calls = []

    def _fetch(self, path, params=None):
        self.calls.append((path, params))
        if path == "/me":
            return FakeResponse({"miniProfile": {"firstName": "Pat", "lastName": "Lee"}}, self.status_code)
        payload = self.pages.pop(0) if self.pages else {"elements": []}
        return FakeResponse(payload, self.status_code)

    def search(self, params):
        self.calls.append(("search", params))
        return self.search_results


def inline_element(n):
    return {
        "connectedMember": f"urn:li:fsd_profile:ACoAA{n}",
        "createdAt": 1710460800000,  # 2024-03-15
        "connectedMemberResolutionResult": {
            "firstName": f"First{n}",
            "lastName": f"Last{n}",
            "headline": f"Role {n}",
            "publicIdentifier": f"person-{n}",
            "entityUrn": f"urn:li:fsd_profile:ACoAA{n}",
        },
    }


def no_sleep(_):
    pass


def test_parses_inline_connections_page():
    contacts, raw = ls.parse_connections_page({"elements": [inline_element(1)]})
    assert raw == 1
    c = contacts[0]
    assert (c.first_name, c.last_name, c.public_id, c.urn_id) == ("First1", "Last1", "person-1", "ACoAA1")
    assert c.headline == "Role 1"
    assert c.connected_on == "2024-03-15"
    assert c.source == "linkedin"


def test_parses_normalized_connections_page():
    data = {
        "data": {"*elements": ["urn:li:fsd_connection:1"]},
        "included": [
            {"entityUrn": "urn:li:fsd_connection:1", "*connectedMember": "urn:li:fsd_profile:ACoAA9",
             "createdAt": 1710460800000},
            {"entityUrn": "urn:li:fsd_profile:ACoAA9", "firstName": "Nora", "lastName": "N",
             "headline": "CTO", "publicIdentifier": "nora"},
        ],
    }
    contacts, raw = ls.parse_connections_page(data)
    assert raw == 1
    assert contacts[0].public_id == "nora"
    assert contacts[0].urn_id == "ACoAA9"


def test_unreadable_pages_raise_unexpected_response():
    with pytest.raises(ls.UnexpectedResponse):
        ls.parse_connections_page({"something": "else"})
    with pytest.raises(ls.UnexpectedResponse):
        ls.parse_connections_page({"elements": [{"connectedMember": "urn:li:fsd_profile:X"}]})


def test_paginates_until_a_short_page_and_waits_between_pages():
    full = {"elements": [inline_element(i) for i in range(ls.PAGE_SIZE)]}
    short = {"elements": [inline_element(100)]}
    api = FakeApi(pages=[full, short])
    sleeps = []
    contacts = list(ls.fetch_connections(api, delay=1, sleep=sleeps.append))
    assert len(contacts) == ls.PAGE_SIZE + 1
    assert [params["start"] for _, params in api.calls] == [0, ls.PAGE_SIZE]
    assert len(sleeps) == 1 and 1 <= sleeps[0] <= 2


def test_respects_max_pages():
    full = {"elements": [inline_element(i) for i in range(ls.PAGE_SIZE)]}
    api = FakeApi(pages=[full, full, full])
    assert len(list(ls.fetch_connections(api, max_pages=2, sleep=no_sleep))) == 2 * ls.PAGE_SIZE
    assert len(api.calls) == 2


def test_auto_falls_back_to_search_when_first_page_is_unreadable():
    result = {
        "title": {"text": "Sam Rivera"},
        "primarySubtitle": {"text": "Head of Growth at Initech"},
        "secondarySubtitle": {"text": "Denver"},
        "navigationUrl": "https://www.linkedin.com/in/sam-rivera?miniProfileUrn=x",
        "entityUrn": "urn:li:fsd_entityResultViewModel:(urn:li:fsd_profile:ACoAA7,SEARCH_SRP,DEFAULT)",
    }
    private = {"title": {"text": "LinkedIn Member"}, "navigationUrl": "https://www.linkedin.com/search/x"}
    api = FakeApi(pages=[{"unexpected": True}], search_results=[result, private])
    logs = []
    contacts = list(ls.iter_connections(api, sleep=no_sleep, log=logs.append))
    assert [(c.first_name, c.last_name, c.public_id, c.urn_id, c.location) for c in contacts] == [
        ("Sam", "Rivera", "sam-rivera", "ACoAA7", "Denver")
    ]
    assert "falling back" in logs[0]


def test_connections_method_does_not_fall_back():
    api = FakeApi(pages=[{"unexpected": True}])
    with pytest.raises(ls.UnexpectedResponse):
        list(ls.iter_connections(api, method="connections", sleep=no_sleep))
    assert not any(path == "search" for path, _ in api.calls)


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_errors_stop_the_sync(status):
    api = FakeApi(status_code=status)
    with pytest.raises(ls.SyncError) as exc:
        list(ls.iter_connections(api, sleep=no_sleep))
    assert not isinstance(exc.value, ls.UnexpectedResponse)  # no fallback on auth/rate errors
    assert not any(path == "search" for path, _ in api.calls)


def test_html_response_means_logged_out():
    api = FakeApi(pages=["<html>login</html>"])
    with pytest.raises(ls.SyncError, match="fresh cookies"):
        list(ls.fetch_connections(api, sleep=no_sleep))


def test_whoami():
    assert ls.whoami(FakeApi()) == "Pat Lee"


def test_make_client_requires_cookies():
    with pytest.raises(ls.SyncError, match="LINKEDIN_LI_AT"):
        ls.make_client("", "")


def test_make_client_sets_session_cookies_and_user_agent():
    api = ls.make_client("AQEDAT-token", '"ajax:123"', user_agent="MyBrowser/1.0")
    session = api.client.session
    assert session.cookies.get("li_at") == "AQEDAT-token"
    assert session.cookies.get("JSESSIONID") == '"ajax:123"'
    assert session.headers["csrf-token"] == "ajax:123"
    assert session.headers["user-agent"] == "MyBrowser/1.0"


def test_fetch_contact_info():
    class Api:
        def get_profile_contact_info(self, public_id):
            return {"email_address": "x@example.com", "phone_numbers": [{"number": "555", "type": "MOBILE"}]}

    assert ls.fetch_contact_info(Api(), "x") == ("x@example.com", "555")
