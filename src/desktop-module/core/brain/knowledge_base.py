"""
Knowledge Base Module
=====================

A hierarchical knowledge base for the KIKI AI assistant to store
experiences, learnings, information about people, environments, and develop personality.

Ported from KIKI-SMART — standalone, no LiveKit dependencies.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Union

from tools_and_config.config_loader import get_full_config


# ============================================================================
# Configuration
# ============================================================================

def _get_default_kb_path() -> Path:
    """Get default knowledge base file path."""
    return Path(__file__).resolve().parents[2] / "state" / "knowledge_base.json"


def get_kb_file_path() -> Path:
    """Get the configured knowledge base file path."""
    config = get_full_config()
    kb_config = config.get("knowledge_base", {})
    return Path(kb_config.get("file_path", str(_get_default_kb_path())))


def get_kb_config() -> Dict[str, Any]:
    """Get the full knowledge base configuration."""
    config = get_full_config()
    return config.get("knowledge_base", {})


def get_person_attributes() -> List[str]:
    """Get configured person attributes from config."""
    kb_config = get_kb_config()
    return kb_config.get("person_attributes", [
        "appearance", "character", "routine", "interests",
        "hobbies", "current_ongoing", "notes_list"
    ])


# ============================================================================
# Knowledge Base Class
# ============================================================================

class KnowledgeBase:
    """
    Hierarchical knowledge base for persistent AI memory.

    Categories:
        - people: Information about individuals (includes self "Kiki")
        - environments: Different locations the robot visits
        - learnings: Things the AI has learned
        - experiences: Past events and their outcomes
        - facts: Known facts about the world
        - personality: Developed personality traits (Kiki's own)
    """

    DEFAULT_STRUCTURE = {
        "people": {
            "Kiki": {
                "first_seen": None,
                "last_seen": None,
                "relationship": "self",
                "notes": "I am Kiki, a witty robot companion and genuine friend!",
                "character": "sarcastic",
                "notes_list": []
            }
        },
        "environments": {},
        "learnings": {},
        "experiences": [],
        "experiences_archive": [],
        "facts": {},
        "personality": {
            "developed_traits": ["sarcastic", "witty"],
            "interaction_preferences": {}
        },
        "metadata": {
            "created": None,
            "last_updated": None,
            "version": "2.0"
        }
    }

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or get_kb_file_path()
        self.data = self._load()
        self._migrate_if_needed()
        # Rotates which OLDER note surfaces in get_summary() each call, so over
        # time every stored note about a person gets injected — the working
        # summary stays small while nothing is permanently dropped from view.
        self._summary_calls = 0

    def _load(self) -> Dict[str, Any]:
        """Load knowledge base from file or create new."""
        if self.file_path.exists():
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                print(f"[KnowledgeBase] Loaded from {self.file_path}")
                return data
            except (json.JSONDecodeError, IOError) as e:
                print(f"[KnowledgeBase] Error loading file: {e}. Creating new.")

        data = json.loads(json.dumps(self.DEFAULT_STRUCTURE))
        data["metadata"]["created"] = datetime.now().isoformat()
        data["people"]["Kiki"]["first_seen"] = datetime.now().isoformat()
        return data

    def _migrate_if_needed(self) -> None:
        """Migrate older knowledge base structures to current version."""
        for key, default_val in self.DEFAULT_STRUCTURE.items():
            if key not in self.data:
                if isinstance(default_val, dict):
                    self.data[key] = default_val.copy()
                elif isinstance(default_val, list):
                    self.data[key] = []
                else:
                    self.data[key] = default_val

        if "environments" not in self.data:
            self.data["environments"] = {}

        if "Kiki" not in self.data["people"]:
            self.data["people"]["Kiki"] = {
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "relationship": "self",
                "notes": "I am Kiki, a witty robot assistant!",
                "character": "sarcastic",
                "notes_list": []
            }

        if self.data.get("metadata", {}).get("version") != "2.0":
            self.data["metadata"]["version"] = "2.0"
            print("[KnowledgeBase] Migrated to version 2.0")

    def save(self) -> bool:
        """Save knowledge base to file."""
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self.data["metadata"]["last_updated"] = datetime.now().isoformat()

            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)

            print(f"[KnowledgeBase] Saved to {self.file_path}")
            return True
        except Exception as e:
            print(f"[KnowledgeBase] Error saving: {e}")
            return False

    # ========================================================================
    # Environment Management
    # ========================================================================

    def add_environment(self, name: str, description: Optional[str] = None,
                        details: Optional[Dict[str, str]] = None, **extra) -> Dict[str, Any]:
        if name not in self.data["environments"]:
            self.data["environments"][name] = {
                "first_visited": datetime.now().isoformat(),
                "last_visited": datetime.now().isoformat()
            }
        env = self.data["environments"][name]
        env["last_visited"] = datetime.now().isoformat()
        if description:
            env["description"] = description
        if details:
            if "details" not in env:
                env["details"] = {}
            env["details"].update(details)
        env.update(extra)
        return env

    def get_environment(self, name: str) -> Optional[Dict[str, Any]]:
        return self.data["environments"].get(name)

    def update_environment(self, name: str, key: str, value: Any) -> bool:
        if name in self.data["environments"]:
            self.data["environments"][name][key] = value
            self.data["environments"][name]["last_visited"] = datetime.now().isoformat()
            return True
        return False

    def remove_environment(self, name: str) -> bool:
        if name in self.data["environments"]:
            del self.data["environments"][name]
            return True
        return False

    # ========================================================================
    # People Management
    # ========================================================================

    def add_person(self, name: str, relationship: Optional[str] = None,
                   traits: Optional[List[str]] = None, notes: Optional[str] = None,
                   appearance: Optional[str] = None, character: Optional[str] = None,
                   **extra) -> Dict[str, Any]:
        if name not in self.data["people"]:
            self.data["people"][name] = {
                "first_seen": datetime.now().isoformat(),
                "last_seen": datetime.now().isoformat(),
                "notes_list": []
            }
        person = self.data["people"][name]
        person["last_seen"] = datetime.now().isoformat()

        if relationship:
            person["relationship"] = relationship
        if traits:
            existing = person.get("traits", [])
            person["traits"] = list(set(existing + traits))
        if notes:
            if "notes_list" not in person:
                person["notes_list"] = []
            if notes not in person["notes_list"]:
                person["notes_list"].append(notes)
            person["notes"] = notes
        if appearance:
            person["appearance"] = appearance
        if character:
            person["character"] = character

        for key, value in extra.items():
            if key in ["routine", "interests", "hobbies"]:
                if key not in person:
                    person[key] = []
                if isinstance(value, list):
                    for item in value:
                        if item not in person[key]:
                            person[key].append(item)
                elif value not in person[key]:
                    person[key].append(value)
            else:
                person[key] = value

        return person

    def add_person_attribute(self, name: str, attribute: str, value: Any,
                             append: bool = False) -> bool:
        if name not in self.data["people"]:
            self.add_person(name)
        person = self.data["people"][name]
        person["last_seen"] = datetime.now().isoformat()
        list_attributes = ["routine", "interests", "hobbies", "notes_list"]
        if attribute in list_attributes:
            if attribute not in person:
                person[attribute] = []
            if append or attribute == "notes_list":
                if isinstance(value, list):
                    for item in value:
                        if item not in person[attribute]:
                            person[attribute].append(item)
                elif value not in person[attribute]:
                    person[attribute].append(value)
            else:
                person[attribute] = value if isinstance(value, list) else [value]
        else:
            person[attribute] = value
            if attribute == "current_ongoing":
                # Timestamp so get_summary can age it out — a months-old
                # "currently working on X" kept surfacing as if it were today.
                person["current_ongoing_updated"] = datetime.now().isoformat()
        return True

    def get_person_attribute(self, name: str, attribute: str) -> Optional[Any]:
        person = self.data["people"].get(name)
        if person:
            return person.get(attribute)
        return None

    def add_note_to_person(self, name: str, note: str) -> bool:
        return self.add_person_attribute(name, "notes_list", note, append=True)

    def set_person_character(self, name: str, character: str) -> bool:
        return self.add_person_attribute(name, "character", character)

    def add_routine_item(self, name: str, routine_item: str) -> bool:
        return self.add_person_attribute(name, "routine", routine_item, append=True)

    def add_interest(self, name: str, interest: str) -> bool:
        return self.add_person_attribute(name, "interests", interest, append=True)

    def set_current_ongoing(self, name: str, ongoing: str) -> bool:
        return self.add_person_attribute(name, "current_ongoing", ongoing)

    def get_person(self, name: str) -> Optional[Dict[str, Any]]:
        return self.data["people"].get(name)

    def remove_person(self, name: str) -> bool:
        if name in self.data["people"] and name != "Kiki":
            del self.data["people"][name]
            return True
        return False

    # ========================================================================
    # Self Management
    # ========================================================================

    def update_self(self, **attributes) -> Dict[str, Any]:
        return self.add_person("Kiki", **attributes)

    def add_self_note(self, note: str) -> bool:
        return self.add_note_to_person("Kiki", note)

    def set_self_character(self, character: str) -> bool:
        return self.set_person_character("Kiki", character)

    # ========================================================================
    # Learnings Management
    # ========================================================================

    def add_learning(self, category: str, learning: str) -> None:
        if category not in self.data["learnings"]:
            self.data["learnings"][category] = []
        if learning not in self.data["learnings"][category]:
            self.data["learnings"][category].append(learning)

    def get_learnings(self, category: Optional[str] = None) -> Union[Dict, List]:
        if category:
            return self.data["learnings"].get(category, [])
        return self.data["learnings"]

    # ========================================================================
    # Experiences Management
    # ========================================================================

    def add_experience(self, event: str, outcome: str = "neutral",
                       details: Optional[str] = None) -> Dict[str, Any]:
        experience = {
            "date": datetime.now().isoformat(),
            "event": event,
            "outcome": outcome
        }
        if details:
            experience["details"] = details
        self.data["experiences"].append(experience)
        if len(self.data["experiences"]) > 100:
            # Non-destructive: overflow moves to the archive instead of being
            # dropped, so nothing Kiki experienced is ever truly forgotten —
            # it stays retrievable via search()/the recall_memory tool.
            overflow = self.data["experiences"][:-100]
            self.data.setdefault("experiences_archive", []).extend(overflow)
            self.data["experiences"] = self.data["experiences"][-100:]
        return experience

    def get_recent_experiences(self, count: int = 10) -> List[Dict[str, Any]]:
        return self.data["experiences"][-count:]

    # ========================================================================
    # Facts Management
    # ========================================================================

    def add_fact(self, key: str, value: Any) -> None:
        self.data["facts"][key] = value

    def get_fact(self, key: str) -> Optional[Any]:
        return self.data["facts"].get(key)

    def remove_fact(self, key: str) -> bool:
        if key in self.data["facts"]:
            del self.data["facts"][key]
            return True
        return False

    # ========================================================================
    # Personality Management
    # ========================================================================

    def add_trait(self, trait: str) -> None:
        if trait not in self.data["personality"]["developed_traits"]:
            self.data["personality"]["developed_traits"].append(trait)

    def set_preference(self, key: str, value: Any) -> None:
        self.data["personality"]["interaction_preferences"][key] = value

    def get_personality(self) -> Dict[str, Any]:
        return self.data["personality"]

    # ========================================================================
    # Search & Summary
    # ========================================================================

    def search(self, query: str) -> Dict[str, List[str]]:
        query_lower = query.lower()
        results = {"people": [], "environments": [], "learnings": [], "experiences": [], "facts": []}

        for name, info in self.data["people"].items():
            if query_lower in name.lower() or query_lower in str(info).lower():
                results["people"].append(f"{name}: {info}")

        for name, info in self.data["environments"].items():
            if query_lower in name.lower() or query_lower in str(info).lower():
                results["environments"].append(f"{name}: {info}")

        for cat, items in self.data["learnings"].items():
            for item in items:
                if query_lower in item.lower() or query_lower in cat.lower():
                    results["learnings"].append(f"[{cat}] {item}")

        for exp in self.data["experiences"]:
            if query_lower in exp["event"].lower():
                results["experiences"].append(f"{exp['date']}: {exp['event']} ({exp['outcome']})")

        # Also search the archive so old experiences are never lost to recall.
        for exp in self.data.get("experiences_archive", []):
            if query_lower in exp.get("event", "").lower():
                results["experiences"].append(
                    f"{exp.get('date', '?')}: {exp['event']} ({exp.get('outcome', 'neutral')}) [archived]")

        for key, val in self.data["facts"].items():
            if query_lower in key.lower() or query_lower in str(val).lower():
                results["facts"].append(f"{key}: {val}")

        return {k: v for k, v in results.items() if v}

    def get_summary(self, max_lines: int = 50) -> str:
        lines = []
        self._summary_calls += 1
        rot = self._summary_calls

        # Self (Kiki) summary first
        kiki = self.data["people"].get("Kiki", {})
        if kiki:
            lines.append("=== ABOUT ME (KIKI) ===")
            if kiki.get("character"):
                lines.append(f"Current mood: {kiki['character']}")
            if kiki.get("notes_list"):
                for note in kiki["notes_list"][-3:]:
                    lines.append(f"- {note}")

        # People summary (excluding self)
        other_people = {k: v for k, v in self.data["people"].items() if k != "Kiki"}
        if other_people:
            lines.append("\n=== PEOPLE I KNOW ===")
            for name, info in other_people.items():
                parts = [f"**{name}**"]
                if info.get("relationship"):
                    parts.append(f"({info['relationship']})")
                lines.append(" ".join(parts))
                if info.get("appearance"):
                    lines.append(f"  Appearance: {info['appearance']}")
                if info.get("character"):
                    lines.append(f"  Character: {info['character']}")
                if info.get("traits"):
                    lines.append(f"  Traits: {', '.join(info['traits'])}")
                if info.get("interests"):
                    lines.append(f"  Interests: {', '.join(info['interests'])}")
                if info.get("current_ongoing"):
                    # Age out stale "currently" items: undated or >14 days old
                    # entries are history, not the present (the model treated a
                    # March "English PPT" note as live news in June).
                    ongoing_line = None
                    updated = info.get("current_ongoing_updated")
                    if updated:
                        try:
                            age_days = (datetime.now() - datetime.fromisoformat(updated)).days
                            if age_days <= 14:
                                ongoing_line = f"  Currently: {info['current_ongoing']}"
                            elif age_days <= 60:
                                ongoing_line = (f"  A while back ({age_days} days ago, probably finished): "
                                                f"{info['current_ongoing']}")
                            # >60 days: drop entirely
                        except (ValueError, TypeError):
                            pass
                    if ongoing_line is None and not updated:
                        ongoing_line = f"  At some point (undated, may be old): {info['current_ongoing']}"
                    if ongoing_line:
                        lines.append(ongoing_line)
                if info.get("routine"):
                    lines.append(f"  Routine: {'; '.join(info['routine'][:3])}")
                if info.get("notes_list"):
                    notes = info["notes_list"]
                    recent = notes[-2:]
                    # Surface ONE rotating older note (if any) so the full
                    # history of a person eventually gets recalled, not just the
                    # latest two. The rest stays reachable via recall_memory.
                    older = notes[:-2]
                    if older:
                        pick = older[rot % len(older)]
                        if pick not in recent:
                            lines.append(f"  Note (earlier): {pick}")
                    for note in recent:
                        lines.append(f"  Note: {note}")
                elif info.get("notes"):
                    lines.append(f"  Note: {info['notes']}")

        # Environments
        if self.data["environments"]:
            lines.append("\n=== ENVIRONMENTS I KNOW ===")
            for name, info in self.data["environments"].items():
                parts = [f"**{name}**"]
                if info.get("description"):
                    parts.append(f"- {info['description']}")
                lines.append(" ".join(parts))

        # Learnings
        if self.data["learnings"]:
            lines.append("\n=== THINGS I'VE LEARNED ===")
            for cat, items in self.data["learnings"].items():
                lines.append(f"[{cat}]")
                for item in items[-3:]:
                    lines.append(f"  - {item}")

        # Recent experiences
        recent_exp = self.get_recent_experiences(3)
        if recent_exp:
            lines.append("\n=== RECENT EXPERIENCES ===")
            for exp in recent_exp:
                lines.append(f"- {exp['event']} ({exp['outcome']})")

        # Facts
        if self.data["facts"]:
            lines.append("\n=== KNOWN FACTS ===")
            for key, val in list(self.data["facts"].items())[:5]:
                lines.append(f"- {key}: {val}")

        # Personality
        if self.data["personality"]["developed_traits"]:
            lines.append("\n=== MY PERSONALITY ===")
            lines.append(f"Traits: {', '.join(self.data['personality']['developed_traits'])}")

        result = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            result += f"\n... ({len(lines) - max_lines} more lines)"
        return result


# ============================================================================
# Public Module Functions (Singleton)
# ============================================================================

_kb_instance: Optional[KnowledgeBase] = None


def get_knowledge_base() -> KnowledgeBase:
    """Get the singleton knowledge base instance."""
    global _kb_instance
    if _kb_instance is None:
        _kb_instance = KnowledgeBase()
    return _kb_instance


def reload_knowledge_base() -> KnowledgeBase:
    """Force reload the knowledge base from disk."""
    global _kb_instance
    _kb_instance = KnowledgeBase()
    return _kb_instance


def get_knowledge_summary(max_lines: int = 50) -> Optional[str]:
    """Get the knowledge base summary for context injection."""
    kb = get_knowledge_base()
    summary = kb.get_summary(max_lines)
    return summary if summary.strip() else None


def save_knowledge_base() -> bool:
    """Save the current knowledge base to disk."""
    return get_knowledge_base().save()


async def apply_knowledge_updates(updates: List[Dict[str, str]], source: str = "Memory") -> int:
    """Apply a list of {category, key, attribute, value} updates to the KB.

    Shared by Unified Idle Mind and background workers. `source` is a short
    label used only in terminal logs so you can see
    exactly what each subsystem writes to long-term memory.
    Returns the number of updates applied.
    """
    if not updates:
        return 0

    kb = get_knowledge_base()
    applied_count = 0

    for update in updates:
        try:
            before = applied_count
            category = update.get("category", "")
            key = update.get("key", "")
            value = update.get("value", "")
            attribute = update.get("attribute", "")

            if not value:
                print(f"[{source}→Memory] ⤬ skipped (no value): {update}")
                continue

            if category == "self":
                key = "Kiki"
                category = "people"

            if category == "learnings":
                if key:
                    kb.add_learning(key, value)
                    applied_count += 1
            elif category == "people":
                if not key:
                    continue
                if attribute == "appearance":
                    kb.add_person_attribute(key, "appearance", value)
                elif attribute == "character":
                    kb.set_person_character(key, value)
                elif attribute == "routine":
                    items = [v.strip() for v in value.split(",")]
                    for item in items:
                        kb.add_routine_item(key, item)
                elif attribute in ("interests", "hobbies"):
                    items = [v.strip() for v in value.split(",")]
                    for item in items:
                        kb.add_interest(key, item)
                elif attribute == "current_ongoing":
                    kb.set_current_ongoing(key, value)
                elif attribute == "notes":
                    kb.add_note_to_person(key, value)
                else:
                    kb.add_note_to_person(key, value)
                applied_count += 1
            elif category == "environments":
                if key:
                    kb.add_environment(key, description=value)
                    applied_count += 1
            elif category == "facts":
                if key:
                    kb.add_fact(key, value)
                    applied_count += 1
            elif category == "personality":
                if key:
                    kb.add_trait(key)
                    applied_count += 1
            else:
                print(f"[{source}→Memory] ⤬ skipped (unknown category '{category}'): {update}")

            # Log exactly what landed in long-term memory this iteration.
            if applied_count > before:
                attr = f" [{attribute}]" if attribute else ""
                print(f"[{source}→Memory] ✚ {category}/{key}{attr} = {value}")
        except Exception as e:
            print(f"[{source}→Memory] Error applying update {update}: {e}")

    if applied_count > 0:
        save_knowledge_base()
        print(f"[{source}→Memory] Saved {applied_count} knowledge update(s) to knowledge_base.json")
    else:
        print(f"[{source}→Memory] No knowledge updates applied (nothing new to remember)")
    return applied_count
