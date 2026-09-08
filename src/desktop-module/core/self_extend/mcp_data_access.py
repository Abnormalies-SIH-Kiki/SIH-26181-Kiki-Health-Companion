"""Compact, model-facing reads for Kiki's connected Gmail and Notion MCPs.

The generic Smithery bridge is intentionally retained for uncommon operations,
but its raw envelopes, full schemas and Gmail HTML payloads are far too large
for routine background reads.  These helpers call the known connected tools
directly and return only the fields an agent normally needs.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any

from core.self_extend import smithery_cli


_SPACE_RE = re.compile(r"\s+")
_HTML_TAG_RE = re.compile(
    r"</?(?:html|body|head|style|script|div|span|p|br|table|tr|td|li|ul|ol|"
    r"h[1-6]|a|img|strong|b|em|i)(?:\s|/?>)",
    re.IGNORECASE,
)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, _attrs):
        if tag in {"script", "style", "head"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in {
            "br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in {"script", "style", "head"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if not self._ignored_depth:
            self.parts.append(data)


def _clean_text(value: Any, limit: int) -> str:
    text = html.unescape(str(value or ""))
    if _HTML_TAG_RE.search(text):
        parser = _HTMLTextExtractor()
        try:
            parser.feed(text)
            text = " ".join(parser.parts)
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + f"…[+{len(text) - limit} chars]"
    return text


def _decode_smithery(raw: str) -> tuple[dict | None, str | None]:
    """Unwrap Smithery's MCP envelope and its nested JSON text response."""
    if not raw:
        return None, "MCP returned an empty response."
    if raw.lower().startswith("error:"):
        return None, raw
    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        return None, f"MCP returned invalid JSON: {_clean_text(raw, 500)}"

    if outer.get("isError"):
        # Smithery reports tool/auth failures inside a normal envelope with a
        # zero exit code. Surface the real text (including any authorization
        # URL) instead of letting it fall through as "unknown error".
        details = " ".join(
            str(item.get("text", "")) for item in outer.get("content", [])
            if isinstance(item, dict))
        return None, _clean_text(details or outer.get("error"), 600) or \
            "MCP reported an error without details."

    structured = outer.get("structuredContent")
    if isinstance(structured, dict):
        return structured, None
    for item in outer.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        text = item.get("text", "")
        try:
            parsed = json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return {"text": str(text)}, None
        if isinstance(parsed, dict):
            return parsed, None
    return None, "MCP response did not contain structured text data."


