"""Recipient resolution: groups, mishearings and ambiguity.

The bug these cover: `whatsapp.py search_contacts` filters groups out in SQL
(`AND jid NOT LIKE '%@g.us'`), so before this, a group name could never resolve
at all — "send it to burgito" failed regardless of how it was spelled.
"""

import pytest

from core.self_extend import whatsapp_mcp


@pytest.fixture(autouse=True)
def no_live_address_book(monkeypatch):
    """Keep these tests off the real device address book.

    `_candidate_pool` consults whatsmeow_contacts (~2400 real names), so
    without this a genuine contact can outrank the fixture's chats and the
    result depends on whose phone the suite runs on.
    """
    monkeypatch.setattr(
        "core.self_extend.whatsapp_contacts.resolve_name",
        lambda *a, **k: [])


class FakeClient:
    """Stands in for the MCP session; records which lookups were attempted."""

    def __init__(self, contacts=None, chats=None):
        self.contacts = contacts or []
        self.chats = chats or []
        self.calls = []

    def call_tool(self, name, args=None, timeout=None):
        args = args or {}
        self.calls.append((name, args))
        if name == "search_contacts":
            q = str(args.get("query") or "").casefold()
            # Mirrors the real SQL: substring LIKE, groups excluded.
            return [c for c in self.contacts if q in str(c["name"]).casefold()]
        if name == "list_chats":
            q = args.get("query")
            limit = int(args.get("limit") or 20)
            if not q:
                return self.chats[:limit]
            q = str(q).casefold()
            return [c for c in self.chats
                    if q in str(c["name"]).casefold()][:limit]
        raise AssertionError(f"unexpected tool {name}")


BURGITO = {"jid": "120363000000000002@g.us", "name": "Burgito"}
BHARAT = {"jid": "919876543210@s.whatsapp.net", "name": "Bharat"}
MOM = {"jid": "919812345678@s.whatsapp.net", "name": "Mom"}


def resolve(client, name):
    return whatsapp_mcp._resolve_recipient(client, name, timeout=5)


# --- the reported failure --------------------------------------------------

def test_misheard_group_name_resolves_to_the_real_group():
    """'burrito time' must reach the group 'Burgito'.

    search_contacts cannot see groups and no LIKE query matches the typo, so
    this only works via the recent-chats sweep plus fuzzy scoring.
    """
    client = FakeClient(contacts=[BHARAT, MOM], chats=[BURGITO, BHARAT, MOM])
    jid, error = resolve(client, "burrito time")
    assert error is None
    assert jid == BURGITO["jid"]


def test_group_is_unreachable_through_contacts_alone():
    """Guards the actual root cause: contacts-only lookup finds nothing."""
    client = FakeClient(contacts=[BHARAT, MOM], chats=[BURGITO])
    assert client.call_tool("search_contacts", {"query": "Burgito"}) == []
    jid, error = resolve(client, "Burgito")
    assert error is None and jid == BURGITO["jid"]


@pytest.mark.parametrize("spoken", [
    "burgito", "Burgito", "burgito time", "burrito", "bergito",
])
def test_common_mishearings_all_land_on_the_group(spoken):
    client = FakeClient(contacts=[BHARAT, MOM], chats=[BURGITO, BHARAT, MOM])
    jid, error = resolve(client, spoken)
    assert error is None, f"{spoken!r} failed: {error}"
    assert jid == BURGITO["jid"]


# --- safety: never message the wrong person --------------------------------

def test_two_equally_close_names_ask_instead_of_guessing():
    a = {"jid": "111@g.us", "name": "Burgito"}
    b = {"jid": "222@g.us", "name": "Burgita"}
    client = FakeClient(chats=[a, b])
    jid, error = resolve(client, "burgito")
    # 'Burgito' is an exact match for a, so that one is allowed through.
    assert jid == a["jid"] and error is None

    # But a name equidistant from both must not silently pick one.
    jid, error = resolve(client, "burgitx")
    assert jid is None
    assert "ambiguous" in error["error"].lower()
    assert {m["name"] for m in error["matches"]} == {"Burgito", "Burgita"}


