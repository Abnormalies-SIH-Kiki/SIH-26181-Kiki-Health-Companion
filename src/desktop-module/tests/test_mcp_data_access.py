import json

from core.self_extend import mcp_data_access


def _envelope(data):
    return json.dumps({
        "content": [{"type": "text", "text": json.dumps(data)}],
        "structuredContent": data,
    })


def test_read_gmail_search_path_compacts(monkeypatch):
    captured = {}
    response = {
        "emails": [{
            "message_id": "m1",
            "thread_id": "t1",
            "date": "2026-07-26T10:00:00Z",
            "sender": "Sender <sender@example.com>",
            "to": "me@example.com",
            "subject": "Useful subject",
            "snippet": "Hello&nbsp;there <b>friend</b>",
            "label_ids": ["INBOX", "UNREAD"],
        }],
        "pagination": {
            "total_estimate": 7,
            "next_page_token": "opaque-page-token",
        },
    }

    def fake_call(connection, tool, args_json):
        captured.update({
            "connection": connection,
            "tool": tool,
            "args": json.loads(args_json),
        })
        return _envelope(response)

    monkeypatch.setattr(mcp_data_access.smithery_cli, "tool_call", fake_call)
    result = json.loads(mcp_data_access.read_gmail("is:unread", 3))

    assert captured["connection"] == "gmail"
    assert captured["tool"] == "Gmail_SearchEmailsByQuery"
    assert captured["args"]["result_detail"] == "lightweight"
    assert captured["args"]["max_results"] == 3
    assert captured["args"]["query"] == "is:unread"
    assert result["emails"][0] == {
        "id": "m1",
        "thread_id": "t1",
        "time": "2026-07-26T10:00:00Z",
        "from": "Sender <sender@example.com>",
        "to": "me@example.com",
        "subject": "Useful subject",
        "snippet": "Hello there friend",
        "labels": ["INBOX", "UNREAD"],
        "attachments": [],
    }
    assert result["estimated_total"] == 7
    assert result["next_page_token"] == "opaque-page-token"


def test_read_gmail_without_query_lists_without_bodies(monkeypatch):
    captured = {}

    def fake_call(connection, tool, args_json):
        captured.update({"tool": tool, "args": json.loads(args_json)})
        return _envelope({"emails": [], "pagination": {}})

    monkeypatch.setattr(mcp_data_access.smithery_cli, "tool_call", fake_call)
    json.loads(mcp_data_access.read_gmail(max_results=4))

    assert captured["tool"] == "Gmail_ListEmails"
    assert captured["args"] == {
        "n_emails": 4,
        "include_body": False,
        "exclude_automated": False,
    }


def test_read_gmail_message_returns_plain_body_without_html(monkeypatch):
    captured = {}
    response = {
        "id": "m1",
        "thread_id": "t1",
        "date": "2026-07-26T10:00:00Z",
        "sender": "sender@example.com",
        "to": "me@example.com",
        "subject": "Subject",
        "label_ids": ["INBOX"],
        "body": "First line.\nSecond line.",
        "html_body": "<div>should not win</div>",
    }

    def fake_call(connection, tool, args_json):
        captured.update({"tool": tool, "args": json.loads(args_json)})
        return _envelope(response)

    monkeypatch.setattr(mcp_data_access.smithery_cli, "tool_call", fake_call)
    result = json.loads(mcp_data_access.read_gmail_message("m1"))

    assert captured["tool"] == "Gmail_GetEmail"
    assert captured["args"] == {"email_id": "m1"}
    assert result["body"] == "First line. Second line."
    assert "should not win" not in json.dumps(result)


def test_read_gmail_message_falls_back_to_html_body(monkeypatch):
    monkeypatch.setattr(
        mcp_data_access.smithery_cli, "tool_call",
        lambda *_args: _envelope({"id": "m1", "html_body": "<p>Only&nbsp;HTML</p>"}))

    result = json.loads(mcp_data_access.read_gmail_message("m1"))
    assert result["body"] == "Only HTML"


def test_read_gmail_thread_compacts_each_message(monkeypatch):
    captured = {}
    response = {
        "id": "t1",
        "messages": [{
            "id": "m1",
            "thread_id": "t1",
            "date": "2026-07-26T10:00:00Z",
            "from_": "one@example.com",
            "to": "two@example.com",
            "subject": "Thread subject",
            "label_ids": ["SENT"],
            "html_body": "<p>Thread&nbsp;body</p>",
        }],
    }

    def fake_call(connection, tool, args_json):
        captured.update({"tool": tool, "args": json.loads(args_json)})
        return _envelope(response)

    monkeypatch.setattr(mcp_data_access.smithery_cli, "tool_call", fake_call)
    result = json.loads(mcp_data_access.read_gmail_thread("t1"))

    assert captured["tool"] == "Gmail_GetThread"
    assert captured["args"] == {"thread_id": "t1"}
    assert result["thread_id"] == "t1"
    assert result["messages"][0]["body"] == "Thread body"
    assert result["messages"][0]["from"] == "one@example.com"


def test_decode_surfaces_tool_error_text(monkeypatch):
    monkeypatch.setattr(
        mcp_data_access.smithery_cli, "tool_call",
        lambda *_args: json.dumps({
            "content": [{"type": "text",
                         "text": "Connection \"gmail\" requires authorization."}],
            "isError": True,
        }))

    result = json.loads(mcp_data_access.read_gmail())
    assert "requires authorization" in result["error"]


def test_search_notion_sets_server_side_size_limits(monkeypatch):
    captured = {}
    response = {
        "results": [{
            "id": "page-1",
            "title": "Roadmap",
            "type": "page",
            "timestamp": "2026-07-25T00:00:00Z",
            "highlight": "A short result",
            "url": "https://notion.so/page-1",
        }],
    }

    def fake_call(connection, tool, args_json):
        captured.update({
            "connection": connection,
            "tool": tool,
            "args": json.loads(args_json),
        })
        return _envelope(response)

    monkeypatch.setattr(mcp_data_access.smithery_cli, "tool_call", fake_call)
    result = json.loads(mcp_data_access.search_notion("Kiki roadmap", 4, 120))

    assert captured == {
        "connection": "notion",
        "tool": "notion-search",
        "args": {
            "query": "Kiki roadmap",
            "query_type": "internal",
            "page_size": 4,
            "max_highlight_length": 120,
        },
    }
    assert result["returned"] == 1
    assert result["results"][0]["id"] == "page-1"


def test_read_notion_bounds_content_and_removes_envelope_boilerplate(monkeypatch):
    response = {
        "metadata": {"type": "page"},
        "title": "Page",
        "url": "https://notion.so/page",
        "text": (
            'Here is the result of "view" for the Page:\n'
            "<page><content>" + ("x" * 900) + "</content></page>"
        ),
    }
    monkeypatch.setattr(
        mcp_data_access.smithery_cli, "tool_call",
        lambda *_args: _envelope(response))

    result = json.loads(mcp_data_access.read_notion("page-id", max_chars=500))
    assert result["type"] == "page"
    assert not result["content"].startswith("Here is the result")
    assert "…[+" in result["content"]
    assert len(result["content"]) < 550
