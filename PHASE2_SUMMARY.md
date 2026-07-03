# Phase 2 (Friday) — The AI Feature: Rephrasing Card Questions

**Project:** Fork of Anki for **USMLE Step 1** — desktop + Android companion that
**share one Rust engine**. Phase 1 (Wednesday) shipped the core with **no AI**
([`PHASE1_SUMMARY.md`](PHASE1_SUMMARY.md)). This Phase 2 / Friday build adds the
one AI feature: **AI rephrasing of a card's question on reappearance**, plus the
in-app **Performance** signal it feeds, and the honest eval/leakage proof around
it.

- **AI feature:** reword the **question** of easy, familiar cards when they come
  back, so the student recalls the *fact*, not the card's *shape*.
- **Provider / model:** OpenAI **`gpt-4o`** (chat completions). API key stored in
  a **git-ignored** `ai_secrets.json` (or `OPENAI_API_KEY`); never committed.
- **Default:** **OFF** (`aiRephraseEnabled`). The whole app scores with AI off.
- **Where it lives:** `qt/aqt/ai/rephrase.py` (all AI logic), reviewer hooks +
  Tools-menu toggle in `qt/aqt/main.py`, Performance/Readiness compute in
  `rslib/src/stats/readiness.rs`, admin perf-setting in `rslib/src/stats/admin.rs`.

---

## 1. What the feature does (and why)

When a card reappears and **every gate passes**, the reviewer shows an
AI-reworded version of the **question** (the answer side is untouched). This
strips the "familiar wording" environmental cue and forces the student to
**re-encode** the fact — deeper, transfer-oriented learning rather than
pattern-matching a memorized card layout. Answering a rephrased card also
produces an in-app **Performance** signal (below).

**Gating — all must hold, otherwise the original question is shown verbatim:**

1. `aiRephraseEnabled` is **on** (Tools menu; off by default).
2. **Long-term learning mode** (`usmleStudyMode == "learning"`, the shared SPOV1
   toggle).
3. Card FSRS **difficulty < 5** (only easy/consolidated cards; reuses the font
   gate's threshold).
4. A **valid rephrasing is available** in the per-profile cache.

---

## 2. First-view rephrasing (behaves like the font change)

The rewording shows on the **first** eligible appearance of a card, exactly like
the instant font change — there is **no** "wait until the second review" step:

- **First eligible appearance (cache miss):** fetch the rewording
  **synchronously**, show it immediately, and **score** the student's answer
  (perf nudge + FSRS damping). The only cost is a brief ~1–2 s pause on that
  first view while the model responds.
- **Every later appearance (cache hit):** show the **cached** rewording
  instantly and keep scoring it.

Rephrasing is derived only from the card's own text, so scoring an answer to it
is honest. A card keeps the **same** rewording on every reappearance until it's
rated **Easy**, which invalidates the cache so the next appearance is freshly
reworded. On any fetch failure (offline/error/implausible output) the card falls
back to the **original** and that answer is **not** scored.

---

## 3. Source-tracing (rubric-critical)

Every rephrasing is derived **only from the card's own rendered text** — never
outside knowledge:

- Input text is **sanitized** first (script/style/comments/hidden nodes and
  `on*` handlers stripped) as a **prompt-injection defence** against adversarial
  "hidden text in the source file" cards.
- Each cached rephrasing stores **source note id + SHA-256 of the original text +
  model + timestamp** (`ai_rephrase_cache.json` in the profile folder).
- If the model returns empty or degenerate output (length ratio outside
  0.3×–3×), it is rejected and the original is used.

---

## 4. The per-card `performance` score

A per-card **`performance`** value lives in Anki's `custom_data["perf"]` (range
**1–100**, default **50**). When an **AI-rephrased** card is answered it is nudged
by grade — **Again −8 / Hard −3 / Good +3 / Easy +8** (arbitrary v1 steps) — and
clamped to [1, 100]. On those same answers the **FSRS memory-state change is
damped to 0.5×** the normal update (a different retrieval context shouldn't move
stability/difficulty as much as a normal review). Both the perf write and the
damping are **undo-safe** — folded into the answer's own undo step, so a single
⌘Z reverts everything.

---

## 5. How it feeds the dashboard — blended Performance + Readiness