def test_unrelated_name_is_rejected_not_forced_onto_a_chat():
    client = FakeClient(contacts=[BHARAT, MOM], chats=[BURGITO, BHARAT, MOM])
    jid, error = resolve(client, "zxqwvplk")
    assert jid is None
    assert "matched" in error["error"]


def test_empty_recipient_is_rejected():
    jid, error = resolve(FakeClient(), "   ")
    assert jid is None and "required" in error["error"]


# --- pass-through paths ----------------------------------------------------

def test_phone_number_and_jid_bypass_lookup_entirely():
    client = FakeClient()
    assert resolve(client, "+91 98765-43210") == ("919876543210", None)
    assert resolve(client, "120363000000000002@g.us") == (
        "120363000000000002@g.us", None)
    assert client.calls == [], "a literal recipient must cost no MCP calls"


def test_exact_contact_name_still_wins_over_a_similar_group():
    client = FakeClient(contacts=[BHARAT], chats=[BHARAT,
                                                  {"jid": "9@g.us", "name": "Bharat Fans"}])
    jid, error = resolve(client, "Bharat")
    assert error is None and jid == BHARAT["jid"]


# --- scoring unit ----------------------------------------------------------

def test_score_ranks_the_intended_group_above_the_alternatives():
    score = whatsapp_mcp._name_score
    assert score("burrito time", "Burgito") > score("burrito time", "Bharat")
    assert score("burrito time", "Burgito") > score("burrito time", "Mom")
    assert score("Burgito", "Burgito") == 1.0


def test_score_ignores_filler_words():
    score = whatsapp_mcp._name_score
    assert score("my burgito group", "Burgito") >= whatsapp_mcp._FUZZY_ACCEPT


def test_sender_ids_are_labelled_with_names(monkeypatch):
    """Un-enriched, a chat summary is a list of phone numbers and the model
    cannot tell who said what."""
    monkeypatch.setattr(
        "core.self_extend.whatsapp_contacts.display_name",
        lambda ident: {"20000000000031": "Nikhil"}.get(str(ident).split("@")[0]))

    payload = [
        {"sender": "20000000000031", "content": "2:30 aa jana", "is_from_me": 0},
        {"sender": "919900000016", "content": "ok", "is_from_me": 1},
        {"sender": "999999999999", "content": "hm", "is_from_me": 0},
    ]
    out = whatsapp_mcp._label_people(payload)
    assert out[0]["sender_name"] == "Nikhil"
    assert out[0]["sender"] == "20000000000031", "raw id must be preserved"
    assert out[1]["sender_name"] == "Vaibhav", "own messages should be attributed"
    assert "sender_name" not in out[2], "unknown senders stay unlabelled"


def test_a_numeric_dm_chat_name_is_replaced_with_the_contact(monkeypatch):
    monkeypatch.setattr(
        "core.self_extend.whatsapp_contacts.display_name",
        lambda ident: "Namita" if "919900000011" in str(ident) else None)
    out = whatsapp_mcp._label_people(
        [{"jid": "919900000011@s.whatsapp.net", "name": "919900000011"}])
    assert out[0]["name"] == "Namita"
    assert out[0]["number"] == "919900000011"


def test_group_names_are_never_overwritten(monkeypatch):
    monkeypatch.setattr("core.self_extend.whatsapp_contacts.display_name",
                        lambda ident: "SHOULD NOT APPEAR")
    out = whatsapp_mcp._label_people(
        [{"jid": "120363000000000001@g.us", "name": "burgito time"}])
    assert out[0]["name"] == "burgito time"


def test_labelling_never_breaks_the_payload(monkeypatch):
    def boom(_):
        raise RuntimeError("address book on fire")

    monkeypatch.setattr("core.self_extend.whatsapp_contacts.display_name", boom)
    payload = [{"sender": "1", "content": "hi"}]
    assert whatsapp_mcp._label_people(payload) == payload


def test_broad_sweep_only_runs_when_targeted_lookups_fail():
    """The 80-chat sweep is the expensive path — it must stay a last resort."""
    client = FakeClient(contacts=[BHARAT], chats=[BHARAT])
    resolve(client, "Bharat")
    sweeps = [a for n, a in client.calls
              if n == "list_chats" and not a.get("query")]
    assert sweeps == []
