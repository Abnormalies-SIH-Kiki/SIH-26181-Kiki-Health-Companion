import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

from core.self_extend import whatsapp_mcp


ROOT = Path(__file__).resolve().parents[1]
SERVER_DIR = ROOT / "whatsapp-mcp" / "whatsapp-mcp-server"


def _load_server_module():
    sys.path.insert(0, str(SERVER_DIR))
    try:
        spec = importlib.util.spec_from_file_location(
            "kiki_test_whatsapp_server", SERVER_DIR / "main.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SERVER_DIR))


def test_bundled_mcp_exposes_every_required_tool():
    server = _load_server_module()
    for name in whatsapp_mcp._EXPECTED_TOOLS:
        assert callable(getattr(server, name))


def test_mcp_results_convert_dataclasses_and_datetimes():
    server = _load_server_module()
    whatsapp = sys.modules["whatsapp"]
    chat = whatsapp.Chat(
        jid="123@s.whatsapp.net",
        name="Person",
        last_message_time=datetime(2026, 7, 26, 12, 30),
    )
    assert server._jsonable([chat]) == [{
        "jid": "123@s.whatsapp.net",
        "name": "Person",
        "last_message_time": "2026-07-26T12:30:00",
        "last_message": None,
        "last_sender": None,
        "last_is_from_me": None,
    }]


def test_kiki_catalog_and_handlers_include_all_whatsapp_tools():
    from tools_and_config.tools import TOOLS, _ASYNC_TOOL_HANDLERS

    names = {tool["function"]["name"] for tool in TOOLS}
    assert whatsapp_mcp._EXPECTED_TOOLS <= names
    assert whatsapp_mcp._EXPECTED_TOOLS <= _ASYNC_TOOL_HANDLERS.keys()


def test_unique_contact_name_is_resolved_before_send():
    calls = []

    class FakeClient:
        def call_tool(self, name, arguments, timeout=None):
            calls.append((name, arguments))
            if name == "search_contacts":
                return [{
                    "name": "Mum",
                    "phone_number": "911234567890",
                    "jid": "911234567890@s.whatsapp.net",
                }]
            return {"success": True}

    resolved, error = whatsapp_mcp._resolve_recipient(
        FakeClient(), "Mum", timeout=2)
    assert error is None
    assert resolved == "911234567890@s.whatsapp.net"
    assert calls[0][0] == "search_contacts"


def test_contact_name_is_resolved_for_last_interaction(monkeypatch):
    calls = []

    class FakeClient:
        def call_tool(self, name, arguments, timeout=None):
            calls.append((name, dict(arguments)))
            if name == "search_contacts":
                return [{
                    "name": "Mum",
                    "phone_number": "911234567890",
                    "jid": "911234567890@s.whatsapp.net",
                }]
            return "latest message"

    monkeypatch.setattr(
        whatsapp_mcp, "get_whatsapp_mcp", lambda *_a, **_k: FakeClient())
    result = whatsapp_mcp.call_whatsapp_tool_data(
        "get_last_interaction", {"jid": "Mum"}, resolve_contact=True)
    assert result == "latest message"
    assert calls[-1] == (
        "get_last_interaction",
        {"jid": "911234567890@s.whatsapp.net"},
    )


def test_compact_tool_json_returns_errors_without_raising(monkeypatch):
    class BrokenClient:
        def call_tool(self, *_args, **_kwargs):
            raise TimeoutError("not ready")

    monkeypatch.setattr(whatsapp_mcp, "get_whatsapp_mcp", lambda *_a, **_k: BrokenClient())
    result = json.loads(whatsapp_mcp.call_whatsapp_tool_json("list_chats"))
    assert result["tool"] == "list_chats"
    assert "not ready" in result["error"]