The per-card perf score is **not** shown raw; it is the **0.25 term** of a
**blended Performance** metric, computed per horizon (today / +5d / +10d) in
Rust:

```
Performance(h) = 0.75 · memory_retrievability(h) + 0.25 · card_perf_score
```

- **Uncertainty band:** obtained by **ranging the weight pair** from
  **(0.85, 0.15)** to **(0.65, 0.35)** around the (0.75, 0.25) mean — not a
  statistical CI, a sensitivity band.
- **Readiness(h)** = **blended Performance(h) × coverage fraction**, mapped
  through the calibration curve to a **pass probability**. Readiness is a
  probability, so it is shown **without a range**
  (`range_low = range_high = p_pass`).
- **Availability / give-up:** both Performance and Readiness **abstain until
  > 50% of outline cards are rephrased & scored** (in addition to the ≥ 200
  graded reviews / ≥ 50% coverage give-up rule).

> ⚠️ **The 0.75 / 0.25 weights (and the ± band) are ARBITRARY** — a qualitative
> learning-science prior (durable memory dominates transfer, but performance on
> reworded prompts matters). **Honest weights can only come from real held-out
> testing data, which we do not yet have.** This caveat is shown verbatim in the
> app's Readiness tab.

**AI-off fallback (Due-Friday: show a score even with AI switched off).** With
AI off there is no per-card rephrasing signal, so instead of hiding Performance
and Readiness we report a **compromised** estimate:

```
Performance(h) = 0.9 · memory_retrievability(h)     # AI OFF, no ± band
```

