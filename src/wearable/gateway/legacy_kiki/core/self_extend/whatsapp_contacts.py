"""
Kiki's WhatsApp address book.

THE PROBLEM THIS SOLVES
-----------------------
The MCP server reads `messages.db`, which stores chats and senders as bare
identifiers. Measured on this device: **292 direct chats, 0 of them with a
human name** — every DM is a number like ``236601208778873``. So "send a
message to Namita" could never resolve, and a chat summary read out as a list
of phone numbers.

But the Go bridge's own whatsmeow store (`whatsapp.db`) already holds the real
data, unused by anything until now:

* ``whatsmeow_contacts``  — 2397 rows of full_name / first_name / push_name /
  business_name against a phone JID. This is the actual WhatsApp address book.
* ``whatsmeow_lid_map``   — 1221 rows mapping the opaque ``@lid`` identifiers
  that appear as message senders back to real phone numbers.

Chaining those two turns ``128668764475550`` into "Nikhil" (verified 8/8 on
live group senders). This module owns that chain, in both directions:

    resolve_name("namita")      → candidates to SEND to
    display_name("1287...")     → a name to READ OUT in a summary

Manual overrides from ``whatsapp.contacts`` in config.json always win, for
nicknames the address book does not know ("mom", "landlord").

Read-only: nothing here ever writes to the bridge's store.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
_STORE = _ROOT / "whatsapp-mcp" / "whatsapp-bridge" / "store" / "whatsapp.db"

_SPACE_RE = re.compile(r"\s+")
_DIGITS_RE = re.compile(r"^\+?\d[\d\s\-]*$")

_CACHE = None
_CACHE_SIG = None
_LOCK = threading.RLock()

# Names are noisy: WhatsApp push names carry decoration like "~Abhishek~".
_DECORATION_RE = re.compile(r"^[~*_\-\s]+|[~*_\-\s]+$")


def _normalize(value: str) -> str:
    return _SPACE_RE.sub(" ", str(value or "").casefold().strip())


def _clean_name(value: Optional[str]) -> str:
    return _DECORATION_RE.sub("", str(value or "").strip())


def _phone_of(jid: str) -> str:
    return str(jid or "").split("@", 1)[0].split(":", 1)[0]


class _Book:
    """Immutable snapshot of the address book + lid map."""

    def __init__(self, entries, lid_to_pn, pn_to_lid, by_key):
        self.entries = entries          # list of dicts: name, phone, jid, source
        self.lid_to_pn = lid_to_pn
        self.pn_to_lid = pn_to_lid
        self.by_key = by_key            # phone/lid -> best display name


def _store_signature():
    """(mtime, size) of the bridge store — cheap staleness check.

    The bridge writes to this file continuously as contacts sync, so the cache
    must rebuild when it changes rather than being loaded once per process.
    """
    try:
        st = _STORE.stat()
        # st_mtime_ns, not st_mtime: the float loses sub-microsecond resolution
        # at current epoch values, so two writes close together can compare
        # equal and the cache silently keeps serving stale contacts. A small
        # INSERT often reuses a free page, leaving st_size unchanged, so the
        # timestamp is the only thing that moves.
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _manual_contacts() -> dict:
    """``whatsapp.contacts`` from config.json: {"name": "number"}."""
    try:
        from tools_and_config.config_loader import get_full_config
        raw = (get_full_config().get("whatsapp", {}) or {}).get("contacts", {})
    except Exception:
        return {}
    out = {}
    if isinstance(raw, dict):
        for name, number in raw.items():
            name = str(name or "").strip()
            # Keys starting with "_" are comments/examples in config.json, not
            # people — loading them would put "_example_mom" in the address book.
            if not name or name.startswith("_"):
                continue
            digits = re.sub(r"\D", "", str(number or ""))
            if digits:
                out[name] = digits
    return out


def _load() -> _Book:
    entries = []
    lid_to_pn, pn_to_lid, by_key = {}, {}, {}

    if _STORE.is_file():
        try:
            # read-only URI: never risk touching the live bridge store.
            conn = sqlite3.connect(f"file:{_STORE}?mode=ro", uri=True, timeout=5)
            try:
                for lid, pn in conn.execute(
                        "SELECT lid, pn FROM whatsmeow_lid_map"):
                    lid, pn = _phone_of(lid), _phone_of(pn)
                    if lid and pn:
                        lid_to_pn[lid] = pn
                        pn_to_lid.setdefault(pn, lid)

                for their_jid, first, full, push, biz in conn.execute(
                        "SELECT their_jid, first_name, full_name, push_name, "
                        "business_name FROM whatsmeow_contacts"):
                    phone = _phone_of(their_jid)
                    if not phone:
                        continue
                    # The same person is often stored TWICE — once under their
                    # @lid and once under their phone JID. Canonicalize to the
                    # phone so they dedupe into one candidate, otherwise a
                    # unique name looks ambiguous and Kiki asks needlessly.
                    phone = lid_to_pn.get(phone, phone)
                    # Prefer the name the USER saved over WhatsApp's push name.
                    name = next(
                        (_clean_name(c) for c in (full, first, biz, push)
                         if _clean_name(c)), "")
                    if not name or _DIGITS_RE.match(name):
                        continue
                    entries.append({"name": name, "phone": phone,
                                    "jid": f"{phone}@s.whatsapp.net",
                                    "source": "address_book"})
                    by_key.setdefault(phone, name)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            print(f"[WhatsAppContacts] store unreadable ({exc}); "
                  f"falling back to manual contacts only")

    # Manual overrides last so they WIN on duplicate names.
    for name, phone in _manual_contacts().items():
        entries.append({"name": name, "phone": phone,
                        "jid": f"{phone}@s.whatsapp.net", "source": "config"})
        by_key[phone] = name

    # Every lid inherits its phone's name, so message senders resolve directly.
    for lid, pn in lid_to_pn.items():
        if pn in by_key:
            by_key.setdefault(lid, by_key[pn])

    return _Book(entries, lid_to_pn, pn_to_lid, by_key)


# Hard ceiling on how long a (mtime, size) signature is trusted.
#
# MEASURED on this Pi: inode timestamps advance in 4 ms steps (kernel jiffies,
# CONFIG_HZ=250), and a small INSERT into a 3 MB SQLite file reuses a free page
# so st_size does not move at all. So a bridge write landing in the same 4 ms
# tick as a cache load is INVISIBLE to the signature — and stays invisible,
# because nothing later will change it either. A contact synced at that instant
# would never be findable: exactly the "send a message to <someone I just
# added>" failure. Reproduced 2 times in 6.
#
# A full reload is ~0.05 s for 2434 contacts, so re-reading every 30 s costs
# nothing measurable and bounds the staleness window.
_CACHE_TTL_SECONDS = 30.0
_CACHE_LOADED_AT = 0.0


def _book() -> _Book:
    global _CACHE, _CACHE_SIG, _CACHE_LOADED_AT
    with _LOCK:
        sig = _store_signature()
        stale = (time.monotonic() - _CACHE_LOADED_AT) > _CACHE_TTL_SECONDS
        if _CACHE is None or sig != _CACHE_SIG or stale:
            started = time.perf_counter()
            fresh = _load()
            changed = _CACHE is None or len(fresh.entries) != len(_CACHE.entries)
            _CACHE, _CACHE_SIG = fresh, sig
            _CACHE_LOADED_AT = time.monotonic()
            # Only announce real changes: the TTL sweep would otherwise log
            # every 30 seconds forever.
            if changed:
                print(f"[WhatsAppContacts] loaded {len(_CACHE.entries)} contacts, "
                      f"{len(_CACHE.lid_to_pn)} lid mappings "
                      f"({time.perf_counter() - started:.2f}s)")
        return _CACHE


def reload_now():
    """Drop the cache (tests, or after editing whatsapp.contacts)."""
    global _CACHE, _CACHE_SIG, _CACHE_LOADED_AT
    with _LOCK:
        _CACHE = _CACHE_SIG = None
        _CACHE_LOADED_AT = 0.0


# --- reading: identifier → human name --------------------------------------

def display_name(identifier: str) -> Optional[str]:
    """Human name for a sender/JID, or None.

    ``identifier`` may be a raw ``@lid`` number, a phone number, or a full JID —
    all three appear in message rows depending on the chat.
    """
    key = _phone_of(identifier)
    if not key:
        return None
    book = _book()
    name = book.by_key.get(key)
    if name:
        return name
    pn = book.lid_to_pn.get(key)
    return book.by_key.get(pn) if pn else None


def label(identifier: str) -> str:
    """``display_name`` but always returns something printable."""
    return display_name(identifier) or str(identifier or "")


def jid_variants(jid: str) -> list[str]:
    """Every JID form this conversation might be stored under.

    A direct chat is addressed by PHONE when sending, but `messages.db` files
    its history under the opaque ``@lid``. Measured: "Moshe Website" resolves to
    ``16479932193@s.whatsapp.net`` (0 message rows) while its 60 real messages
    live under ``23145595560020@lid``. Reading the wrong one returns nothing,
    and "there are no messages" is then reported as fact.

    Returns the original first, then the alternate form(s), de-duplicated.
    """
    raw = str(jid or "").strip()
    if not raw or raw.endswith("@g.us"):
        return [raw] if raw else []

    key = _phone_of(raw)
    book = _book()
    out = [raw]
    for candidate in (
        f"{book.lid_to_pn[key]}@s.whatsapp.net" if key in book.lid_to_pn else None,
        f"{book.pn_to_lid[key]}@lid" if key in book.pn_to_lid else None,
    ):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def phone_for(identifier: str) -> Optional[str]:
    """Real phone number behind a ``@lid`` identifier (or itself, if already one)."""
    key = _phone_of(identifier)
    if not key:
        return None
    return _book().lid_to_pn.get(key, key)


# --- writing: spoken name → contact ----------------------------------------

def contained_at_word_boundary(a: str, b: str) -> bool:
    """True when one name contains the other as WHOLE words.

    Plain substring containment is far too generous on short names, and it is
    not a near-miss: "amit" is literally inside "namitha", so a request for
    Namita scored the contact Amit 0.90, tied with Namita's 0.92, and the send
    was refused as ambiguous. A length-ratio guard cannot separate those two —
    "amit"/"namitha" is 0.57 and the legitimate "burgito"/"burgito time" is
    0.58. Word boundaries can: "burgito" IS a word of "burgito time"; "amit" is
    only a fragment of "namitha".

    Shared by both scorers so the rule cannot drift between the read path and
    the send path.
    """
    at = [t for t in re.split(r"[^a-z0-9]+", _normalize(a)) if t]
    bt = [t for t in re.split(r"[^a-z0-9]+", _normalize(b)) if t]
    if not at or not bt:
        return False
    if len(at) > len(bt):
        at, bt = bt, at
    return any(bt[i:i + len(at)] == at for i in range(len(bt) - len(at) + 1))


def _score(query: str, name: str) -> float:
    """Similarity of a spoken name to an address-book name, 0..1."""
    q, n = _normalize(query), _normalize(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    ratio = SequenceMatcher(None, q, n).ratio()
    # Containment is a strong signal, but only at a word boundary — see
    # contained_at_word_boundary for the two cases that pin this rule down.
    if contained_at_word_boundary(q, n) and min(len(q), len(n)) >= 3:
        ratio = max(ratio, 0.92)
    qt = [t for t in re.split(r"[^a-z0-9]+", q) if t]
    nt = [t for t in re.split(r"[^a-z0-9]+", n) if t]
    if qt and nt:
        per = [max(SequenceMatcher(None, a, b).ratio() for b in nt) for a in qt]
        ratio = max(ratio, 0.35 * ratio + 0.65 * (sum(per) / len(per)))
    return ratio


def resolve_name(query: str, limit: int = 8, threshold: float = 0.62) -> list[dict]:
    """Address-book contacts matching a spoken name, best first.

    Each result carries ``name``, ``phone``, ``jid``, ``source`` and ``score``.
    Manual config entries outrank address-book entries at equal score.
    """
    query = str(query or "").strip()
    if not query:
        return []
    scored = []
    for entry in _book().entries:
        score = _score(query, entry["name"])
        if score >= threshold:
            item = dict(entry)
            item["score"] = round(score, 3)
            scored.append((score, entry["source"] == "config", item))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)

    # Collapse duplicates on phone number (a person can appear under several
    # name fields), keeping the best-scoring label for each.
    seen, out = set(), []
    for _score_value, _is_cfg, item in scored:
        if item["phone"] in seen:
            continue
        seen.add(item["phone"])
        out.append(item)
        if len(out) >= limit:
            break
    return out


def stats() -> dict:
    book = _book()
    return {
        "contacts": len(book.entries),
        "lid_mappings": len(book.lid_to_pn),
        "manual": sum(1 for e in book.entries if e["source"] == "config"),
        "store": str(_STORE),
        "store_present": _STORE.is_file(),
    }
