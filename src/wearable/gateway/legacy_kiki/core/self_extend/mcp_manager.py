"""
core/self_extend/mcp_manager.py
================================
MCP registry search, config management, and server code generation.

Adapted from claude_self_extend.py — no Anthropic dependency.
"""

import json
import os
import platform
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests

from tools_and_config.config_loader import get_full_config


# ── Path helpers ─────────────────────────────────────────────────────────────

def get_mcp_servers_root() -> Path:
    config = get_full_config()
    se_cfg = config.get("self_extend", {})
    default_dir = str(Path.home() / ".claude" / "mcp_servers")
    path = Path(se_cfg.get("mcp_servers_dir", default_dir))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_claude_desktop_config_path() -> Path:
    """Platform-specific path to Claude Desktop config."""
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def get_smithery_api_key() -> str:
    config = get_full_config()
    se_cfg = config.get("self_extend", {})
    return se_cfg.get("smithery_api_key", "") or os.environ.get("SMITHERY_API_KEY", "")


def get_github_token() -> str:
    config = get_full_config()
    se_cfg = config.get("self_extend", {})
    return se_cfg.get("github_token", "") or os.environ.get("GITHUB_TOKEN", "")


# ──────────────────────────────────────────────────────────────────────────────
# MCP REGISTRY CLIENT  (Smithery + GitHub fallback)
# ──────────────────────────────────────────────────────────────────────────────

