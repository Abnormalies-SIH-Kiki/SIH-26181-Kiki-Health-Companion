"""The WhatsApp address book: names in, names out.

Context for why this exists at all — measured on the live device:
`messages.db` holds 292 direct chats and **zero** of them carry a human name.
Every DM is a bare number. So "send a message to Namita" could never resolve
and a chat summary read out as a list of phone numbers. The real names live in
the Go bridge's own whatsmeow store, which nothing used until now.

These tests build their own tiny store so they never depend on the live one.
"""

import sqlite3

import pytest

from core.self_extend import whatsapp_contacts as wc


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A miniature whatsmeow store mirroring the real schema."""
    store = tmp_path / "whatsapp.db"
    conn = sqlite3.connect(store)
    conn.execute("CREATE TABLE whatsmeow_lid_map (lid TEXT, pn TEXT)")
    conn.execute(
        "CREATE TABLE whatsmeow_contacts (our_jid TEXT, their_jid TEXT, "
        "first_name TEXT, full_name TEXT, push_name TEXT, business_name TEXT)")
    conn.executemany("INSERT INTO whatsmeow_lid_map VALUES (?,?)", [
        ("20000000000021", "15550000021"),   # Studio Website
        ("20000000000031", "919111222333"),  # a group member
        ("20000000000013", "919900000013"),   # stored twice, see below
    ])
    conn.executemany("INSERT INTO whatsmeow_contacts VALUES (?,?,?,?,?,?)", [
        ("me", "919900000011@s.whatsapp.net", "", "Namita", "Namita", None),
        ("me", "919900000012@s.whatsapp.net", "", "NamitaOffice", None, None),
        ("me", "15550000021@s.whatsapp.net", "", "Studio Website", "MG", None),
        ("me", "15550000022@s.whatsapp.net", "", "Studio Partener", None, None),
        ("me", "919111222333@s.whatsapp.net", "", "Nikhil", None, None),
        # The SAME person filed under both their lid and their phone.
        ("me", "20000000000013@lid", "", "Ravi Kumar", None, None),
        ("me", "919900000013@s.whatsapp.net", "", "Ravi Kumar", None, None),
        # Decorated push name, and a junk numeric "name".
        ("me", "919900000014@s.whatsapp.net", "", "~Deepak~", None, None),
        ("me", "911111111111@s.whatsapp.net", "", "911111111111", None, None),
        # A one-letter contact: must never win a fuzzy match against a real name.
        ("me", "919900000015@s.whatsapp.net", "", "A", None, None),
    ])
    conn.commit()
    conn.close()

    monkeypatch.setattr(wc, "_STORE", store)
    monkeypatch.setattr(wc, "_manual_contacts", dict)
    wc.reload_now()
    yield wc
    wc.reload_now()


# --- the reported failures -------------------------------------------------

def test_a_saved_contact_name_resolves_to_their_number(book):
    """'send a message to namita' — the exact case that failed."""
    matches = book.resolve_name("namita")
    assert matches, "Namita is in the address book but did not resolve"
    assert matches[0]["name"] == "Namita"
    assert matches[0]["phone"] == "919900000011"


def test_multiword_contact_name_resolves(book):
    """'tell me the chat summary for studio website'."""
    matches = book.resolve_name("studio website")
    assert matches[0]["name"] == "Studio Website"
    assert matches[0]["phone"] == "15550000021"


def test_short_junk_contact_cannot_outrank_a_real_name(book):
    """A contact literally named "A" scored 0.92 against "namita" once,
    because "a" is a substring of it."""
    names = [m["name"] for m in book.resolve_name("namita")]
    assert names[0] == "Namita"
    assert "A" not in names


def test_same_person_under_lid_and_phone_collapses_to_one(book):
    """Otherwise a unique name looks ambiguous and Kiki asks needlessly."""
    matches = book.resolve_name("ravi kumar")
    assert len(matches) == 1, f"duplicate contact not collapsed: {matches}"
    assert matches[0]["phone"] == "919900000013", "should canonicalize to the phone"


def test_genuinely_different_people_stay_separate(book):
    matches = book.resolve_name("namita", threshold=0.5)
    assert {m["name"] for m in matches} >= {"Namita", "NamitaOffice"}


def test_unknown_name_resolves_to_nothing(book):
    assert book.resolve_name("zzzznotacontact") == []


# --- reading: identifier → name --------------------------------------------

def test_message_sender_lid_becomes_a_human_name(book):
    """Message rows carry an opaque @lid; un-enriched, a summary is numbers."""
    assert book.display_name("20000000000031") == "Nikhil"
    assert book.display_name("919900000011") == "Namita"
    assert book.display_name("919900000011@s.whatsapp.net") == "Namita"


def test_unknown_identifier_degrades_to_itself(book):
    assert book.display_name("999999999999") is None
    assert book.label("999999999999") == "999999999999"


def test_decorated_push_names_are_cleaned(book):
    assert book.display_name("919900000014") == "Deepak"


def test_a_numeric_contact_name_is_not_treated_as_a_name(book):
    assert book.display_name("911111111111") is None


# --- the phone-vs-lid split ------------------------------------------------

def test_jid_variants_covers_both_storage_forms(book):
    """Measured live: 'Studio Website' resolves to 15550000021@s.whatsapp.net,
    which has 0 message rows, while its 60 real messages sit under
    20000000000021@lid. Querying only the first reports "no messages" as fact.
    """
    variants = book.jid_variants("15550000021@s.whatsapp.net")
    assert variants[0] == "15550000021@s.whatsapp.net"
    assert "20000000000021@lid" in variants

    back = book.jid_variants("20000000000021@lid")
    assert "15550000021@s.whatsapp.net" in back


def test_group_jids_have_no_alternate_form(book):
    assert book.jid_variants("120363000000000001@g.us") == ["120363000000000001@g.us"]


# --- manual overrides ------------------------------------------------------

def test_config_contacts_win_over_the_address_book(book, monkeypatch):
    monkeypatch.setattr(wc, "_manual_contacts",
                        lambda: {"Nikhil": "910000000001", "Mom": "910000000002"})
    wc.reload_now()
    top = book.resolve_name("nikhil")[0]
    assert top["source"] == "config" and top["phone"] == "910000000001"
    assert book.resolve_name("mom")[0]["name"] == "Mom"


def test_comment_keys_in_config_are_not_loaded_as_people(book, monkeypatch):
    """config.json ships `_example_mom` as documentation, not a contact."""
    monkeypatch.setattr(
        wc, "_manual_contacts",
        lambda: {k: v for k, v in
                 {"_example_mom": "919812345678", "Real": "910000000003"}.items()
                 if not k.startswith("_")})
    wc.reload_now()
    assert book.resolve_name("example") == []
    assert book.resolve_name("real")[0]["name"] == "Real"


def test_a_missing_store_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "_STORE", tmp_path / "nope.db")
    monkeypatch.setattr(wc, "_manual_contacts", lambda: {"Mom": "911111111112"})
    wc.reload_now()
    try:
        assert wc.resolve_name("mom")[0]["phone"] == "911111111112"
        assert wc.display_name("123") is None
    finally:
        wc.reload_now()


def _insert_brandnew():
    conn = sqlite3.connect(wc._STORE)
    conn.execute("INSERT INTO whatsmeow_contacts VALUES (?,?,?,?,?,?)",
                 ("me", "915555555555@s.whatsapp.net", "", "BrandNew", None, None))
    conn.commit()
    conn.close()


def test_cache_rebuilds_when_the_store_signature_moves(book, monkeypatch):
    """The bridge syncs contacts continuously; a load-once cache goes stale."""
    assert book.resolve_name("brandnew") == []
    _insert_brandnew()
    # Force the signature to differ, which is the fast path. The TTL below
    # covers the case where it does NOT.
    monkeypatch.setattr(wc, "_CACHE_SIG", ("forced", 0))
    assert book.resolve_name("brandnew")[0]["phone"] == "915555555555"


def test_a_write_invisible_to_the_signature_is_still_picked_up(book, monkeypatch):
    """MEASURED: inode mtime advances in 4 ms steps on this Pi, and a small
    INSERT reuses a free page so st_size never moves. A bridge write inside
    that tick is invisible to (mtime, size) — and stays invisible forever,
    since nothing later changes it either. Only the TTL rescues it."""
    assert book.resolve_name("brandnew") == []
    frozen = wc._store_signature()
    monkeypatch.setattr(wc, "_store_signature", lambda: frozen)
    _insert_brandnew()

    monkeypatch.setattr(wc, "_CACHE_TTL_SECONDS", 1e9)
    assert book.resolve_name("brandnew") == [], "signature genuinely unchanged"

    monkeypatch.setattr(wc, "_CACHE_TTL_SECONDS", -1.0)   # TTL expired
    assert book.resolve_name("brandnew")[0]["phone"] == "915555555555"


# --- containment must respect word boundaries ------------------------------
# Live failure, 2026-07-26: "summarize my chats by namitha" and "send a message
# to namita" both failed. Plain substring containment scored the contact "Amit"
# 0.90 against "namitha" — because "amit" IS a substring of it — which tied
# with the real Namita's 0.92 and got the send refused as ambiguous.

def test_a_name_fragment_is_not_a_match(book):
    """"amit" sits inside "namitha", but Amit is not who was meant."""
    assert not wc.contained_at_word_boundary("namitha", "amit")
    assert wc._score("namitha", "Amit") < wc._score("namitha", "Namita")


def test_a_whole_word_prefix_still_gets_the_containment_boost(book):
    """The case the boost exists for: one word of a real chat name."""
    assert wc.contained_at_word_boundary("burgito", "burgito time")
    assert wc._score("burgito", "burgito time") >= 0.9


def test_namita_beats_amit_by_more_than_the_ambiguity_margin(book):
    """Not just ranked first — far enough ahead that the send goes through."""
    from core.self_extend.whatsapp_mcp import _FUZZY_MARGIN, _name_score
    lead = _name_score("namitha", "Namita") - _name_score("namitha", "Amit")
    assert lead >= _FUZZY_MARGIN
