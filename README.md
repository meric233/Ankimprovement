# Ankimprovement — Desktop (USMLE Step 1)

A "speedrun" **USMLE Step 1** study app: a fork of [Anki](https://apps.ankiweb.net)
that shares **one Rust engine** with an [AnkiDroid](https://github.com/ankidroid/Anki-Android)
fork (the Android companion). This is the desktop app.

**Exam — USMLE Step 1:** pass/fail only (no numeric score, since 2022-01-26);
7 blocks × ≤40 MCQs, 60 min each, ~8-hour day; content organized by organ system
and discipline. The app reports **probability of passing** with a range — never an
invented score.

## Features

- **Study dashboard** — three always-shown scores, each with an honest range:
  - **Memory** — mean FSRS recall over the cards you've studied.
  - **Performance** — chance of answering a *new* exam-style question right (placeholder until the held-out model).
  - **Readiness** — a **calibrated probability of passing** (≥ 60 % cut score), *not* a predicted score.
- **Over-confidence view** — every score is also projected forward with **no further study** at **today / +5 days / +10 days**, so decay is visible.
- **Coverage map** — % of the First Aid outline you've actually seen.
- **Give-up rule** — Readiness abstains until **≥ 200 graded reviews** *and* **≥ 50 % coverage**, showing what's missing instead of guessing.
- **Mastery query** — fast per-topic cards-mastered + average recall (Rust; undo-safe).
- **Manual mode toggle** — switch between long-term and short-term study framing.
- **Forced UI randomization** — randomizes card fonts to stop you memorizing a card's *look* instead of its content (toggleable).
- **Admin / simulation mode** — dev tooling to bulk-set FSRS state, advance days, and reset cards, to exercise the dashboard without weeks of reviews.

## Build & run

Needs [rustup](https://rustup.rs/) (toolchain pinned in `rust-toolchain.toml`).

```bash
./run                    # build & launch from source
tools/build-installer    # build a distributable .dmg (macOS) under out/installer/dist
```

Full instructions: [`docs/development.md`](./docs/development.md). Android build +
emulator setup lives in the AnkiDroid fork at
`docs/development/build-and-emulator-setup.md`.

## License & credit

Fork of **Anki** (Ankitects Pty Ltd + contributors), distributed under
**AGPL-3.0-or-later**; some user-contributed parts are BSD-3-Clause (see
[`CONTRIBUTORS`](./CONTRIBUTORS), [`LICENSE`](./LICENSE)). Credit to the upstream
**Anki** and **AnkiDroid** projects for the engine and apps.
