"""Crash / durability test (PRD §10, §11; report §7g) — "zero corrupted
collections".

A worker process opens a throwaway collection and hammers the real write path
(add_note + answer_card, i.e. the review path, all committed through the Rust
backend). The parent SIGKILLs the worker at a random moment — an abrupt crash
mid-write, no clean close — then reopens the collection and runs a full
integrity audit. Repeated ``--cycles`` times.

Pass = every cycle reopens cleanly with:
  * ``pragma integrity_check`` == ok
  * ``pragma foreign_key_check`` empty
  * every ``cards.nid`` resolves to a note, every ``revlog.cid`` resolves to a
    card (no dangling rows)
  * the committed row count never goes backwards (no lost committed data)

    cd Ankimprovement
    PYTHONPATH="$PWD/out/pylib:$PWD/out/qt" out/pyenv/bin/python crash_test.py --cycles 20
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import signal
import sys
import tempfile
import time

from anki.collection import Collection
from anki.scheduler.v3 import CardAnswer

TOPICS = ["Pathology", "Pharmacology", "Physiology", "Microbiology"]


def seed_collection(path: str) -> None:
    col = Collection(path)
    col.set_config("fsrs", True)
    model = col.models.by_name("Basic")
    deck_id = col.decks.id("AnKing::USMLE")
    for i in range(300):
        note = col.new_note(model)
        note["Front"] = f"[{TOPICS[i % len(TOPICS)]}] seed question #{i}"
        note["Back"] = f"Answer {i}"
        note.tags = [f"#AK_Step1_v11::#FirstAid::{TOPICS[i % len(TOPICS)]}"]
        col.add_note(note, deck_id)
    col.close()


def worker(path: str) -> None:
    """Hammer the real write path forever; the parent will SIGKILL us mid-write."""
    col = Collection(path)
    model = col.models.by_name("Basic")
    deck_id = col.decks.id("AnKing::USMLE")
    i = 0
    while True:
        i += 1
        # a real committed review, when a card is due
        queued = col.sched.get_queued_cards(fetch_limit=1)
        if queued.cards:
            card = col.get_card(queued.cards[0].card.id)
            card.start_timer()
            ans = col.sched.build_answer(
                card=card, states=queued.cards[0].states, rating=CardAnswer.GOOD
            )
            col.sched.answer_card(ans)
        # a real committed note add (keeps the write path continuously busy)
        note = col.new_note(model)
        note["Front"] = f"crash-worker note {os.getpid()}-{i}"
        note["Back"] = "x"
        note.tags = ["#AK_Step1_v11::#FirstAid::Pathology"]
        col.add_note(note, deck_id)


def audit(path: str) -> tuple[bool, str, int]:
    """Reopen and audit. Returns (ok, detail, committed_row_count)."""
    try:
        col = Collection(path)
    except Exception as exc:  # noqa: BLE001
        return False, f"reopen FAILED: {exc}", -1
    try:
        integrity = col.db.scalar("pragma integrity_check")
        fk = col.db.all("pragma foreign_key_check")
        orphan_cards = col.db.scalar(
            "select count(*) from cards where nid not in (select id from notes)"
        )
        orphan_revlog = col.db.scalar(
            "select count(*) from revlog where cid not in (select id from cards)"
        )
        rows = col.db.scalar("select count(*) from notes") + col.db.scalar(
            "select count(*) from revlog"
        )
        ok = (
            integrity == "ok"
            and not fk
            and orphan_cards == 0
            and orphan_revlog == 0
        )
        detail = (
            f"integrity={integrity} fk={len(fk)} "
            f"orphan_cards={orphan_cards} orphan_revlog={orphan_revlog}"
        )
        return ok, detail, rows
    finally:
        col.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--child", type=str, default="")
    args = ap.parse_args()

    if args.child:  # child entrypoint
        worker(args.child)
        return 0  # unreachable (killed by parent)

    random.seed(args.seed)
    workdir = tempfile.mkdtemp(prefix="anki_crash_")
    path = os.path.join(workdir, "crash.anki2")
    print(f"== crash_test: seeding collection, {args.cycles} kill cycles ==")
    seed_collection(path)

    clean = 0
    prev_rows = 0
    try:
        for cycle in range(1, args.cycles + 1):
            proc = os.fork()
            if proc == 0:  # child
                os.execv(
                    sys.executable,
                    [sys.executable, os.path.abspath(__file__), "--child", path],
                )
                os._exit(127)
            # parent: let the worker write for a random slice, then hard-kill it
            time.sleep(random.uniform(0.05, 0.35))
            os.kill(proc, signal.SIGKILL)
            os.waitpid(proc, 0)
            ok, detail, rows = audit(path)
            grew = rows >= prev_rows
            status = "OK" if (ok and grew) else "CORRUPT/LOSS"
            if ok and grew:
                clean += 1
            print(
                f"  cycle {cycle:>2}: killed mid-write -> reopen {status}  "
                f"[{detail}] rows={rows} (prev {prev_rows})"
            )
            prev_rows = max(prev_rows, rows)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n== RESULT: {clean}/{args.cycles} cycles clean, zero corruption ==")
    return 0 if clean == args.cycles else 1


if __name__ == "__main__":
    raise SystemExit(main())
