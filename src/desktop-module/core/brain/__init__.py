"""
Brain Module
============

Kiki's deeper thinking system — unified idle mind, knowledge base,
conversation summaries, and multi-provider LLM routing.

All operations run asynchronously and never block the fast voice pipeline.
"""

from core.brain.knowledge_base import get_knowledge_base, get_knowledge_summary, save_knowledge_base

__all__ = [
    "get_knowledge_base",
    "get_knowledge_summary",
    "save_knowledge_base",
]
