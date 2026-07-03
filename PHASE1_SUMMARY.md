# Phase 1 (Wednesday) — What Was Built

**Project:** Fork of Anki for **USMLE Step 1** — a desktop app + an Android
companion that **share one Rust engine**. This build is the Phase 1 / Wednesday
milestone: *both apps review the same deck, with honest scores and a real engine
change.* **No AI is in this build** (AI is Friday).

- **Exam:** USMLE Step 1 (pass/fail) — no invented numeric score.
- **License:** AGPL-3.0-or-later, crediting Anki (BSD-3-Clause parts noted).
- **Deck:** AnKing V11 (`AnKing V11 updated.apkg`).
- **Repos:** `Ankimprovement/` (desktop fork), `Ankimprovement-Android/`
  (AnkiDroid fork), shared engine in `Ankimprovement/rslib/`.

---

## 1. Rust engine change — Mastery query

A new backend call that returns, **per Step 1 topic**, the number of cards
**mastered** (mature, interval ≥ 21d) and the **average FSRS recall**, fast
enough to power the dashboard on 50,000 cards. It is exposed as a protobuf RPC
(`StatsService.MasteryByTopic`) and called from Python as
`col.mastery_by_topic(...)`.

**Where it lives:**
- `rslib/src/stats/mastery.rs` — new file, all query logic + tests.
- `rslib/src/stats/mod.rs`, `rslib/src/stats/service.rs` — wiring.
- `proto/anki/stats.proto` — the new RPC + messages.
- `pylib/anki/collection.py` — the `mastery_by_topic` wrapper.

**Tests:** 3+ Rust unit tests in `mastery.rs` + 1 end-to-end Python test in
`pylib/tests/test_stats.py::test_mastery_by_topic`. Run:
`cargo test -p anki --lib stats::mastery`.

**One-page "why Rust, not Python" note (already written):**
`Ankimprovement/docs/mastery-query.md` — covers rationale (FSRS lives in Rust;
performance on 50k cards; one engine → both apps; correctness), the list of
upstream files touched with merge-risk ratings, and the test list.

### Undo & collection safety (and how to undo in the app)
- The mastery query is **read-only**: it performs no `transact`, writes nothing,
  and adds **no undo entry**, so it cannot corrupt the collection or disturb
  undo/redo (proven by the `query_is_read_only_and_undo_safe` test).
- The **Admin / simulation** operations (below) that *do* change card state are
  **undoable**. To undo in the app: **Edit → Undo**, or **⌘Z** on macOS
  (Ctrl+Z elsewhere). Each simulation op is a single undo step.

---

## 2. The three honest scores + give-up rule

The dashboard always renders **three separate score cards** (never blended):

- **Memory** — mean current FSRS recall over studied cards, with a range.
- **Performance** — honest **"Not measured yet"** placeholder (needs held-out
  exam-style questions; that's Sunday). Deliberately kept separate from Memory.
- **Readiness** — **probability of *passing*** (explicitly *not* a predicted
  score), with a range, coverage %, reasons, and last-updated.

**Give-up rule (enforced from Wednesday):** no readiness number until the
student has **≥ 200 graded reviews AND ≥ 50% Step 1 outline coverage**. Below
either line the app **abstains** and shows "not enough data yet" + what's
missing.

**Where it lives:** `rslib/src/stats/readiness.rs`, `qt/aqt/readiness.py`,
`ts/routes/readiness/+page.svelte` (desktop UI); Android:
`AnkiDroid/.../pages/ReadinessPage.kt`.

---

## 3. Lowering over-confidence — the 5-day / 10-day "no-study" horizons

This is the core honesty mechanic and a good thing to narrate in the demo.

**Problem it solves:** readiness computed for "an exam today" uses each card's
**current retrievability R**. A **crammed** card has high R *right now* but low
**stability S**, so a today-only number makes fragile, about-to-be-forgotten
knowledge look solid. That is exactly the over-confidence trap.

**Mechanic:** alongside "today", the dashboard also computes each score **as if
the exam were N days away and the student does no reviewing in between**. It
projects every card's recall forward with the FSRS retrievability formula:

```
R(t, S) = (1 + FACTOR · t / S) ^ (-DECAY)      # project with t = elapsed + N·days
```

Because the decay is governed by **stability S**, **low-stability (crammed)
cards fall off fastest**. We then re-aggregate (coverage-weighted, unseen = 0)
into the pass probability at each horizon.

**Default horizons:** **today (0d) · +5 days · +10 days**, all "no study."

**What the numbers mean:**
- The **gap between "today" and "+10 days"** is a direct, honest signal of how
  **fragile** today's readiness is. A large drop = the student *feels* ready but
  is leaning on recall that won't last → durable learning still needed.
- The **same today / +5d / +10d columns appear on all three cards** so the
  structure is identical: **Memory** shows its real recall decaying over the
  horizons (ungated, over studied cards); **Readiness** shows pass probability
  per horizon; **Performance** shows the slots as "not measured yet."

**Why it's honest, not a gimmick:** it satisfies the honesty rules (range +
reasons), and it turns FSRS's built-in **S (stability) vs. R (retrievability)**
distinction into a concrete teaching moment about **durable vs. crammed**
knowledge — no invented math.

