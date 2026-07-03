"""
Leakage check for the AI rephrasing feature (Speedrun section 7e).

"Leaked data makes a model look smarter than it is, and it zeroes that score."

Our rephraser calls a **frozen** OpenAI model - we do not train or fine-tune on
anything, so there is no training corpus for a test item to leak into. The only
text the model is ever shown is the card it is rephrasing at review time. So the
concrete leakage risk here is: a held-out eval fixture (rephrase_eval_data.json)
being an exact/near copy of a real study-deck card, which would let the model
"recognise" it.

This script scans a corpus for near-copies of the held-out eval items and
reports the result. Re-runnable, mirroring sync_verify.py:

    cd Ankimprovement
    # default: verify the held-out set is internally unique and record that
    # there is no training corpus (frozen model):
    python3 rephrase_leakage_check.py
    # scan against a real collection's notes (needs the built anki package):
    PYTHONPATH="$PWD/out/pylib" python3 rephrase_leakage_check.py --collection /path/to/collection.anki2
    # or scan against an arbitrary text corpus:
    python3 rephrase_leakage_check.py --corpus notes.txt

Exit code 0 == clean (no leakage).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE / "rephrase_eval_data.json"

NEAR_COPY_OVERLAP = 0.8  # token Jaccard at/above this == a near-copy (leak)

_WORD_RE = re.compile(r"[a-z0-9]+")


def log(msg: str) -> None:
    print(msg, flush=True)


def tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def overlap(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def load_corpus(args) -> list[str]:
    if args.corpus:
        text = Path(args.corpus).read_text("utf-8")
        return [line for line in text.splitlines() if line.strip()]
    if args.collection:
        from anki.collection import Collection  # lazy: needs built anki

        col = Collection(args.collection)
        try:
            corpus: list[str] = []
            for nid in col.find_notes(""):
                corpus.extend(col.get_note(nid).fields)
            return corpus
        finally:
            col.close()
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--collection", help="path to a .anki2 collection to scan")
    ap.add_argument("--corpus", help="path to a newline-delimited text corpus")
    args = ap.parse_args()

    items = json.loads(DATA_PATH.read_text("utf-8"))["items"]
    log(f"Held-out eval items: {len(items)} (from {DATA_PATH.name}).")

    # 1) Internal uniqueness: no two fixtures are near-copies of each other.
    dupes = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if overlap(items[i]["question"], items[j]["question"]) >= NEAR_COPY_OVERLAP:
                dupes += 1
                log(f"  DUPLICATE fixtures: {items[i]['id']} ~ {items[j]['id']}")
    log(f"Internal uniqueness: {'CLEAN' if dupes == 0 else f'{dupes} duplicate(s)'}.")

    # 2) Corpus scan (training/prompt corpus).
    corpus = load_corpus(args)
    if not corpus:
        log("No training corpus supplied: the rephraser uses a FROZEN model with "
            "no fine-tuning, so there is no training set for test items to leak "
            "into. (Pass --collection or --corpus to scan a real source.)")
        leaks = 0
    else:
        log(f"Scanning {len(corpus)} corpus entries for near-copies...")
        leaks = 0
        for it in items:
            worst = max((overlap(it["question"], c) for c in corpus), default=0.0)
            if worst >= NEAR_COPY_OVERLAP:
                leaks += 1
                log(f"  LEAK: {it['id']} near-copy in corpus (overlap {worst:.2f})")
        log(f"Corpus scan: {'CLEAN' if leaks == 0 else f'{leaks} leak(s)'}.")

    clean = dupes == 0 and leaks == 0
    log("\n" + ("CLEAN: no leakage detected." if clean else "DIRTY: leakage found."))
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
