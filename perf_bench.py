"""Speed benchmark (PRD §10 / report §7h) — the "one-command benchmark".

Builds a throwaway synthetic collection and reports p50 / p95 / worst latency
for the latency-critical actions (no cherry-picked single number):

  * button press  = ``sched.answer_card`` (grade the shown card)
  * next card     = ``sched.get_queued_cards(fetch_limit=1)``
  * mastery query = ``col.mastery_by_topic`` (the Rust RPC, §7a)
  * dashboard     = ``col.study_dashboard`` (Memory/Performance/Readiness build)

This is a machine benchmark (not a user test): the collection is synthetic but
the code paths, scheduler, FSRS, and Rust aggregation are the real ones both
apps use. Deterministic given ``--seed``.

    cd Ankimprovement
    PYTHONPATH="$PWD/out/pylib:$PWD/out/qt" out/pyenv/bin/python perf_bench.py --cards 50000
"""

from __future__ import annotations

import argparse
import os
import random
import resource
import shutil
import statistics
import sys
import tempfile
import time

from anki.collection import Collection
from anki.scheduler.v3 import CardAnswer

TOPICS = [
    "Biochemistry", "Immunology", "Microbiology", "Pathology", "Pharmacology",
    "Physiology", "Cardiovascular", "Respiratory", "Renal", "GI",
    "Heme_Onc", "Neuro", "MSK", "Endocrine", "Reproductive", "Psychiatry",
]


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def summarize(name: str, samples_ms: list[float], budget_ms: float | None) -> str:
    p50 = statistics.median(samples_ms)
    p95 = pct(samples_ms, 95)
    worst = max(samples_ms)
    verdict = ""
    if budget_ms is not None:
        verdict = "  PASS" if p95 < budget_ms else "  **OVER budget**"
        verdict += f" (budget p95 < {budget_ms:g} ms)"
    return (
        f"{name:<26} n={len(samples_ms):>6}  "
        f"p50={p50:7.2f} ms  p95={p95:7.2f} ms  worst={worst:8.2f} ms{verdict}"
    )


def build_collection(path: str, n_cards: int, seed: int) -> Collection:
    rng = random.Random(seed)
    col = Collection(path)
    col.set_config("fsrs", True)  # this fork always runs FSRS (report §4)
    model = col.models.by_name("Basic")
    deck_id = col.decks.id("AnKing::USMLE")
    col.decks.select(deck_id)
    t0 = time.time()
    for i in range(n_cards):
        note = col.new_note(model)
        topic = TOPICS[i % len(TOPICS)]
        note["Front"] = f"[{topic}] synthetic question #{i}: which drug treats X{i}?"
        note["Back"] = f"Answer {i}"
        note.tags = [f"#AK_Step1_v11::#FirstAid::{topic}"]
        col.add_note(note, deck_id)
        if i and i % 10000 == 0:
            print(f"  built {i}/{n_cards} notes ({time.time()-t0:.1f}s)", flush=True)
    print(f"  built {n_cards} notes in {time.time()-t0:.1f}s", flush=True)
    return col


def bench_answer(col: Collection, reps: int) -> list[float]:
    """Time button presses: fetch the next card and grade it (v3 answer_card)."""
    samples: list[float] = []
    for _ in range(reps):
        queued = col.sched.get_queued_cards(fetch_limit=1)
        if not queued.cards:
            break
        card = col.get_card(queued.cards[0].card.id)
        card.start_timer()
        states = queued.cards[0].states
        rating = random.choice(
            [CardAnswer.GOOD, CardAnswer.GOOD, CardAnswer.GOOD, CardAnswer.EASY]
        )
        answer = col.sched.build_answer(card=card, states=states, rating=rating)
        t = time.perf_counter()
        col.sched.answer_card(answer)
        samples.append((time.perf_counter() - t) * 1000.0)
    return samples


def bench_next_card(col: Collection, reps: int) -> list[float]:
    samples = []
    for _ in range(reps):
        t = time.perf_counter()
        col.sched.get_queued_cards(fetch_limit=1)
        samples.append((time.perf_counter() - t) * 1000.0)
    return samples


def bench_call(fn, reps: int) -> list[float]:
    samples = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t) * 1000.0)
    return samples


def time_once(fn) -> float:
    t = time.perf_counter()
    fn()
    return (time.perf_counter() - t) * 1000.0


def peak_rss_mb() -> float:
    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    return (kb / (1024 * 1024)) if sys.platform == "darwin" else (kb / 1024)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=int, default=50000)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    random.seed(args.seed)

    workdir = tempfile.mkdtemp(prefix="anki_bench_")
    path = os.path.join(workdir, "bench.anki2")
    print(f"== perf_bench: building {args.cards} synthetic cards (seed {args.seed}) ==")
    col = build_collection(path, args.cards, args.seed)
    try:
        # Give ~40% of cards realistic FSRS state + perf via the Rust bulk admin
        # op, so the dashboard/mastery aggregation runs over real memory states.
        col.admin_set_fsrs(
            search="", stability=30.0, difficulty=4.0,
            target_retrievability=0.85, sample_percent=40, performance=60,
        )
        # Dashboard "first load" = the very first build (cold caches).
        first_load = time_once(
            lambda: col.study_dashboard(readiness_horizons_days=[0, 5, 10]))

        print("\n== latencies (real code paths; synthetic data) ==")
        results = [
            summarize("button press (answer)", bench_answer(col, 800), 50.0),
            summarize("next card (get_queued)", bench_next_card(col, 800), 100.0),
            summarize("mastery_by_topic (RPC)",
                      bench_call(lambda: col.mastery_by_topic(topic_depth=3), 40), 1000.0),
            summarize("dashboard refresh (build)",
                      bench_call(lambda: col.study_dashboard(
                          readiness_horizons_days=[0, 5, 10]), 40), 500.0),
        ]
        print("\n".join(results))
        print(f"dashboard first load        : {first_load:7.2f} ms"
              f"       (budget < 1000 ms)  "
              f"{'PASS' if first_load < 1000 else '**OVER**'}")

        # Cold start: close and time a fresh reopen of the 50k collection.
        card_n = col.card_count()
        col.close()
        cold = time_once(lambda: Collection(path).close())
        print(f"cold start (open 50k col)   : {cold:7.2f} ms"
              f"       (budget < 5000 ms)  "
              f"{'PASS' if cold < 5000 else '**OVER**'}")
        print(f"peak memory (RSS)           : {peak_rss_mb():7.1f} MB "
              f"for {card_n} cards")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