def _result(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _email_attachments(item: dict) -> list[str]:
    names = []
    for attachment in item.get("attachments") or []:
        if isinstance(attachment, dict):
            name = attachment.get("filename") or attachment.get("name")
            if name:
                names.append(str(name)[:160])
    return names[:10]


def _compact_email(item: Any, snippet_limit: int = 500,
                   body_limit: int = 0) -> dict | None:
    """Normalise one email row from any of the Gmail_* tools.

    The list, search and get tools return the same message under different
    field names (``id``/``message_id``, ``from_``/``sender``), so every reader
    goes through here and emits one stable shape.
    """
    if not isinstance(item, dict):
        return None
    row = {
        "id": item.get("id") or item.get("message_id"),
        "thread_id": item.get("thread_id"),
        "time": item.get("date"),
        "from": _clean_text(item.get("from_") or item.get("sender"), 240),
        "to": _clean_text(item.get("to"), 240),
        "subject": _clean_text(item.get("subject"), 300),
        "labels": list(item.get("label_ids") or [])[:12],
    }
    if body_limit:
        body = item.get("body") or item.get("html_body") or item.get("snippet")
        row["body"] = _clean_text(body, body_limit)
    else:
        row["snippet"] = _clean_text(item.get("snippet"), snippet_limit)
        row["attachments"] = _email_attachments(item)
    return row


def read_gmail(query: str = "", max_results: int = 5) -> str:
    """Return compact Gmail metadata and snippets without MIME/HTML payloads."""
    limit = min(20, max(1, int(max_results)))
    query = str(query or "").strip()
    if query:
        tool = "Gmail_SearchEmailsByQuery"
        args = {
            "query": query,
            "max_results": limit,
            "result_detail": "lightweight",
            "include_spam_trash": False,
        }
    else:
        # No query means "the newest mail". exclude_automated stays False so
        # this keeps returning everything the previous server did; flip it to
        # True to drop promotions/social/updates and no-reply senders.
        tool = "Gmail_ListEmails"
        args = {
            "n_emails": limit,
            "include_body": False,
            "exclude_automated": False,
        }
    raw = smithery_cli.tool_call(
        "gmail", tool, json.dumps(args, separators=(",", ":")))
    data, error = _decode_smithery(raw)
    if error:
        return _result({"error": error})

    emails = []
    for item in (data or {}).get("emails", [])[:limit]:
        row = _compact_email(item)
        if row:
            emails.append(row)
    pagination = (data or {}).get("pagination")
    if not isinstance(pagination, dict):
        pagination = {}
    return _result({
        "emails": emails,
        "returned": len(emails),
        "estimated_total": pagination.get("total_estimate"),
        "next_page_token": pagination.get("next_page_token"),
    })


def read_gmail_message(message_id: str, max_body_chars: int = 5000) -> str:
    """Read one Gmail message as clean bounded text, excluding raw headers."""
    body_limit = min(12000, max(500, int(max_body_chars)))
    args = {"email_id": str(message_id)}
    raw = smithery_cli.tool_call(
        "gmail", "Gmail_GetEmail", json.dumps(args, separators=(",", ":")))
    data, error = _decode_smithery(raw)
    if error:
        return _result({"error": error})
    row = _compact_email(data, body_limit=body_limit)
    return _result(row or {"error": "Gmail returned no message."})


def read_gmail_thread(thread_id: str, max_messages: int = 10,
                      max_body_chars: int = 2000) -> str:
    """Read a Gmail thread as a bounded list of clean messages."""
    message_limit = min(20, max(1, int(max_messages)))
    body_limit = min(8000, max(300, int(max_body_chars)))
    args = {"thread_id": str(thread_id)}
    raw = smithery_cli.tool_call(
        "gmail", "Gmail_GetThread", json.dumps(args, separators=(",", ":")))
    data, error = _decode_smithery(raw)
    if error:
        return _result({"error": error})

    messages = []
    for item in (data or {}).get("messages", [])[:message_limit]:
        row = _compact_email(item, body_limit=body_limit)
        if row:
            messages.append(row)
    return _result({
        "thread_id": str(thread_id),
        "messages": messages,
        "returned": len(messages),
    })


def search_notion(query: str, max_results: int = 5,
                  highlight_chars: int = 240) -> str:
    """Search Notion and return a small ranked result list."""
    limit = min(10, max(1, int(max_results)))
    highlight_limit = min(400, max(0, int(highlight_chars)))
    args = {
        "query": str(query).strip(),
        "query_type": "internal",
        "page_size": limit,
        "max_highlight_length": highlight_limit,
    }
    raw = smithery_cli.tool_call(
        "notion", "notion-search", json.dumps(args, separators=(",", ":")))
    data, error = _decode_smithery(raw)
    if error:
        return _result({"error": error})

    results = []
    for item in (data or {}).get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        results.append({
            "id": item.get("id"),
            "title": _clean_text(item.get("title"), 300),
            "type": item.get("type"),
            "updated": item.get("timestamp"),
            "highlight": _clean_text(item.get("highlight"), highlight_limit),
            "url": item.get("url"),
        })
    return _result({"results": results, "returned": len(results)})


def read_notion(entity_id: str, max_chars: int = 6000) -> str:
    """Fetch one Notion entity while bounding its enhanced-Markdown content."""
    text_limit = min(16000, max(500, int(max_chars)))
    args = {
        "id": str(entity_id),
        "include_transcript": False,
        "include_discussions": False,
    }
    raw = smithery_cli.tool_call(
        "notion", "notion-fetch", json.dumps(args, separators=(",", ":")))
    data, error = _decode_smithery(raw)
    if error:
        return _result({"error": error})

    text = str((data or {}).get("text", ""))
    if text.startswith('Here is the result of "view"'):
        first_newline = text.find("\n")
        if first_newline >= 0:
            text = text[first_newline + 1:]
    if len(text) > text_limit:
        text = text[:text_limit].rstrip() + f"\n…[+{len(text) - text_limit} chars]"
    metadata = (data or {}).get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return _result({
        "type": metadata.get("type"),
        "title": _clean_text((data or {}).get("title"), 300),
        "url": (data or {}).get("url"),
        "content": text,
    })
