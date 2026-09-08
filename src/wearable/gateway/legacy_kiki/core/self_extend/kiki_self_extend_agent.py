"""
core/self_extend/kiki_self_extend_agent.py
=============================================
KikiSelfExtendAgent — autonomous self-extension for Kiki.

Uses generate_llm_resp.generate() (Gemini → Groq → local) via a
prompt-based JSON tool-calling loop.  NO Anthropic API dependency.

Loop mechanics
--------------
Each iteration sends the LLM a prompt containing:
  1. System instructions (who it is, available tools, output format)
  2. The current task/goal
  3. The conversation history so far (tool results)

The LLM outputs ONE JSON block per turn:
  {"thought": "...", "tool": "tool_name", "args": {...}}
  or
  {"thought": "...", "done": true, "result": "final answer"}

The agent parses this, dispatches the tool, appends the result, and loops.
"""

import json
import re
import textwrap
from pathlib import Path
from typing import Any, Optional

from core.self_extend.skill_manager import SkillManager
from core.self_extend.mcp_manager import (
    MCPRegistryClient, MCPConfigManager, MCPServerCreator, SmitherySkillsClient
)
import core.self_extend.smithery_cli as _cli


# ──────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ──────────────────────────────────────────────────────────────────────────────

KIKI_SELF_EXTEND_SYSTEM = """\
You are Kiki's self-extension mind — an autonomous agent that extends Kiki's own
capabilities by installing Smithery skills and connecting MCP servers.

You are given a GOAL. Achieve it step by step using the available tools.

## OUTPUT FORMAT (STRICT)
Each response must be a SINGLE JSON object, nothing else:

To call a tool:
{"thought": "why I'm calling this tool", "tool": "<tool_name>", "args": {<key>: <value>}}

To finish:
{"thought": "summarize what was accomplished", "done": true, "result": "<final summary>"}

## AVAILABLE TOOLS

### list_skills
List all skills already installed in Kiki's local skills directory.
args: {}

### read_skill
Read the full SKILL.md content of an installed skill.
args: {"name": "<skill_folder_name>"}

### create_skill
Create a brand-new SKILL.md manually. Use only when nothing suitable exists on Smithery.
args: {
  "name": "<snake_case_name>",
  "skill_md_content": "<full SKILL.md>",
  "extra_files": {"filename.py": "content"}  // optional
}

### delete_skill
Delete a locally installed skill.
args: {"name": "<skill_folder_name>"}

### search_smithery_skills
Search the Smithery Skills registry (smithery skill search). Returns identifiers.
Always try this FIRST before creating a skill from scratch.
args: {"query": "<natural language>", "page": 1}

### view_smithery_skill
Fetch the FULL content of a Smithery skill without installing it.
Use to preview content before deciding to install.
args: {"identifier": "namespace/slug"}

### install_smithery_skill
Download and install a Smithery skill into Kiki's local skills directory.
Fetches the COMPLETE documentation via `smithery skill view`.
args: {"identifier": "namespace/slug"}

### search_mcp
Search the Smithery MCP server registry (smithery mcp search).
Returns server IDs and descriptions.
args: {"query": "<natural language>", "page": 1}

### mcp_add
Connect an MCP server via Smithery CLI (smithery mcp add).
args: {"server": "<server-id or url>"}

### mcp_list
List all currently connected MCP servers.
args: {}

### mcp_remove
Remove MCP server connection(s).
args: {"ids": "<space-separated connection IDs>"}

### tool_find
Search tools in a connected MCP server.
args: {"connection": "<connection-id>", "query": "<intent>"}

### tool_list
List all tools from a connected MCP server.
args: {"connection": "<connection-id>"}

### tool_call
Call a tool from a connected MCP server.
args: {"connection": "<connection-id>", "tool": "<tool-name>", "args_json": "{...}"}

### create_mcp_server
Generate and register a custom FastMCP Python server from scratch.
Only use when nothing suitable exists on Smithery.
args: {
  "server_name": "<snake_case>",
  "description": "<what it does>",
  "tools": [{"name": "...", "description": "...", "params": [{"name":"p","type":"str"}], "impl": "python body"}]
}

### read_file
Read any file from disk.
args: {"path": "<path>"}

### write_file
Write text to a file (creates parent dirs).
args: {"path": "<path>", "content": "<text>"}

### list_directory
List files in a directory.
args: {"path": "<path>"}

### run_shell_command
Run a shell command (non-destructive only).
args: {"command": "<shell string>", "timeout": 30}

## GUIDELINES
- Check existing skills first (list_skills) before installing or creating new ones.
- For skills: ALWAYS try search_smithery_skills first. Only create_skill if nothing suitable exists.
- For MCP: search_mcp → mcp_add to connect → tool_list to see available tools.
- Prefer Smithery registry over creating from scratch.
- After each tool call you will receive the result. Use it to decide the next step.
- Be efficient: accomplish the goal in as few steps as possible.
- When done, set done=true and provide a clear, concise result string.
"""


