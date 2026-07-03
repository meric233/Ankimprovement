# Ankimprovement — Desktop (USMLE Step 1)

A "speedrun" **USMLE Step 1** study app: a fork of [Anki](https://apps.ankiweb.net)
that shares **one Rust engine** with an [AnkiDroid](https://github.com/ankidroid/Anki-Android)
fork (the Android companion). This is the desktop app.

**Exam — USMLE Step 1:** pass/fail only (no numeric score, since 2022-01-26);
7 blocks × ≤40 MCQs, 60 min each, ~8-hour day; content organized by organ system
and discipline. The app reports **probability of passing** with a range — never an
invented score.

## Features

- **AI question rephrasing** *(the flagship AI feature; off by default)* — when an eligible card reappears, an LLM (`gpt-4o`) rewords **only its question** (active↔passive, reordered clauses) while keeping the answer, medical terms, numbers, and cloze blanks **verbatim**. This strips the "familiar wording" cue and forces re-encoding.
  - **Source-traced** — each rewording stores the source note id + SHA-256 of the original text + model + timestamp; only the card's own sanitized text is sent.
  - **Held-out preflight gate** — before any student sees a rephrase, an in-app eval runs on a held-out set and the feature stays OFF until it clears its pre-declared cutoffs (answer-preservation ≥ 90 %, wrong-answer-rate ≤ 10 %, effective-rephrasing ≥ 80 %).
  - **First-view + prefetch** — the reword shows on first appearance and upcoming cards are pre-fetched in the background to hide model latency; a card keeps the same reword until you rate it **Easy**.
  - **Feeds Performance** — answering a rephrased card nudges a per-card `performance` score and damps the FSRS update to 0.5× (undo-safe).
- **Study dashboard** — three always-shown scores, each per **today / +5 days / +10 days** horizon:
  - **Memory** — mean FSRS recall over the cards you've studied (with a confidence range).
  - **Performance** — a blend of `0.75 × Memory + 0.25 × per-card rephrasing score`, with an uncertainty band from ranging the weights; falls back to a flagged `0.9 × Memory` estimate when AI is off. Abstains until > 50 % of outline cards are scored. *(Weights are an explicit, arbitrary learning-science prior — noted in-app — pending real testing data.)*
  - **Readiness** — a **calibrated probability of passing** (≥ 60 % cut score), = blended Performance × coverage; a point probability, *not* a predicted score and shown without a range.
- **Over-confidence view** — every score is also projected forward with **no further study** at today / +5 / +10 days, so decay is visible.
- **Coverage map** — % of the First Aid outline you've actually seen, with a **Review** and **Learn least-covered topic** shortcut.
- **Give-up rule** — Readiness abstains until **≥ 200 graded reviews**, **≥ 50 % coverage**, *and* the Performance signal is available — showing what's missing instead of guessing.
- **Mastery query** — fast per-topic cards-mastered + average recall (Rust; undo-safe).
- **Manual mode toggle** — switch between long-term and short-term study framing.
- **Forced UI randomization** — randomizes card fonts to stop you memorizing a card's *look* instead of its content (toggleable).
- **Admin / simulation mode** — dev tooling to bulk-set FSRS state (and per-card performance), advance days, and reset cards, to exercise the dashboard without weeks of reviews.

## Build & run

Needs [rustup](https://rustup.rs/) (toolchain pinned in `rust-toolchain.toml`).

```bash
./run                    # build & launch from source
tools/build-installer    # build a distributable .dmg (macOS) under out/installer/dist
```

The app runs fully **without** AI. To enable the optional AI rephrasing feature,
drop an OpenAI key in `ai_secrets.json` (`{"openai_api_key": "sk-...", "model":
"gpt-4o"}`), then turn it on via **Tools → AI: rephrase cards**; with no key the
feature stays off. Full instructions: [`docs/development.md`](./docs/development.md).
Android build + emulator setup lives in the AnkiDroid fork at
`docs/development/build-and-emulator-setup.md`.

## License & credit

Fork of **Anki** (Ankitects Pty Ltd + contributors), distributed under
**AGPL-3.0-or-later**; some user-contributed parts are BSD-3-Clause (see
[`CONTRIBUTORS`](./CONTRIBUTORS), [`LICENSE`](./LICENSE)). Credit to the upstream
**Anki** and **AnkiDroid** projects for the engine and apps.