flagged `degraded = true` with a caveat ("compromised estimate — 0.9 × Memory —
less accurate; enable AI for a true score"). Readiness is still derived from it
(× coverage) and its card carries a matching note. It needs ≥ 1 studied card;
otherwise it stays unavailable. The 0.9 haircut is itself arbitrary. Rust reads
the `aiRephraseEnabled` config (so desktop + Android agree); the weight band and
"cards scored" line are hidden in the UI when degraded.

**Where:** `rslib/src/stats/readiness.rs` (`build_performance`, `build_readiness`,
`DashboardPerformance` + `DashboardPerformanceHorizon`, incl. `degraded` /
`degraded_note`); UI in `ts/routes/readiness/ReadinessPage.svelte`.

---

## 6. Admin / simulation support

The **Admin: simulation mode** dialog can set the per-card **Performance score
(1–100)** in the *same* form as FSRS S/D/R (no new tab). Cards set this way are
marked **scored** (and a synthetic graded review is logged) so they count toward
the total Performance score and can **unlock** Performance-based Readiness for a
demo. **Reset-to-new clears both the FSRS state and the `perf` custom data** as a
single undoable step, so a reset card correctly stops counting as "scored" and
matches its zeroed coverage.

**Where:** `rslib/src/stats/admin.rs` (`custom_data_with_perf` /
`custom_data_without_perf`, `admin_set_fsrs`, `admin_reset_cards`),
`proto/anki/stats.proto` (`AdminSetFsrsRequest.performance`),
`pylib/anki/collection.py`, `qt/aqt/admin.py`.

---

## 7. Held-out eval, baseline, and leakage check

- **Live in-app preflight — "an eval that runs before students see anything":**
  the held-out check also runs **inside the app**, automatically, before any
  student is shown a rephrase. When AI is toggled on (and lazily before the first
  eligible card) `run_preflight_eval` runs on a **background thread**, prints
  **accuracy (answer-preservation) + wrong-answer rate** and the cutoffs to the
  terminal, and **gates the feature**: rephrasing stays OFF (eligible cards show
  the original) until the eval passes answer-preservation ≥ 90% (wrong-rate
  ≤ 10%) **and** effective-rephrasing ≥ 80% (when embeddings are available).
  Sample live run: `PREFLIGHT RESULT: PASS — accuracy 100%, wrong-rate 0%,
  meaning 100%, effective 100%`. Same held-out set + cutoffs as the offline
  script, so numbers are comparable. Code: `qt/aqt/ai/rephrase.py`
  (`run_preflight_eval`, `_ensure_preflight`, `trigger_preflight`).
- **Eval (`rephrase_eval.py --live`, re-runnable):** on 15 held-out items, with
  the tightened synonym-only prompt (temp 0.2) — answer-preservation **100%**,
  meaning-preservation **100%**, wrong-rate **0%**, **effective-rephrasing 80%**
  (95% range 55–93%). Pre-declared cutoffs (effective ≥ 80%, answer-preservation
  ≥ 90%) **met**. Meaning-similarity via OpenAI embeddings
  (`text-embedding-3-small`), lexical fallback offline. `--dry-run` is a plumbing
  check (always passes); `--live` enforces the cutoffs.
- **Beats a baseline:** **AI 80% > 73%** naive synonym-substitution — the
  baseline barely changes wording and fails to strip the visual cue.
- **Faithfulness trade-off (honest):** tightening the prompt to close-synonym +
  structure changes only (no invented words like *effective*/*initiate*) raised
  meaning-preservation 93% → **100%** but lowered effective-rephrasing 93% →
  **80%** — the model now sometimes returns text nearly/entirely unchanged rather
  than risk drifting the claim.
- **Leakage (`rephrase_leakage_check.py`):** **CLEAN** — a **frozen** model (no
  fine-tuning ⇒ no training corpus to leak into); the 15 fixtures are internally
  unique and authored separately from the deck (can also scan a real collection
  via `--collection`).

---

## 8. Robustness

Any API failure — offline, HTTP error, timeout, malformed response, missing
`requests`, missing key — makes the feature **fall back to the original
question** and make **no** perf/damping change. The reviewer never breaks and the
app always scores with AI off.

---

## 9. Observability & demo logging

All decisions are logged under the `anki.ai.rephrase` logger, which is visible in
the `./run` dev console. This makes a "0 cards scored" situation self-explanatory
and lets the demo show the model's work live:

- On launch: `hooks registered (model=gpt-4o)` — or a **WARNING** if no key is
  found (feature stays off).
- Per card: `SKIP … (enabled=… config=… learning=… has_state=… difficulty=…)`
  pinpoints exactly which gate blocked a rephrase.
- Fetch path (first view): `fetching rewording … (first eligible view)` →
  `CACHED new rewording  ORIGINAL: … / REPHRASED: …` (or `no usable rewording …
  showing ORIGINAL`).
- Scoring path: `SHOWING rephrased question … NOW` → `SCORING card … perf X → Y
  (ease N)`.
- The **`CACHED`** and **`SHOWING`** lines print the **`ORIGINAL`** vs
  **`REPHRASED`** text side-by-side (HTML stripped, collapsed to one line each)
  so the rewording is visible on camera.

**Verified working end-to-end** from the dev log, e.g.:

```
INFO:anki.ai.rephrase: SHOWING rephrased question for card 1499967475961 NOW (this answer will be scored)
INFO:anki.ai.rephrase: SCORING card 1499967475961 perf 50.0 -> 58.0 (ease 4)
```

---

## 10. Files touched / added

| Area | Files |
|---|---|
| AI logic | `qt/aqt/ai/rephrase.py` (new), `qt/aqt/ai/__init__.py` |
| Secrets | `ai_secrets.json` (git-ignored), `.gitignore` |
| Wiring / toggle | `qt/aqt/main.py` (hooks `init`, Tools-menu toggle) |
| Dashboard compute | `rslib/src/stats/readiness.rs` (blended Performance + Readiness horizons) |
| Admin | `rslib/src/stats/admin.rs`, `proto/anki/stats.proto`, `pylib/anki/collection.py`, `qt/aqt/admin.py` |
| UI | `ts/routes/readiness/ReadinessPage.svelte` (horizons + arbitrary-weights caveat) |
| Eval / proof | `rephrase_eval.py`, `rephrase_leakage_check.py` |
| Tests | `qt/tests/test_ai_rephrase.py` (10), Rust `stats::readiness` / `stats::admin` |

---

## 11. How to demo (repeatable)

1. **Tools → "AI: rephrase cards (experimental)"** → on.
2. Confirm **long-term learning mode**.
3. **Admin:** set some cards to **difficulty 2** (< 5) with a memory state.
4. **Review those cards** — the **first** view of each eligible card pauses
   briefly, then shows the **reworded** question immediately; the log shows
   `fetching rewording … (first eligible view)` → `CACHED  ORIGINAL: … /
   REPHRASED: …` → `SHOWING rephrased question … NOW` → `SCORING … perf 50 → 53`.
   The Performance tab's scored count rises right away (no need to advance days
   first). Reviewing again later reuses the cached rewording instantly, and, past
   50% coverage, Performance/Readiness become available.

---

## 12. Explicitly NOT in this build (later / by design)

- **Card *generation*** (§7f gold-set check) — not our chosen AI feature.
- **Fitted** Performance/Readiness weights and calibration (Brier/log-loss) —
  Sunday; today's weights are the arbitrary v1 prior noted above.
- **Paraphrase-gap held-out test** (§7d) and the **UI-randomization 3-build
  ablation** (§8) — Sunday.

---

## 13. Android parity — the feature runs natively on the phone

The Speedrun deliverable is desktop **+** a mobile companion that share one
engine, so the AI feature now runs **natively on AnkiDroid**, not just via
synced data. The dashboard scores (Memory / Performance / Readiness, the AI-off
`0.9 × memory` fallback, coverage, admin perf) were already cross-platform
because they are computed in the **shared Rust engine** the phone links; what was
missing — and is now built — is the **rephrasing feature itself** in Kotlin.

The Kotlin port is a faithful mirror of `qt/aqt/ai/rephrase.py`, reusing the
same config keys, perf steps, 0.5× damping, difficulty gate, cache-until-Easy
rule, source-tracing, prompt, cutoffs, and held-out preflight — so a card scored
on the phone moves exactly as it would on the desktop and the two agree after
sync.

| Desktop (Python) | Android (Kotlin) |
|---|---|
| `qt/aqt/ai/rephrase.py` pure logic | `AnkiDroid/.../ai/AiRephrase.kt` (perf math, damping, sanitising, cutoffs) |
| `request_rephrasing` / `_embedding` (OpenAI) | `ai/AiRephraseApi.kt` (OkHttp; key from `BuildConfig.OPENAI_API_KEY`) |
| `RephraseCache` (`ai_rephrase_cache.json`) | `ai/AiRephraseCache.kt` (same file, in the collection folder) |
| `run_preflight_eval` | `ai/AiRephrasePreflight.kt` (logs accuracy + wrong-rate; gates the feature) |
| `_RephraseController` + `card_will_show` / `reviewer_did_answer_card` hooks | `ai/AiRephraseController.kt`, wired into **both** reviewers |
| Tools-menu toggle (`main.py`) | Nav-drawer **"AI: rephrase cards"** toggle (`NavigationDrawerActivity`) → same `aiRephraseEnabled` config |

**Reviewer wiring (both Android reviewers):**

- **Question substitution** — legacy `AndroidCardRenderContext.renderCard` (next
  to the existing USMLE font swap) and the new `CardViewerViewModel.showQuestion`
  (via an overridable `maybeRephraseQuestion` the reviewer implements, so the
  previewer is never rephrased). Rendering can't block on the network, so the
  legacy path substitutes **cache-first** and re-renders in place when a
  background fetch lands; the new (coroutine) path awaits the fetch inline. Both
  **prefetch** the next few due cards to hide latency.
- **Post-answer** — after `sched.answerCard` in both `Reviewer.answerCardInner`
  and `ReviewerViewModel.answerCardInternal`: nudge `custom_data["perf"]` and damp
  the FSRS state change to 0.5×, folded into the answer's undo step.
- **Card mutation** — `Card.customData` / `Card.memoryState` were made publicly
  settable in `libanki` (matching the Python `Card`), persisted via
  `col.updateCard` + `col.mergeUndoEntries`.

**Key & build.** The OpenAI key is injected at build time from
`local.properties` (`OPENAI_API_KEY` / `OPENAI_MODEL`) — the same key the desktop
reads from `ai_secrets.json` — into `BuildConfig`; empty ⇒ feature stays OFF.
Built and installed to the emulator with
`./gradlew :AnkiDroid:installFullDebug` (verified: builds, installs, launches, no
crash). Logs are visible via `adb logcat | grep -i "AI rephrase"` (Timber), the
phone-side equivalent of the desktop dev console.

*Anchored to `PRD_Week2.md` (§9a, AI rephrasing) and the Speedrun spec (Friday:
"AI added & checked; phone syncs"). Continues [`PHASE1_SUMMARY.md`](PHASE1_SUMMARY.md).*
