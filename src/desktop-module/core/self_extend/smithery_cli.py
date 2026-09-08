"""
core/self_extend/smithery_cli.py
=================================
Thin wrapper around the Smithery CLI (smithery v4.7.4+).

All operations go through subprocess calls so they share the user's
authenticated session from `smithery auth login`.  No API keys needed.

Smithery CLI commands covered:
  smithery mcp search [term]
  smithery mcp add <server>
  smithery mcp list
  smithery mcp get <id>
  smithery mcp remove <ids...>
  smithery tool find <connection> [query]
  smithery tool list <connection> [prefix]
  smithery tool get <connection> <tool>
  smithery tool call <connection> <tool> [args]
  smithery skill search <query>
  smithery skill view <identifier>
  smithery skill add <skill>
  smithery skill agents
"""

import subprocess
from typing import Optional


SMITHERY_BIN = "smithery"


def _run(args: list[str], timeout: int = 30,
         extra_env: Optional[dict] = None) -> tuple[str, str, int]:
    """
    Run a smithery command.
    Returns (stdout, stderr, returncode).
    """
    import os
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    try:
        result = subprocess.run(
            [SMITHERY_BIN] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except FileNotFoundError:
        return (
            "",
            "Smithery CLI not found. Install: sudo npm install -g @smithery/cli@latest",
            1,
        )
    except subprocess.TimeoutExpired:
        return "", f"Command timed out after {timeout}s", 1
    except Exception as exc:
        return "", str(exc), 1


def _ok(stdout: str, stderr: str, rc: int) -> str:
    """Return stdout on success, stderr on failure."""
    if rc == 0:
        return stdout
    return f"error: {stderr or 'unknown error'}"


# ──────────────────────────────────────────────────────────────────────────────
# MCP COMMANDS
# ──────────────────────────────────────────────────────────────────────────────

def mcp_search(term: str = "", page: int = 1) -> str:
    """smithery mcp search [term] [--page N]"""
    args = ["mcp", "search"]
    if term:
        args.append(term)
    if page > 1:
        args += ["--page", str(page)]
    return _ok(*_run(args))


def mcp_add(server: str) -> str:
    """smithery mcp add <server>  (connection URL or registry ID)"""
    return _ok(*_run(["mcp", "add", server]))


def mcp_list() -> str:
    """smithery mcp list"""
    return _ok(*_run(["mcp", "list"]))


def mcp_get(connection_id: str) -> str:
    """smithery mcp get <id>"""
    return _ok(*_run(["mcp", "get", connection_id]))


def mcp_remove(*ids: str) -> str:
    """smithery mcp remove <ids...>"""
    if not ids:
        return "error: no IDs provided"
    return _ok(*_run(["mcp", "remove"] + list(ids)))


# ──────────────────────────────────────────────────────────────────────────────
# TOOL COMMANDS
# ──────────────────────────────────────────────────────────────────────────────

def tool_find(connection: str, query: str = "") -> str:
    """smithery tool find <connection> [query]"""
    args = ["tool", "find", connection]
    if query:
        args.append(query)
    return _ok(*_run(args))


def tool_list(connection: str, prefix: str = "") -> str:
    """smithery tool list <connection> [prefix]"""
    args = ["tool", "list", connection]
    if prefix:
        args.append(prefix)
    return _ok(*_run(args))


def tool_get(connection: str, tool: str) -> str:
    """smithery tool get <connection> <tool>"""
    return _ok(*_run(["tool", "get", connection, tool]))


def tool_call(connection: str, tool: str, args_json: str = "",
              timeout: int = 60) -> str:
    """smithery tool call <connection> <tool> [args]"""
    cmd = ["tool", "call", connection, tool]
    if args_json and args_json.strip():
        cmd.append(args_json.strip())
    return _ok(*_run(cmd, timeout=timeout))


# ──────────────────────────────────────────────────────────────────────────────
# SKILL COMMANDS
# ──────────────────────────────────────────────────────────────────────────────

def skill_search(query: str = "", page: int = 1) -> str:
    """smithery skill search <query>"""
    args = ["skill", "search", query] if query else ["skill", "search", "--help"]
    if page > 1:
        args += ["--page", str(page)]
    return _ok(*_run(args))


def skill_view(identifier: str) -> str:
    """
    smithery skill view <identifier>

    Returns the FULL skill documentation (YAML frontmatter + markdown body).
    This is the authoritative source for SKILL.md content.
    identifier format: 'namespace/slug'  e.g. 'langfuse/skill-developer'
    """
    return _ok(*_run(["skill", "view", identifier], timeout=20))


def skill_add(skill: str, agent: str = "claude-code") -> str:
    """
    smithery skill add <skill> [--agent <agent>]

    Installs the skill into the specified agent's skill store.
    Use skill_view() to also save a local SKILL.md copy for Kiki.
    """
    args = ["skill", "add", skill]
    if agent:
        args += ["--agent", agent]
    return _ok(*_run(args, timeout=30))


def skill_agents() -> str:
    """smithery skill agents — list available agents for skill installation."""
    return _ok(*_run(["skill", "agents"]))


# ──────────────────────────────────────────────────────────────────────────────
# COMBINED HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def install_skill_to_kiki(identifier: str) -> dict:
    """
    Full install flow for Kiki:
    1. `smithery skill view <identifier>` → full SKILL.md content
    2. Save as a local SKILL.md in Kiki's skills directory
    3. (Optionally) `smithery skill add` to also register in the agent store

    Returns: {
        "skill_name": str,
        "path": str,
        "already_existed": bool,
        "content_length": int,
    }
    """
    from core.self_extend.skill_manager import SkillManager

    # Fetch full content via CLI
    content = skill_view(identifier)
    if content.startswith("error:"):
        return {"error": content}

    # Derive a filesystem-safe skill name from identifier
    # e.g. "langfuse/skill-developer" → "langfuse__skill_developer"
    skill_name = identifier.replace("/", "__").replace("-", "_")

    sm = SkillManager()
    already_existed = sm.skill_exists(skill_name)
    path = sm.create_skill(skill_name, content)

    return {
        "skill_name": skill_name,
        "path": path,
        "already_existed": already_existed,
        "content_length": len(content),
    }
