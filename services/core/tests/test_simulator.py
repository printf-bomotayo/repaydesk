"""The stand-in messaging provider behaves as the gateway expects a real one to."""

import pytest
from fastapi.testclient import TestClient

from repaydesk.simulators import messaging

AUTH = {"Authorization": "Bearer dev-only-messaging-token"}


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.delenv("MESSAGING_TOKEN", raising=False)
    monkeypatch.setenv("SIM_UNREACHABLE", "07005999999")
    messaging.reset()
    return TestClient(messaging.app)


def send(sim, to="07005123456", ref="CM-1", body="Good morning Zainab", channel="sms", headers=AUTH):
    return sim.post("/v1/messages", json={"channel": channel, "to": to, "body": body, "client_ref": ref},
                    headers=headers)


def test_a_message_is_accepted_once_however_often_it_is_sent(sim):
    first = send(sim)
    assert first.status_code == 201 and first.json()["id"] == "SIM-000001" and not first.json()["duplicate"]
    again = send(sim)
    assert again.status_code == 200 and again.json()["id"] == "SIM-000001" and again.json()["duplicate"]
    assert send(sim, ref="CM-2", channel="whatsapp").json()["id"] == "SIM-000002"
    inbox = sim.get("/v1/messages", params={"to": "07005123456"}, headers=AUTH).json()["items"]
    assert [m["client_ref"] for m in inbox] == ["CM-2", "CM-1"]


def test_it_refuses_what_a_provider_would(sim):
    assert send(sim, headers={}).status_code == 401
    assert send(sim, headers={"Authorization": "Bearer wrong"}).status_code == 401
    bad = send(sim, to="12345")
    assert bad.status_code == 422 and bad.json()["error"] == "invalid_number"
    assert send(sim, to="07005999999").json()["error"] == "unreachable"
    assert send(sim, body="x" * 1601).json()["error"] == "too_long"
    assert send(sim, channel="pigeon").status_code == 422
    assert sim.get("/v1/messages", headers=AUTH).json()["items"] == []


def test_the_phones_page_shows_each_inbox_escaped(sim):
    send(sim, body="Pay <b>now</b> & thanks")
    page = sim.get("/").text
    assert "Prototype" in page and "07005123456" in page
    assert "Pay &lt;b&gt;now&lt;/b&gt; &amp; thanks" in page and "<b>now</b>" not in page
