#!/usr/bin/env python3
"""Tune automatic memory recall against your own questions.

Automatic recall fires when a question names something the knowledge base has
stored. Whether that is *right* depends entirely on what your knowledge base
happens to hold, so the only honest way to set `auto_recall.min_anchor_idf` is
to run your own questions through it.

Usage:

    # See what fires today, using a built-in sample
    ~/Kiki/kiki/bin/python scripts/calibrate_auto_recall.py

    # Score your own questions: one per line, prefix with - for "should NOT fire"
    ~/Kiki/kiki/bin/python scripts/calibrate_auto_recall.py my_queries.txt

    # Sweep the rarity bar to see the precision/recall trade-off
    ~/Kiki/kiki/bin/python scripts/calibrate_auto_recall.py my_queries.txt --sweep

A query file looks like this -- unprefixed lines are things you HAVE discussed
and want recalled, `-` lines are ordinary questions that must stay untouched:

    how is my project going
    what did Yash say about the internship
    - play some music
    - what is the capital of France

The number that matters is the false-fire count. A missed recall costs one
sentence ("we talked about this before"); a wrong injection drags an unrelated
conversation toward the wrong topic, which on the local box it will happily do.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.brain.auto_recall import EntityIndex, _token_idf  # noqa: E402  # noqa
from core.brain.memory_search import _tokens, get_memory_searcher  # noqa: E402

SAMPLE = [
    ("how is my discrete structures going", True),
    ("what about Moksha", True),
    ("tell me about Yash Gupta", True),
    ("whats up with Ansh", True),
    ("how is Priya doing", True),
    ("play some music", False),
    ("what time is it", False),
    ("tell me a joke", False),
    ("whats the weather", False),
    ("can you move forward", False),
    ("what is the capital of France", False),
    ("remind me to take my tablet", False),
]


def load(path: Path):
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            rows.append((line[1:].strip(), False))
        else:
            rows.append((line, True))
    return rows


def evaluate(index: EntityIndex, rows, min_idf: float):
    caught = fired = positives = negatives = 0
    detail = []
    for question, should in rows:
        anchors = index.find_anchors(question, min_idf=min_idf)
        hit = bool(anchors)
        if should:
            positives += 1
            caught += hit
        else:
            negatives += 1
            fired += hit
        detail.append((question, should, anchors))
    return caught, positives, fired, negatives, detail


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sweep = "--sweep" in sys.argv

    rows = load(Path(args[0])) if args else SAMPLE
    # Rarity comes from the search index, which reports nothing useful until it
    # is built -- without this every single-token anchor would look like a miss.
    print("building the memory index ...")
    get_memory_searcher()._rebuild_if_needed()

    index = EntityIndex()
    print(f"knowledge-base entities indexed: {index.size()}")
    print(f"questions: {len(rows)}\n")

    if sweep:
        print(f"{'min_idf':>8}  {'recalled':>12}  {'false fires':>12}")
        for bar in (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0):
            caught, pos, fired, neg, _ = evaluate(index, rows, bar)
            flag = "  <-- shipped default" if abs(bar - 5.5) < 1e-9 else ""
            print(f"{bar:>8.1f}  {caught:>5}/{pos:<6}  {fired:>5}/{neg:<6}{flag}")
        print("\nPick the largest bar that still recalls what you care about. "
              "Set it as auto_recall.min_anchor_idf in tools_and_config/config.json.")
        return 0

    from tools_and_config.config_loader import get_full_config
    bar = float(get_full_config().get("auto_recall", {}).get("min_anchor_idf", 5.5))
    caught, pos, fired, neg, detail = evaluate(index, rows, bar)

    for question, should, anchors in detail:
        want = "recall" if should else "ignore"
        got = "FIRED " if anchors else "quiet "
        ok = "ok " if bool(anchors) == should else "MISS" if should else "FALSE"
        names = ", ".join(anchors[:3]) if anchors else "-"
        print(f"[{ok:<5}] want={want:<6} {got} {question[:44]:<46} {names[:46]}")

    print(f"\nat min_anchor_idf={bar}:  recalled {caught}/{pos}   "
          f"false fires {fired}/{neg}")
    if fired:
        print("Raise min_anchor_idf to cut false fires. Check which single token "
              "caused each one:")
        for question, should, anchors in detail:
            if should or not anchors:
                continue
            for token in dict.fromkeys(_tokens(question, remove_stop_words=True)):
                idf = _token_idf(token)
                if idf is not None and idf >= bar:
                    print(f"    {question[:40]:<42} '{token}' idf={idf:.2f}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
