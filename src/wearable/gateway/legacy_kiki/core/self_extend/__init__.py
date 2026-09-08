"""
core/self_extend — Kiki's self-extension package.

Provides skill management, MCP server management, and the
KikiSelfExtendAgent that drives autonomous self-extension tasks using
generate_llm_resp.generate() (no Anthropic API required).
"""

from core.self_extend.skill_manager import SkillManager
from core.self_extend.mcp_manager import (
    MCPRegistryClient,
    MCPConfigManager,
    MCPServerCreator,
    SmitherySkillsClient,
)
from core.self_extend.kiki_self_extend_agent import KikiSelfExtendAgent
import core.self_extend.smithery_cli as smithery_cli

__all__ = [
    "SkillManager",
    "MCPRegistryClient",
    "MCPConfigManager",
    "MCPServerCreator",
    "SmitherySkillsClient",
    "KikiSelfExtendAgent",
    "smithery_cli",
]