# ──────────────────────────────────────────────────────────────────────────────
# TOOL EXECUTOR  (local, no network except registry search)
# ──────────────────────────────────────────────────────────────────────────────

import subprocess


class _ToolExecutor:
    """Dispatch tool calls to the appropriate manager."""

    def __init__(self):
        self.skills  = SkillManager()
        self.mcpcfg  = MCPConfigManager()
        self.creator = MCPServerCreator(config_manager=self.mcpcfg)

    def execute(self, tool_name: str, args: dict) -> str:
        try:
            return self._dispatch(tool_name, args)
        except Exception as exc:
            return f"❌ Tool '{tool_name}' error: {exc}"

    def _dispatch(self, name: str, inp: dict) -> str:  # noqa: C901
        # ── Local Skills ───────────────────────────────────────────────────
        if name == "list_skills":
            skills = self.skills.list_skills()
            if not skills:
                return f"No skills found in {self.skills.root}"
            lines = [f"📁 Skills dir: {self.skills.root}\n"]
            for s in skills:
                lines.append(f"🔧 **{s['name']}**\n   {s['preview'][:200]}\n")
            return "\n".join(lines)

        if name == "read_skill":
            content = self.skills.read_skill(inp["name"])
            return content if content else f"Skill '{inp['name']}' not found."

        if name == "create_skill":
            path = self.skills.create_skill(
                inp["name"],
                inp["skill_md_content"],
                inp.get("extra_files"),
            )
            return f"✅ Skill '{inp['name']}' created at: {path}"

        if name == "delete_skill":
            ok = self.skills.delete_skill(inp["name"])
            return (f"🗑️ Skill '{inp['name']}' deleted."
                    if ok else f"Skill '{inp['name']}' not found.")

        # ── Smithery Skills (CLI) ────────────────────────────────────
        if name == "search_smithery_skills":
            return _cli.skill_search(
                inp.get("query", ""),
                page=inp.get("page", 1),
            )

        if name == "view_smithery_skill":
            return _cli.skill_view(inp["identifier"])

        if name == "install_smithery_skill":
            result = _cli.install_skill_to_kiki(inp["identifier"])
            if "error" in result:
                return f"Error: {result['error']}"
            already = " (updated)" if result.get("already_existed") else ""
            return (
                f"✅ Installed '{inp['identifier']}'{already}\n"
                f"   Local name: {result['skill_name']}\n"
                f"   Path: {result['path']}\n"
                f"   Size: {result.get('content_length',0)} characters"
            )

        # ── MCP (CLI) ────────────────────────────────────────────────────
        if name == "search_mcp":
            return _cli.mcp_search(inp.get("query", ""), page=inp.get("page", 1))

        if name == "mcp_add":
            return _cli.mcp_add(inp["server"])

        if name == "mcp_list":
            return _cli.mcp_list()

        if name == "mcp_remove":
            ids = inp.get("ids", "").split()
            return _cli.mcp_remove(*ids)

        # ── MCP Tools (CLI) ───────────────────────────────────────────
        if name == "tool_find":
            return _cli.tool_find(inp["connection"], inp.get("query", ""))

        if name == "tool_list":
            return _cli.tool_list(inp["connection"])

        if name == "tool_call":
            return _cli.tool_call(
                inp["connection"], inp["tool"],
                args_json=inp.get("args_json", "")
            )

        # ── Create custom MCP server (Python/FastMCP) ──────────────────
        if name == "create_mcp_server":
            result = self.creator.create_and_register(
                server_name=inp["server_name"],
                description=inp["description"],
                tools=inp.get("tools", []),
                extra_env=inp.get("extra_env"),
            )
            return (
                f"✅ MCP server '{inp['server_name']}' created!\n"
                f"   Code: {result['code_path']}\n"
                f"   Config: {result['config_status']}\n"
                f"   ℹ️  Restart MCP host to activate."
            )

        # ── Filesystem ───────────────────────────────────────────────
        if name == "read_file":
            p = Path(inp["path"])
            if not p.exists():
                return f"File not found: {p}"
            return p.read_text(errors="replace")

        if name == "write_file":
            p = Path(inp["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inp["content"])
            return f"✅ Written {len(inp['content'])} chars → {p}"

        if name == "list_directory":
            p = Path(inp["path"])
            if not p.exists():
                return f"Path not found: {p}"
            items = sorted(p.iterdir())
            lines = [f"📂 {p}\n"]
            for item in items:
                icon = "📁" if item.is_dir() else "📄"
                lines.append(f"  {icon} {item.name}")
            return "\n".join(lines)

        if name == "run_shell_command":
            timeout = inp.get("timeout", 30)
            result = subprocess.run(
                inp["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            parts = []
            if result.stdout.strip():
                parts.append(f"STDOUT:\n{result.stdout.strip()}")
            if result.stderr.strip():
                parts.append(f"STDERR:\n{result.stderr.strip()}")
            parts.append(f"Return code: {result.returncode}")
            return "\n".join(parts)

        return f"Unknown tool: {name}"


# ──────────────────────────────────────────────────────────────────────────────
# JSON EXTRACTION HELPER
# ──────────────────────────────────────────────────────────────────────────────

_JSON_RE = re.compile(r'\{.*\}', re.DOTALL)


def _extract_json(text: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response string."""
    # Strip markdown code fences
    text = re.sub(r'```(?:json)?\s*', '', text)
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find {...} block
    m = _JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return None


# ──────────────────────────────────────────────────────────────────────────────
# AGENT
# ──────────────────────────────────────────────────────────────────────────────

class KikiSelfExtendAgent:
    """
    Autonomous agent that extends Kiki's capabilities.

    Uses generate_llm_resp.generate() — NO Anthropic API.
    Implements a prompt-based JSON tool-calling loop.
    """

    def __init__(self):
        self.executor = _ToolExecutor()

    def run_task(self, goal: str, max_iterations: int = 15) -> str:
        """
        Execute a self-extension task.

        Args:
            goal: Natural language description of what to do.
            max_iterations: Safety cap on the agent loop.

        Returns:
            Final result string from the agent, or error message.
        """
        from core.brain.generate_llm_resp import generate

        print(f"\n{'='*60}")
        print(f"[SelfExtend] Goal: {goal}")
        print(f"{'='*60}\n")

        # Build the conversation history as a single growing prompt
        conversation: list[dict] = []  # {"role": "user"|"assistant", "text": ...}

        def _build_prompt() -> str:
            parts = [KIKI_SELF_EXTEND_SYSTEM, "\n---\n", f"GOAL: {goal}\n"]
            for turn in conversation:
                role = turn["role"].upper()
                parts.append(f"\n[{role}]\n{turn['text']}")
            parts.append("\n[ASSISTANT]\n")
            return "".join(parts)

        for iteration in range(max_iterations):
            prompt = _build_prompt()

            print(f"[SelfExtend] Iteration {iteration + 1}/{max_iterations}")

            response_text = generate(
                prompt,
                thinking_level="MEDIUM",
                purpose="reasoning"
            )

            if not response_text:
                print("[SelfExtend] LLM returned empty response, aborting.")
                return "⚠️ LLM returned no response during self-extend task."

            print(f"[SelfExtend] LLM response: {response_text[:300]}")

            # Append assistant turn
            conversation.append({"role": "assistant", "text": response_text})

            # Parse JSON action
            action = _extract_json(response_text)
            if not action:
                # Not valid JSON — add an error feedback turn
                err = "⚠️ Could not parse your response as JSON. Please respond with a single JSON object as specified."
                conversation.append({"role": "user", "text": err})
                print(f"[SelfExtend] Parse error: {response_text[:200]}")
                continue

            thought = action.get("thought", "")
            if thought:
                print(f"[SelfExtend] 💭 {thought}")

            # Done?
            if action.get("done"):
                result = action.get("result", "Task completed.")
                print(f"\n[SelfExtend] ✅ Done after {iteration + 1} iteration(s).")
                print(f"[SelfExtend] Result: {result}")
                return result

            # Execute tool
            tool_name = action.get("tool", "")
            args = action.get("args", {})
            if not tool_name:
                conversation.append({
                    "role": "user",
                    "text": "⚠️ No 'tool' specified. Please specify a tool name or set done=true."
                })
                continue

            print(f"[SelfExtend] 🔧 Calling tool: {tool_name}")
            print(f"[SelfExtend]    Args: {json.dumps(args, default=str)[:300]}")
            tool_result = self.executor.execute(tool_name, args)
            print(f"[SelfExtend]    Result: {str(tool_result)[:300]}")

            # Feed result back
            conversation.append({
                "role": "user",
                "text": f"Tool '{tool_name}' result:\n{tool_result}"
            })

        return "⚠️ Self-extend agent reached max iterations without concluding."
