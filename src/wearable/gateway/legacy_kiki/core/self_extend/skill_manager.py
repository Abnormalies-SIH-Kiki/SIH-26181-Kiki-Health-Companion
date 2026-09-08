"""
core/self_extend/skill_manager.py
===================================
Manages Kiki's local Claude-style skill files (SKILL.md).

Skills are directories under `skills_dir`, each containing a SKILL.md
(optionally extra helper files).  This module is a clean adaptation of the
SkillManager from claude_self_extend.py — no Anthropic dependency.
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from tools_and_config.config_loader import get_full_config


def get_skills_root() -> Path:
    """Return the configured skills directory, creating it if needed."""
    config = get_full_config()
    se_cfg = config.get("self_extend", {})
    default_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "skills")
    path = Path(se_cfg.get("skills_dir", default_dir))
    path.mkdir(parents=True, exist_ok=True)
    return path


class SkillManager:
    """Read, list, search, and create Kiki skills in the local skills directory."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or get_skills_root()

    # ── Read ──────────────────────────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        """Return metadata for every skill found under root."""
        skills = []
        if not self.root.exists():
            return skills
        for entry in sorted(self.root.iterdir()):
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.exists():
                    first_lines = skill_md.read_text(errors="replace")[:400]
                    skills.append({
                        "name": entry.name,
                        "path": str(skill_md),
                        "preview": first_lines.strip(),
                    })
        return skills

    def read_skill(self, name: str) -> Optional[str]:
        """Return full SKILL.md text for a named skill, or None if missing."""
        path = self.root / name / "SKILL.md"
        if path.exists():
            return path.read_text(errors="replace")
        return None

    def skill_exists(self, name: str) -> bool:
        return (self.root / name / "SKILL.md").exists()

    def get_skills_summary(self, max_skills: int = 20) -> str:
        """
        Return a concise summary of all skills for injection into the system prompt.
        """
        skills = self.list_skills()
        if not skills:
            return ""
        lines = [f"## Available Kiki Skills ({len(skills)} installed)"]
        for s in skills[:max_skills]:
            # Extract the description line from SKILL.md frontmatter if present
            preview = s["preview"]
            desc = ""
            for line in preview.split("\n"):
                line = line.strip()
                if line.startswith("description:"):
                    desc = line.replace("description:", "").strip()
                    break
            if not desc:
                # Fallback: use first non-YAML non-empty line
                in_yaml = False
                for line in preview.split("\n"):
                    stripped = line.strip()
                    if stripped == "---":
                        in_yaml = not in_yaml
                        continue
                    if not in_yaml and stripped and not stripped.startswith("#"):
                        desc = stripped[:100]
                        break
            lines.append(f"- **{s['name']}**: {desc}")
        lines.append(
            "\nYou can use self_extend_list_skills, self_extend_create_skill, "
            "or self_extend_run_task to browse, create, or manage skills."
        )
        return "\n".join(lines)

    # ── Write ─────────────────────────────────────────────────────────────────

    def create_skill(self, name: str, skill_md_content: str,
                     extra_files: Optional[dict[str, str]] = None) -> str:
        """
        Create a new skill directory with SKILL.md (and optionally helper files).
        Returns the path of the new skill directory.
        """
        skill_dir = self.root / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(skill_md_content)
        if extra_files:
            for fname, content in extra_files.items():
                (skill_dir / fname).write_text(content)
        return str(skill_dir)

    def delete_skill(self, name: str) -> bool:
        skill_dir = self.root / name
        if skill_dir.exists():
            shutil.rmtree(skill_dir)
            return True
        return False

    def update_skill(self, name: str, skill_md_content: str) -> Optional[str]:
        """Update an existing SKILL.md. Returns path or None if skill doesn't exist."""
        path = self.root / name / "SKILL.md"
        if not path.exists():
            return None
        path.write_text(skill_md_content)
        return str(path)
