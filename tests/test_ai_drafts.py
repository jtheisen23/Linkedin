import json
from datetime import date
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from netreach import ai_drafts


def block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


def response(*content, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=list(content))


def draft_json(message="Hi Jane, quick idea for Acme.", relevance="high", reason="Runs ops at a 50-person company."):
    return json.dumps({"message": message, "relevance": relevance, "reason": reason})


class FakeClient:
    """Mimics anthropic.Anthropic().beta.messages.create and records each request."""

    def __init__(self, result):
        self.result = result
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result(request) if callable(self.result) else self.result


CONTACT = {
    "first_name": "Jane", "last_name": "Doe", "headline": "COO at Acme | ex-McKinsey",
    "company": "Acme", "position": "COO", "location": "", "connected_on": "2024-03-15",
    "email": "jane@example.com", "phone": "+1 555 0100",
}


def api_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(status, request=request), body=None)


def test_request_shape():
    client = FakeClient(response(block("text", text=draft_json())))
    ai_drafts.write_draft(client, "Selling ops dashboards.", CONTACT, effort="low", today=date(2026, 10, 17))
    request = client.requests[0]

    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]
    assert request["output_config"]["effort"] == "low"
    assert request["output_config"]["format"]["schema"] == ai_drafts.DRAFT_SCHEMA
    assert "thinking" not in request  # always adaptive on this model; effort is the control
    assert "<campaign_brief>\nSelling ops dashboards.\n</campaign_brief>" in request["system"][1]["text"]
    assert request["system"][1]["cache_control"] == {"type": "ephemeral"}

    user = request["messages"][0]["content"]
    assert "Today is 2026-10-17" in user
    assert '"position": "COO"' in user and '"connected_on": "2024-03-15"' in user
    assert "jane@example.com" not in user and "555" not in user  # never sent
    assert '"location"' not in user  # blank fields are left out


def test_parses_draft():
    client = FakeClient(response(block("thinking", thinking=""), block("text", text=draft_json())))
    draft = ai_drafts.write_draft(client, "brief", CONTACT)
    assert draft == ai_drafts.Draft("Hi Jane, quick idea for Acme.", "high", "Runs ops at a 50-person company.")


def test_discards_partial_output_from_a_model_that_declined():
    client = FakeClient(response(
        block("text", text='{"message": "Hi Ja'),
        block("fallback"),
        block("text", text=draft_json(message="From the fallback model")),
    ))
    assert ai_drafts.write_draft(client, "brief", CONTACT).message == "From the fallback model"


@pytest.mark.parametrize("bad", [
    response(stop_reason="refusal"),
    response(block("text", text='{"message": "cut off'), stop_reason="max_tokens"),
    response(block("text", text="not json")),
    response(block("text", text='["a list"]')),
    response(block("text", text=draft_json(relevance="urgent"))),
    response(block("text", text=draft_json(message="  "))),
])
def test_bad_responses_skip_just_that_contact(bad):
    with pytest.raises(ai_drafts.DraftError):
        ai_drafts.write_draft(FakeClient(bad), "brief", CONTACT)


@pytest.mark.parametrize("error, fatal", [
    (api_error(anthropic.AuthenticationError, 401), True),
    (api_error(anthropic.NotFoundError, 404), True),
    (api_error(anthropic.BadRequestError, 400), True),
    (api_error(anthropic.RateLimitError, 429), False),
    (api_error(anthropic.InternalServerError, 500), False),
])
def test_api_errors(error, fatal):
    expected = ai_drafts.FatalDraftError if fatal else ai_drafts.DraftError
    with pytest.raises(expected):
        ai_drafts.write_draft(FakeClient(error), "brief", CONTACT)


def test_clean_brief_strips_comments():
    assert ai_drafts.clean_brief("<!-- edit me -->\nAbout me: Alex\n<!--\nnote\n-->") == "About me: Alex"


def test_real_sdk_request_and_response_round_trip():
    """Runs the real SDK against a mock transport: checks the wire format, not just our fakes."""
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-4-8",
            "content": [
                {"type": "text", "text": '{"message": "partial'},
                {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"},
                 "trigger": {"type": "refusal"}},
                {"type": "text", "text": draft_json()},
            ],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        })

    client = anthropic.Anthropic(
        api_key="test-key",
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    draft = ai_drafts.write_draft(client, "brief", CONTACT)

    assert draft.relevance == "high"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    body = seen["body"]
    assert body["fallbacks"] == "default"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "betas" not in body  # sent as a header, not in the body


def test_missing_credentials_is_a_setup_error(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent")  # no `ant auth login` profile either
    client = anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(
        transport=httpx2.MockTransport(lambda request: httpx2.Response(500))))
    with pytest.raises(ai_drafts.FatalDraftError, match="ANTHROPIC_API_KEY"):
        ai_drafts.write_draft(client, "brief", CONTACT)
