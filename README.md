# Ankimprovement — Desktop (USMLE Step 1)

A "speedrun" **USMLE Step 1** study app: a fork of [Anki](https://apps.ankiweb.net)
that shares **one Rust engine** with an [AnkiDroid](https://github.com/ankidroid/Anki-Android)
fork (the Android companion). This is the desktop app.

**Exam — USMLE Step 1:** pass/fail only (no numeric score, since 2022-01-26);
7 blocks × ≤40 MCQs, 60 min each, ~8-hour day; content organized by organ system
and discipline. The app reports **probability of passing** with a range — never an
invented score.

## Status

**Phase 0 — done:** desktop + AnkiDroid both build & run from source on one Rust
engine; a trivial Rust change is verified end-to-end on desktop; exam stated above.

**Phase 1 (Wednesday) — in progress. No AI.**
- [x] Rust **mastery query**: per-topic cards-mastered + avg recall, fast on 50k cards (new protobuf, Rust + Python tests, undo-safe).
- [x] **Study dashboard** with three always-shown scores — **Memory**, **Performance** (placeholder until Phase 3), **Readiness** — each with a range.
- [x] **Memory** score, honest (mean FSRS recall + confidence range).
- [x] **Readiness v1**, honest: **P(pass)** (calibrated to reported practice-exam outcomes, *not* a score) + range + coverage %, abstains under the give-up rule.
- [x] **Over-confidence view**: every score projected forward with no study at **today / +5d / +10d**.
- [x] **Coverage map v1** (% of the Step 1 outline seen).
- [x] **Admin / simulation mode**: bulk-set FSRS state, advance days, reset cards (incl. random %) — dev tooling to exercise the dashboard.
- [x] **Android parity**: the dashboard + admin mode run on the AnkiDroid fork via the shared Rust engine.
- [ ] **Manual mode toggle** (long-term ↔ short-term).
- [ ] **Forced UI randomization** feature (toggleable; ablated in Phase 3).
- [ ] **Desktop installer** that runs on a clean machine.

> **Give-up rule:** no readiness score until **≥ 200 graded reviews** *and*
> **≥ 50% outline coverage**; below either line, abstain and show what's missing.

## Build & run

Needs [rustup](https://rustup.rs/) (toolchain pinned in `rust-toolchain.toml`).

```bash
./run
```

Full instructions: [`docs/development.md`](./docs/development.md). Android build +
emulator setup lives in the AnkiDroid fork at
`docs/development/build-and-emulator-setup.md`.

## License & credit

Fork of **Anki** (Ankitects Pty Ltd + contributors), distributed under
**AGPL-3.0-or-later**; some user-contributed parts are BSD-3-Clause (see
[`CONTRIBUTORS`](./CONTRIBUTORS), [`LICENSE`](./LICENSE)). Credit to the upstream
**Anki** and **AnkiDroid** projects for the engine and apps.