class MCPRegistryClient:
    """Search for MCP servers in Smithery registry and GitHub."""

    SMITHERY_BASE = "https://registry.smithery.ai"
    GITHUB_SEARCH = "https://api.github.com/search/repositories"

    def __init__(self, smithery_key: str = "", github_token: str = ""):
        self.smithery_key = smithery_key or get_smithery_api_key()
        self.github_token = github_token or get_github_token()

    # ── Smithery ──────────────────────────────────────────────────────────────

    def search_smithery(self, query: str, page: int = 1, page_size: int = 8) -> dict:
        """Semantic search over the Smithery MCP registry."""
        if not self.smithery_key:
            return {"error": "SMITHERY_API_KEY not set", "servers": []}
        headers = {
            "Authorization": f"Bearer {self.smithery_key}",
            "Accept": "application/json",
        }
        params = {"q": query, "page": page, "pageSize": page_size}
        try:
            r = requests.get(f"{self.SMITHERY_BASE}/servers",
                             headers=headers, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc), "servers": []}

    def get_smithery_server(self, qualified_name: str) -> dict:
        """Fetch full details (tools, config schema, etc.) for one server."""
        if not self.smithery_key:
            return {"error": "SMITHERY_API_KEY not set"}
        headers = {
            "Authorization": f"Bearer {self.smithery_key}",
            "Accept": "application/json",
        }
        try:
            r = requests.get(f"{self.SMITHERY_BASE}/servers/{qualified_name}",
                             headers=headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            return {"error": str(exc)}

    # ── GitHub fallback ───────────────────────────────────────────────────────

    def search_github(self, query: str, max_results: int = 6) -> list[dict]:
        """Search GitHub for MCP server repos as a fallback."""
        full_query = f"{query} mcp-server topic:mcp"
        headers = {"Accept": "application/vnd.github+json"}
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        params = {"q": full_query, "sort": "stars", "per_page": max_results}
        try:
            r = requests.get(self.GITHUB_SEARCH, headers=headers,
                             params=params, timeout=10)
            r.raise_for_status()
            items = r.json().get("items", [])
            return [
                {
                    "name": i["full_name"],
                    "description": i.get("description", ""),
                    "stars": i.get("stargazers_count", 0),
                    "url": i["html_url"],
                    "topics": i.get("topics", []),
                }
                for i in items
            ]
        except Exception as exc:
            return [{"error": str(exc)}]

    def search(self, query: str) -> dict:
        """Unified search: tries Smithery first, falls back to GitHub."""
        smithery = self.search_smithery(query)
        github   = self.search_github(query)
        return {"smithery": smithery, "github": github}


# ──────────────────────────────────────────────────────────────────────────────
# SMITHERY SKILLS CLIENT
# ──────────────────────────────────────────────────────────────────────────────

class SmitherySkillsClient:
    """
    Client for the Smithery Skills API.

    Skills are pre-built prompt instructions (not MCP servers).
    GET /skills          → search/list skills
    GET /skills/{ns}/{slug} → fetch a specific skill (includes 'prompt' field)
    """

    BASE = "https://api.smithery.ai"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or get_smithery_api_key()

    def _headers(self) -> dict:
        if not self.api_key:
            raise ValueError("SMITHERY_API_KEY is not set. Add it to config.json under self_extend.smithery_api_key or set the SMITHERY_API_KEY env var.")
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    def search_skills(self, query: str = "", page: int = 1,
                      page_size: int = 10, category: str = "") -> dict:
        """
        Search Smithery for skills. Returns {"skills": [...], "pagination": {...}}.
        Each skill has: id, namespace, slug, displayName, description,
        qualityScore, verified, categories, totalActivations, upvotes.
        """
        params: dict = {"page": page, "pageSize": page_size}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        try:
            r = requests.get(f"{self.BASE}/skills",
                             headers=self._headers(), params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except ValueError as exc:
            return {"error": str(exc), "skills": []}
        except Exception as exc:
            return {"error": str(exc), "skills": []}

    def get_skill(self, namespace: str, slug: str) -> dict:
        """
        Fetch full details for a specific skill, including the 'prompt' field
        which is the actual skill instruction content.
        """
        try:
            r = requests.get(f"{self.BASE}/skills/{namespace}/{slug}",
                             headers=self._headers(), timeout=10)
            r.raise_for_status()
            return r.json()
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            return {"error": str(exc)}

    def install_skill(self, namespace: str, slug: str,
                      skill_manager: Optional[Any] = None) -> dict:
        """
        Fetch a skill from Smithery and save it locally as a SKILL.md file.

        The Smithery skill's 'prompt' field becomes the SKILL.md body.
        Returns {"skill_name": ..., "path": ..., "already_existed": bool}.
        """
        data = self.get_skill(namespace, slug)
        if "error" in data:
            return {"error": data["error"]}

        # Build a clean SKILL.md from the Smithery skill data
        display_name = data.get("displayName", slug)
        description  = data.get("description", "")
        prompt       = data.get("prompt", "")
        categories   = ", ".join(data.get("categories", []))
        quality      = data.get("qualityScore", 0)
        verified     = data.get("verified", False)
        git_url      = data.get("gitUrl", "")
        servers      = data.get("servers", [])

        skill_md = f"""---
description: {description}
source: smithery/{namespace}/{slug}
categories: {categories}
quality_score: {quality}
verified: {verified}
---

# {display_name}

{prompt}
"""
        if git_url:
            skill_md += f"\n## Source\nGitHub: {git_url}\n"
        if servers:
            skill_md += f"\n## Related MCP Servers\n" + "\n".join(f"- {s}" for s in servers) + "\n"

        # Use provided skill_manager or create one
        if skill_manager is None:
            from core.self_extend.skill_manager import SkillManager
            skill_manager = SkillManager()

        # Use slug as local folder name (safe filesystem name)
        skill_name = f"{namespace}__{slug}".replace("/", "_").replace("-", "_")
        already_existed = skill_manager.skill_exists(skill_name)
        path = skill_manager.create_skill(skill_name, skill_md)

        return {
            "skill_name": skill_name,
            "display_name": display_name,
            "path": path,
            "already_existed": already_existed,
        }


# ──────────────────────────────────────────────────────────────────────────────
# MCP CONFIG MANAGER
# ──────────────────────────────────────────────────────────────────────────────

class MCPConfigManager:
    """Read and write MCP server entries into Claude's config files."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or get_claude_desktop_config_path()

    def read_config(self) -> dict:
        if not self.config_path.exists():
            return {"mcpServers": {}}
        try:
            return json.loads(self.config_path.read_text())
        except json.JSONDecodeError:
            return {"mcpServers": {}}

    def write_config(self, config: dict) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config, indent=2))

    def list_servers(self) -> dict:
        return self.read_config().get("mcpServers", {})

    def add_server(self, name: str, server_config: dict) -> str:
        config = self.read_config()
        config.setdefault("mcpServers", {})[name] = server_config
        self.write_config(config)
        return f"✅  Added MCP server '{name}' → {self.config_path}"

    def remove_server(self, name: str) -> str:
        config = self.read_config()
        servers = config.get("mcpServers", {})
        if name in servers:
            del servers[name]
            config["mcpServers"] = servers
            self.write_config(config)
            return f"🗑️  Removed MCP server '{name}'"
        return f"⚠️  Server '{name}' not found in config"

    def server_exists(self, name: str) -> bool:
        return name in self.list_servers()

    def add_server_to_project(self, name: str, server_config: dict,
                              project_dir: Optional[Path] = None) -> str:
        """Add server to .mcp.json in the given project directory."""
        path = (project_dir or Path.cwd()) / ".mcp.json"
        try:
            existing = json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            existing = {}
        existing.setdefault("mcpServers", {})[name] = server_config
        path.write_text(json.dumps(existing, indent=2))
        return f"✅  Added '{name}' to {path}"


# ──────────────────────────────────────────────────────────────────────────────
# MCP SERVER CREATOR
# ──────────────────────────────────────────────────────────────────────────────

class MCPServerCreator:
    """Generate, save, and register custom Python MCP servers using FastMCP."""

    def __init__(self, servers_root: Optional[Path] = None,
                 config_manager: Optional[MCPConfigManager] = None):
        self.root   = servers_root or get_mcp_servers_root()
        self.config = config_manager or MCPConfigManager()

    def generate_server_code(self, server_name: str, description: str,
                             tools: list[dict]) -> str:
        """
        Generate a self-contained FastMCP Python server.
        tools: [{"name": str, "description": str, "params": [...], "impl": str}]
        """
        tool_blocks = []
        for t in tools:
            params_str = ", ".join(
                f"{p['name']}: {p.get('type', 'str')}"
                for p in t.get("params", [])
            )
            impl = textwrap.indent(t.get("impl", 'return "Not implemented"'), "    ")
            tool_blocks.append(
                f'@mcp.tool(description="{t["description"]}")\n'
                f"def {t['name']}({params_str}) -> str:\n{impl}\n"
            )

        tools_code = "\n\n".join(tool_blocks)
        timestamp = datetime.now().isoformat()

        return f'''\
#!/usr/bin/env python3
"""
{server_name} — Auto-generated MCP Server (Kiki Self-Extend)
Generated: {timestamp}
Description: {description}

Install deps:  pip install fastmcp
Run:           python {server_name}.py
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("{server_name}")

# ── Tools ────────────────────────────────────────────────────────────────────

{tools_code}

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
'''

    def save_server(self, server_name: str, code: str) -> Path:
        """Write server code to disk and return its path."""
        path = self.root / f"{server_name}.py"
        path.write_text(code)
        return path

    def create_and_register(self, server_name: str, description: str,
                            tools: list[dict],
                            extra_env: Optional[dict] = None) -> dict:
        """
        Full pipeline: generate code → save → register in Claude config.
        Returns {"code_path": ..., "config_status": ...}
        """
        code      = self.generate_server_code(server_name, description, tools)
        code_path = self.save_server(server_name, code)

        server_cfg: dict[str, Any] = {
            "command": sys.executable,
            "args": [str(code_path)],
        }
        if extra_env:
            server_cfg["env"] = extra_env

        status = self.config.add_server(server_name, server_cfg)
        return {"code_path": str(code_path), "config_status": status}