### Pass-probability mapping (v1, Wednesday-honest)
Readiness first estimates **expected correctness** (coverage-weighted mean
projected recall), then maps it to a **pass probability** through a v1
calibration curve anchored to reported practice-exam outcomes (pass mark = 60%
correct): e.g. 70%→99%, 65%→95%, **60%→~65%** (deliberately below a naive line,
because right at the cut score pass/fail is near a coin flip; the range also
**widens** near 60%). Sunday refines this against measured Performance.

---

## 4. Admin / simulation mode (dev tooling, NOT scoring)

To exercise the dashboard (give-up rule, recall decay, +5/+10d projection)
without weeks of real reviews: **Tools → "Admin: simulation mode"** (off by
default). It can bulk-set FSRS state (S/D/target-R, optionally on a random N %),
reset a random % of cards to "not learned," and simulate advancing N days with
no study. All operations run in **Rust**, are **undoable (⌘Z)**, and are
explicitly **not part of the honest scoring path** — state this on camera.

**Where it lives:** `rslib/src/stats/admin.rs`, `qt/aqt/admin.py`; Android:
`AnkiDroid/.../pages/AdminSimulationFragment.kt`.

---

## 5. Learning-science features (feature-only for Wednesday; proof is Sunday)

- **Manual mode toggle** — long-term-learning ↔ short-term-performance, shipped
  as a core setting (SPOV1). Near-exam *recommendation* notice is Sunday; we
  never auto-disable.
- **Forced UI randomization** (SPOV2) — bounded, **difficulty-gated** (only
  randomizes low-FSRS-difficulty/easy cards; off on hard cards), with an on/off
  switch so it can be ablated Sunday. Android font side:
  `AnkiDroid/.../cardviewer/UsmleFontChooser.kt`,
  `cardviewer/AndroidCardRenderContext.kt`.
- **Coverage map v1** — % of the Step 1 outline the deck covers; feeds the
  give-up rule.

---

## 6. Desktop app

- **Review loop** on the Step 1 deck (standard Anki review through the shared
  engine).
- Dashboard with the three score cards + horizons + give-up rule (above).
- **Installer** that runs on a clean machine (Wednesday hard-limit item).

## 7. Mobile app (Android / AnkiDroid)

- Builds and runs on the **Android emulator** (AVD `usmle_step1`); build/emulator
  steps in `Ankimprovement-Android/docs/development/build-and-emulator-setup.md`.
- Loads the Step 1 deck and runs a **real review session on the shared Rust
  engine** — this is the only Wednesday mobile requirement.
- **Bonus, done early:** two-way local sync (a Friday item) already works via a
  self-hosted server. Procedure + gotchas:
  `Ankimprovement-Android/docs/development/local-sync-setup.md`.
- **Media policy for demos:** the emulator is set to **not fetch media**
  (`syncFetchMedia=never`) and its media was cleared to save space, so
  image-heavy cards show "failed to load image". Review text-heavy cards on
  camera, or say one line explaining media is intentionally not synced.

---

## 8. Proof artifacts to capture (Wednesday)

- Commit hash (`git rev-parse HEAD`) + a clean-build-from-source recording.
- Rust + Python test results (green).
- Clean-machine installer recording.
- Screen recording of a phone review session on the shared deck.

---

## 9. Explicitly NOT in this build (by design — later phases)

- **AI** of any kind (card-gen / rephrasing) — Friday.
- **Performance model** + paraphrase-gap test — Sunday.
- **Calibrated** Memory (Brier/log-loss) and the fitted readiness curve — Sunday.
- **UI-randomization 3-build ablation** — Sunday (the feature exists now; the
  proof is later).

*Anchored to `PRD_Week2.md` (Phase 1 contract) and the Speedrun spec
(Wednesday: "Both apps work and review the same deck. No AI.").*
