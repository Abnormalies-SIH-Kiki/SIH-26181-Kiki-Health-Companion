"""
Token Counter
=============

Provides token counting for chat context management.
Uses tiktoken when available, falls back to character-based estimation.

Ported from KIKI-SMART — standalone, no LiveKit dependencies.
"""

import os
from pathlib import Path
from typing import Any, List, Dict, Optional


# ============================================================================
# Module Initialization
# ============================================================================

# tiktoken downloads its BPE file on first use and caches it in /tmp by
# default (lost on reboot). Point it at a persistent dir so one successful
# download survives reboots and the Pi can count tokens fully offline.
os.environ.setdefault(
    "TIKTOKEN_CACHE_DIR",
    str(Path(__file__).resolve().parent.parent.parent / ".tiktoken_cache"),
)

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False
    print("[TokenCounter] tiktoken not available, using character-based estimation")


# ============================================================================
# Constants
# ============================================================================

DEFAULT_MODEL = "gpt-4"
CHARS_PER_TOKEN_ESTIMATE = 4  # Approximate for English text

SERVER_CONTEXT_LIMIT = 7168  # llama.cpp -c 7000 rounded up by server
SAFE_CONTEXT_LIMIT = 6200    # leave room for max_tokens generation (~1000)


# ============================================================================
# Public Functions
# ============================================================================

# Per-message token-count cache. The history is append-mostly, so re-tokenizing
# every message each turn is wasted CPU on the Pi — cache by content hash.
_count_cache: Dict[int, int] = {}
_COUNT_CACHE_MAX = 2048


def count_tokens(messages: List[Dict[str, str]], model: str = DEFAULT_MODEL) -> int:
    """
    Count tokens in chat message history.

    Works with KikiFast's message format: [{"role": "...", "content": "..."}]
    Uses tiktoken if available, otherwise character-based estimation.
    Per-message results are cached (history is append-mostly).
    """
    try:
        total = 0

        for msg in messages:
            text_content = _extract_text(msg)
            key = hash((model, text_content))
            cached = _count_cache.get(key)
            if cached is None:
                cached = _count_with_tiktoken(text_content, model)
                if len(_count_cache) > _COUNT_CACHE_MAX:
                    _count_cache.clear()
                _count_cache[key] = cached
            total += cached

        return total

    except Exception as e:
        # NEVER return 0 here: would_exceed_context would read it as "context
        # fine" and auto-summarize would stop firing while the real context
        # grows past the box's 7k limit (400 errors). Estimate instead.
        print(f"[TokenCounter] Error: {e} — using char/4 estimation")
        try:
            return sum(_count_with_estimation(_extract_text(m)) for m in messages)
        except Exception:
            return 0


def is_tiktoken_available() -> bool:
    """Check if tiktoken is available for accurate token counting."""
    return TIKTOKEN_AVAILABLE


def would_exceed_context(messages, model=DEFAULT_MODEL, limit=SAFE_CONTEXT_LIMIT) -> tuple[bool, int]:
    """Check if messages would exceed the safe context limit.
    Returns (would_exceed: bool, current_count: int)."""
    count = count_tokens(messages, model)
    return count > limit, count


# ============================================================================
# Private Helper Functions
# ============================================================================

def _extract_text(msg: Any) -> str:
    """
    Extract text content from a chat message.
    
    Supports both dict format {"role": ..., "content": ...} 
    and object format with .role/.content attributes.
    """
    text_content = ""
    
    # Dict format (KikiFast style)
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, str):
            text_content += content
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    text_content += item
                elif isinstance(item, dict) and "text" in item:
                    text_content += item["text"]
        role = msg.get("role", "")
        text_content += role
    # Object format (LiveKit style — kept for compatibility)
    elif hasattr(msg, 'content'):
        content = msg.content
        if isinstance(content, str):
            text_content += content
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    text_content += item
                elif hasattr(item, 'text'):
                    text_content += item.text
        if hasattr(msg, 'role'):
            text_content += msg.role
    
    return text_content


# Encoding loaded ONCE. On the Pi the first load may need a network download
# (then cached in TIKTOKEN_CACHE_DIR); if it fails — offline, unknown model —
# we mark tiktoken unusable for the session instead of retrying the download
# (and paying a DNS timeout) on every single turn.
_encoding = None
_tiktoken_failed = not TIKTOKEN_AVAILABLE


def _get_encoding(model: str) -> Optional["tiktoken.Encoding"]:
    global _encoding, _tiktoken_failed
    if _encoding is not None or _tiktoken_failed:
        return _encoding
    try:
        try:
            _encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            _encoding = tiktoken.get_encoding("cl100k_base")
    except Exception as e:
        _tiktoken_failed = True
        print(f"[TokenCounter] tiktoken encoding unavailable ({e}) — "
              f"using char/4 estimation for the rest of this session")
    return _encoding


def _count_with_tiktoken(text: str, model: str) -> int:
    """Count tokens using tiktoken; falls back to estimation when unavailable."""
    enc = _get_encoding(model)
    if enc is None:
        return _count_with_estimation(text)
    return len(enc.encode(text, disallowed_special=()))


def _count_with_estimation(text: str) -> int:
    """Estimate token count using character-based heuristic."""
    return len(text) // CHARS_PER_TOKEN_ESTIMATE
