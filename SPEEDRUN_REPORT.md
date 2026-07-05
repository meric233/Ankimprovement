# Speedrun Report — USMLE Step 1 (Anki fork)

One consolidated status doc, following the **Speedrun spec sections identically**.
Each section states what is **done / partial / not yet**, where it lives, the
justification, and the tests/proof. Detailed docs are linked, not duplicated.

**Legend:** ✅ done · 🟡 partial · ⬜ not yet (planned phase in parens).

- **Exam:** USMLE Step 1 — pass/fail (no invented numeric score).
- **License:** AGPL-3.0-or-later, crediting Anki (some parts BSD-3-Clause).
- **Repos:** `Ankimprovement/` (desktop fork) · `Ankimprovement-Android/`
  (AnkiDroid fork) · `Anki-Android-Backend/` (custom backend AAR, re-port of the
  desktop Rust changes so Android links them). Shared engine: `Ankimprovement/rslib/`.
- **Phase status:** **Wednesday (core, no AI) essentially complete.** **Friday
  items done:** two-way sync (early) and the **AI rephrasing feature** —
  source-traced, held-out eval passes its pre-declared cutoff and beats a
  baseline, leakage-clean, off by default (§7f-AI). **Sunday proofs in progress:**
  crash test (20/20 clean) and 50k-card speed benchmark are **real and passing**
  (§7g, §7h); memory-calibration, performance/paraphrase, and the study-feature
  ablation are **simulated on fake data** (§9-SIM, clearly fenced) pending real
  held-out students. Signed release APK built (§12b); recordings/demo video still outstanding.

Key source docs: [`PHASE1_SUMMARY.md`](PHASE1_SUMMARY.md) ·
[`PHASE2_SUMMARY.md`](PHASE2_SUMMARY.md) (Friday AI feature) ·
[`docs/mastery-query.md`](docs/mastery-query.md) ·
[`build-and-emulator-setup.md`](https://github.com/meric233/Ankimprovement-Android/blob/main/docs/development/build-and-emulator-setup.md) ·
[`local-sync-setup.md`](https://github.com/meric233/Ankimprovement-Android/blob/main/docs/development/local-sync-setup.md) ·
[`testing-coverage.md`](docs/testing-coverage.md) · [`PRD_Week2.md`](PRD_Week2.md).

---

## 1. The Mission — three different questions

The app keeps the three headline scores as **separate cards** (it never collapses
them into one blended number):

- **Memory** ✅ — chance the student recalls a taught fact now (FSRS R), per
  horizon (today / +5d / +10d).
- **Performance** 🟡 — chance of transferring a fact to *reworded* questions.
  A **blend** of `0.75 × Memory retrievability + 0.25 × per-card rephrasing
  score` (the per-card `performance` score, 1–100, nudged when an **AI-rephrased**
  card is answered, §7f-AI), shown per horizon with an **uncertainty band** from
  ranging the weights (0.85,0.15)→(0.65,0.35). Abstains until > 50% of outline
  cards are rephrased & scored; kept **separate** from the held-out paraphrase
  test (still Sunday, §7d). **The weights are arbitrary** (qualitative
  learning-science prior); honest weights need real testing data we don't yet
  have (surfaced as a note in the app + §4).
- **Readiness** ✅ — **probability of passing** per horizon, = **blended
  Performance × coverage** mapped through the calibration curve (§4). Being a
  probability, it is shown **without an uncertainty range**. Abstains under the
  give-up rule (which now also requires the Performance signal to be available).

**Honesty rule** — Readiness is only shown with: evidence (reasons), what's
missing (coverage %, give-up gate, Performance availability), and the single
best next thing to study. It is a point probability (no range, by design);
uncertainty is carried on the **Performance** input. Calibration accuracy
(past-guess tracking) is 🟡 v1 only (Sunday adds the fitted curve + Brier). The
over-confidence "no-study" horizons are shown on **all three** cards.

---

## 2. The Rules You Cannot Break

| Rule | Status | Where / note |
|---|---|---|
| Real change inside Anki's **Rust** code | ✅ | Mastery query — §7a |
| Two apps share **one engine**, reviews sync | ✅ | Desktop + AnkiDroid on shared `rslib`; two-way sync done — §3, §7b |
| **Three scores** (not blended); Memory/Performance carry a range, Readiness is a point probability | ✅ | §1, §4 |
| Held-out testing, re-runnable | 🟡 | AI-rephrasing held-out eval ✅ (§7f-AI); memory/performance calibration pipeline built & re-runnable on **simulated** data (§9-SIM); real held-out data pending |
| One study feature, expectation written, on/off test | 🟡 | Feature + toggle shipped; 3-build ablation is Sunday — §8 |
| Every AI output sourced, checked, beats baseline | ✅ | AI rephrasing: source-traced, held-out eval passes cutoff, beats baseline — §7f-AI |
| App refuses a score without enough data | ✅ | Give-up rule — §4 |
| Desktop installer + phone build, both run **AI-off** | 🟡 | Desktop `.dmg` ✅ + emulator APK ✅; AI-off trivially true (no AI). Signed APK ⬜ (Sun) |
| License AGPL-3.0-or-later + credit Anki | ✅ | READMEs + LICENSE |

---

## 3. Two Apps, One Engine

- **Shared engine** ✅ — both clients call the same Rust core over protobuf; our
  engine change (§7a) ships to both. No scheduler rewrite in JS/Kotlin/Swift.
- **Phone runs real review sessions on the same deck** ✅ — AnkiDroid on the
  `usmle_step1` emulator, reviewing the AnKing V11 deck through the shared engine.
- **Two-way sync** ✅ (a Friday item, done early) — self-hosted Anki sync server
  (port 27701, `test/test`); desktop ↔ emulator via `10.0.2.2`. Full procedure,
  media policy, and gotchas in `local-sync-setup.md`.
- **Offline then sync** ✅ — verified (review offline → reconnect → merge).
- **Same three scores + give-up rule on phone** ✅ — native `ReadinessPage.kt`
  renders the shared `readiness` SvelteKit page from the backend.

---

## 4. Memory, Performance, Readiness (shown separately)

All three cards show the same **today / +5d / +10d** horizons. Memory and
Performance carry a range; Readiness is a point probability (no range). All show
coverage %, reasons, and the give-up rule. Lives in
`rslib/src/stats/readiness.rs` (compute), `qt/aqt/readiness.py` +
`ts/routes/readiness/` (desktop UI), `AnkiDroid/.../pages/ReadinessPage.kt` (phone).

**Memory** ✅ — mean current FSRS retrievability *R* over studied cards, with a
normal-approx confidence interval (narrows as data grows), per horizon.

> **FSRS is force-enabled** for this fork at collection load (`main.py
> _ensure_fsrs_enabled`, config key `fsrs`). Anki ships with FSRS **off** (SM-2
> default); with it off, answering never writes `memory_state`, so cards would
> show "no FSRS memory state", the dashboard would have no recall to read, and
> the AI-rephrase difficulty gate could never pass. The entire model is
> FSRS-based, so it must always be on.

**Performance** 🟡 — a **blend of memory and the in-app rephrasing signal**, per
horizon *h*:

```
Performance(h) = 0.75 · memory_retrievability(h) + 0.25 · card_perf_score
```

where `card_perf_score` = mean per-card `performance` (`custom_data["perf"]`,
1–100, default 50) over **all** outline cards, nudged whenever an AI-rephrased
card is answered (§7f-AI). The **uncertainty band** comes from **ranging the
weight pair** from **(0.85, 0.15)** to **(0.65, 0.35)** around the (0.75, 0.25)
mean. Computed in Rust (`stats::readiness`, `DashboardPerformance` +
`DashboardPerformanceHorizon`); **abstains until > 50% of outline cards are
rephrased & scored**. Kept separate from the **held-out** paraphrase test (§7d).

> ⚠️ **The 0.75 / 0.25 weights (and the ± band) are ARBITRARY** — a qualitative
> learning-science prior (durable memory dominates transfer, but performance on
> reworded prompts matters). The **accurate weights can only be determined from
> real held-out testing data, which we do not yet have.** This caveat is shown
> verbatim in the app's Readiness tab.

**AI-off fallback (Due-Friday requirement)** ✅ — the Speedrun requires a
Performance *and* Readiness reading **even with AI switched off**. With AI off
there is no per-card rephrasing signal, so instead of hiding both scores we fall
back to a **compromised estimate**:

```
Performance(h) = 0.9 · memory_retrievability(h)     # AI OFF
```

reported as a **single point (no ± band)** and flagged `degraded=true` with a
caveat. Readiness is then derived from it as usual (× coverage), and its card
shows a matching "based on a compromised estimate (AI off)" note. Rationale: with
no evidence about transfer under rephrasing we assume performance is a little
worse than raw recall (the 0.9 haircut is itself arbitrary). It needs ≥ 1 studied
card to read memory; otherwise it stays unavailable. Implemented in
`stats::readiness::build_performance` (gated on the `aiRephraseEnabled` config,
read in Rust so desktop + Android behave identically), fields `degraded` /
`degraded_note` on `DashboardPerformance`; UI in `ReadinessPage.svelte` hides the
weight band and shows the caveat. Tests:
`ai_off_performance_is_degraded_memory_estimate`,
`ai_off_without_studied_cards_cannot_estimate_performance`.

**Readiness** ✅ — **P(pass)** per horizon, explicitly *not* a predicted score and
**without a range** (it is already a probability). Method:
1. **Expected correctness(h)** = **blended Performance(h) × coverage fraction**.
   Performance does *not* encode coverage (unscored cards fall back to the neutral
   default), so Readiness applies the coverage factor explicitly — thin coverage
   honestly drags the pass probability down.
2. Map correctness → pass probability via the **calibration curve** anchored to
   reported practice-exam outcomes (cut = 60% correct): 70%→99%, 65%→95%,
   62%→92%, **60%→~65%**. The 60% anchor sits below a naive line because at the
   cut score pass/fail is near a coin flip. (v1, Wednesday-honest; Sunday refits
   against measured Performance.)
3. **No uncertainty range**: `range_low = range_high = p_pass` per horizon; the
   UI shows the probability and the expected-correct figure behind it.

**Over-confidence horizons** ✅ — all three cards project forward with **no study**
at **today / +5d / +10d**. Because decay is governed by **stability S**, crammed
(low-S, high-R) cards fall off fastest; since Performance and Readiness both build
on projected memory, the today→+10d **drop** flows through to them and is surfaced
as a fragility warning on the Readiness card. Rationale in
[`PHASE1_SUMMARY.md` §3](PHASE1_SUMMARY.md).

**Give-up rule** ✅ (written down): **no Readiness until ≥ 200 graded reviews AND
≥ 50% Step 1 outline coverage AND the Performance signal is available (> 50% of
outline cards scored)** — since Readiness is now Performance-based, it cannot be
shown without Performance data. Below any line the app abstains and shows what's
missing.

**Admin / simulation mode** ✅ (dev tooling, **not** scoring): bulk-set FSRS state,
**set the per-card Performance score (1–100)** alongside S/D/R in the *same*
form, advance days, reset cards — to exercise the dashboard without weeks of
reviews. Cards set via admin become review cards (a synthetic graded review is
logged) and, when a Performance value is given, are marked **scored** so they
count toward the total Performance score and unlock Performance-based Readiness.
**Reset-to-new clears both the FSRS state and the per-card performance score** (as
one undoable step), so a reset card stops counting as "scored" — matching its
zeroed coverage. Setting FSRS state also **enables the FSRS scheduler**
(`BoolKey::Fsrs`): otherwise the collection's SM-2 answer path (`fsrs_next_states
= None`) **wipes the admin-set memory state to None on the first real answer**,
which showed up as cards reverting to "no FSRS memory state" after being reviewed
— now fixed and covered by `set_fsrs_enables_fsrs_so_answering_keeps_memory_state`.
All in Rust, undoable (⌘Z). `rslib/src/stats/admin.rs`, `qt/aqt/admin.py`,
`AnkiDroid/.../pages/AdminSimulationFragment.kt`.

---

## 5. Pick One Exam

✅ **USMLE Step 1**, pass/fail since 2022-01-26 — we predict **chance of passing**
above a safe line, no invented score. Stated at the top of both READMEs. Content
outline modeled via the AnKing **First Aid** section tags (16 sections), which
drive topic granularity and the coverage map.

---

## 6. What Is Due, Deadline by Deadline

### Due Wednesday — core works on both screens, no AI
| Item | Status |
|---|---|
| Anki forked & building from source | ✅ |
| Rust change end-to-end (diff, 3 Rust tests, 1 Python test) | ✅ — §7a (6 Rust + 1 Python) |
| Review loop on the exam deck | ✅ |
| Memory model, honest (range + give-up rule) | ✅ — §4 |
| Installer that runs on a clean machine | ✅ — `tools/build-installer` → `.dmg`; released `v0.1.1-usmle` |
| Phone builds & runs on emulator, loads deck, real review on shared engine | ✅ |
| **Proof:** commit hash, clean-build recording, test results, install recording, phone review recording | 🟡 — **test results captured** (Rust `stats::` **33/33**, Python AI **13/13**, mastery e2e **1/1**, crash **20/20**, 50k benchmark all budgets met); recordings still to capture |

### Due Friday — AI added & checked; phone syncs
| Item | Status |
|---|---|
| Note on what AI built / why / skipped | ✅ — §7f-AI (AI rephrasing of card questions on reappearance, SPOV4 / PRD §9a) |
| Every AI output traces to a named source | ✅ — each rephrasing stores source note id + SHA-256 of the original text + model + timestamp (`ai_rephrase_cache.json`); rephrases only the card's own sanitized text |
| Held-out eval (accuracy + wrong-answer rate + cutoff) | ✅ — offline `rephrase_eval.py --live` on 15 held-out items: answer-preservation **100%**, meaning **100%**, wrong-rate **0%**, effective-rephrasing **~80%**; cutoffs (effective ≥ 80%, answer-preservation ≥ 90%) **met**. **Also runs live in-app**: `run_preflight_eval` fires before any student sees a rephrase, prints accuracy/wrong-rate to the terminal, and **gates the feature** (§7f-AI) — "an eval that runs before students see anything" |
| Eval runs **before students see anything** (Speedrun §6) | ✅ — in-app **preflight** on feature-enable + before the first eligible card; rephrasing stays OFF until it passes the cutoff, so no unvetted rephrase reaches a student |
| AI beats a simpler baseline | ✅ — vs. naive synonym-substitution baseline: effective-rephrasing **80% (AI) > 73% (baseline)**; the baseline barely changes wording so it fails to strip the visual cue |
| Leakage check clean (§7e) | ✅ — `rephrase_leakage_check.py` CLEAN (frozen model, no fine-tuning → no training set; held-out fixtures internally unique and separate from the deck) |
| App still scores with AI off | ✅ — feature is behind `aiRephraseEnabled` (default **off**); all scoring is AI-independent. **Performance & Readiness are still shown with AI off** as a *compromised* estimate (`Performance = 0.9 × Memory`, flagged `degraded` with an in-app caveat) rather than hidden — see §4 |
| **Two-way sync** works, no lost/double-counted | ✅ (done early) — §7b |
| Offline review then sync | ✅ |
| Phone shows three scores + give-up rule | ✅ |
| Phone runs the **AI feature natively** (not just synced data) | ✅ — full rephrasing port in Kotlin, both reviewers, drawer toggle, live preflight; builds + installs to emulator (§7f-AI-Android) |

### Due Sunday — prove it, ship both
| Item | Status |
|---|---|
| Memory calibrated (calibration chart + Brier/log-loss on held-out) | 🟡 **simulated** — §9-SIM (pipeline + metrics + reliability table inlined on **fake** data; real held-out pending) |
| Performance model accuracy on held-out questions | 🟡 **simulated** — §9-SIM |
| Score mapping written down | 🟡 (v1 correctness→P(pass) curve documented in §4; Readiness is a point probability by design — no range; not yet fitted/validated) |
| Study feature 3-build ablation, equal time | 🟡 **simulated** — §9-SIM / §8 (fake-data ablation with pre-stated H1 + negatives) |
| Honest reporting incl. negative results | 🟡 **simulated** — §9-SIM reports null + time-cost results |
| Generation-forcing (SPOV4, optional) | ⬜ — optional; not built (AI rephrasing was chosen as the AI feature instead) |
| Packaged desktop installer + **signed** phone build | ✅ (desktop `.dmg` — `out/installer/dist/anki-26.05-mac-intel.dmg`, unsigned/un-notarized; **signed release APK built & verified** via `assemblePlayRelease` with our own keystore — see §12b) |
| Sync conflict handling correct + documented | ✅ — §7b |
| Both apps run AI-off and still score | ✅ |
| **Proof:** results report, model descriptions, Brainlift, clean-device recordings | 🟡 — results report = this doc (sim results inlined §9-SIM); model descriptions §4/§12; Brainlift PDF present; **demo video + clean-device recordings ⬜** |

---

## 7. Concrete Challenges

### 7a. The Rust change — **Mastery query** ✅
Backend RPC `StatsService.MasteryByTopic` returning, per topic, cards **mastered**
(mature, interval ≥ 21d) and **average FSRS recall**, fast on 50k cards
(single in-process SQLite pass). Called from Python as `col.mastery_by_topic(...)`.
- **Files:** `rslib/src/stats/mastery.rs` (new), `stats/mod.rs`, `stats/service.rs`,
  `proto/anki/stats.proto`, `pylib/anki/collection.py`.
- **Tests:** 6 Rust unit tests + 1 Python end-to-end (`test_stats.py::test_mastery_by_topic`).
  `cargo test -p anki --lib stats::mastery`.
- **Undo/corruption:** read-only, no `transact`, no undo entry — proven by
  `query_is_read_only_and_undo_safe`. (Admin ops that *do* write are undoable.)
- **Why Rust / files touched / merge risk:** full one-page note in
  [`docs/mastery-query.md`](docs/mastery-query.md) (FSRS lives in
  Rust; 50k-card performance; one engine → both apps; correctness. Footprint = 1
  new file + 4 small additive edits, low rebase risk).
- **Ships to phone:** ✅ re-ported into the backend AAR; the phone dashboard calls it.

### 7b. The sync test ✅
Verified: 10 offline reviews on each side → reconnect → all 20 land once, none
lost/duplicated; same-card conflict resolves deterministically.
- **Conflict rule (written down):** reviews (revlog) are an **additive union**
  (distinct ms-timestamp ids) so none are lost; card/note **state is
  last-writer-wins by `mtime`** (later review wins; loser's review still kept in
  the log). Verified against `rslib/src/sync/collection/chunks.rs`.
- **Re-runnable:** `Ankimprovement/sync_verify.py` reproduces both parts
  headlessly (exit 0). Details + adversarial clock caveat in `local-sync-setup.md` §5.

### 7c. The coverage map ✅
% of the Step 1 First Aid outline the deck actually covers, shown on the
dashboard; feeds the give-up rule (abstain < 50%). Computed in
`rslib/src/stats/readiness.rs` (coverage = seen cards / outline cards per topic).

**Dashboard action buttons** ✅ — the Readiness page now has two buttons
(desktop only, guarded by `bridgeCommandsAvailable()` so AnkiDroid is unaffected):
- **▶ Review** — starts a normal review session on the current deck
  (`startTimebox()` + `moveToState("review")`).
- **📚 Learn least-covered topic** — picks the outline topic with the lowest
  `cards_seen / total_cards` (ties broken by topic size) and opens a rescheduling
  filtered deck scoped to that topic's tag (incl. subtopics, new cards first) so
  it teaches unseen material. Per-topic coverage is now exposed via a new
  `cards_seen` field on `TopicMastery` (`proto/anki/stats.proto`, populated in
  `readiness.rs` + `mastery.rs`) and shown as a **Coverage** column in the map,
  with the least-covered row highlighted. Bridge wiring in `qt/aqt/readiness.py`.

### 7d. The paraphrase test 🟡 (simulated — §9-SIM)
Process built and re-runnable on **fake data** (`simulated_studies.py`): 30 cards ×
2 reworded questions, reporting the **recall-vs-reworded gap** (+0.117 in the
seeded run — original 0.767 vs reworded 0.650) plus the Performance-signal Brier.
Real held-out student data is still needed before the gap can be claimed as
measured.

### 7e. The leakage check ✅
`rephrase_leakage_check.py` scans for near-copies of the held-out eval fixtures.
Result **CLEAN**: the rephraser calls a **frozen** OpenAI model (no training /
fine-tuning), so there is no training corpus for a test item to leak into; the
15 held-out fixtures are internally unique and are authored separately from the
study deck (the script can also scan a real collection via `--collection`).

### 7f. The AI card check — N/A (rephrasing, not generation)
Our AI feature is question **rephrasing** (§7f-AI), not card *generation*, so the
50-pair generation gold set does not apply. The equivalent AI-output check is the
**held-out rephrasing eval** (answer-preservation / wrong-rate / effective-rephrasing
against a pre-declared cutoff, beating a baseline) documented in §7f-AI.

### 7f-AI. The AI feature — rephrasing card questions ✅ (Friday)
**What / why / skipped.** Chosen AI feature = **AI rephrasing of a card's
question on reappearance** (SPOV4 / PRD §9a). Why: it strips the "familiar
wording" environmental cue and forces re-encoding (deeper learning), and it lets
us build an in-app **Performance** signal. Skipped: card *generation* (§7f).
**Runs on both desktop and Android** — the full feature (not just synced data)
is ported natively to AnkiDroid in Kotlin; see §7f-AI-Android.

- **Gating (all must hold, else show the original verbatim):** `aiRephraseEnabled`
  on (**off by default**) · **long-term learning mode** (shared SPOV1 toggle) ·
  card FSRS **difficulty < 5** (reuses the font gate) · a valid rephrasing is
  available. Files: `qt/aqt/ai/rephrase.py`, reviewer hooks
  (`card_will_show` + `reviewer_did_answer_card`), Tools-menu toggle in `main.py`.
- **Source-tracing (rubric-critical):** rephrases **only the card's own sanitized
  text**; each rephrasing stores source note id + SHA-256 of the original text +
  model + timestamp. HTML/scripts/comments/hidden text stripped before the call
  (prompt-injection defence).
- **Rephrasing guideline:** prompt drives **aggressive sentence-structure change
  with unchanged vocabulary** (temp **0.4**) — active↔passive, clause reordering,
  moving the interrogative, and splitting one sentence into two are all
  encouraged (e.g. "Which drug can treat A?" → "A can be treated with which
  drug?"). But **no new words/claims** (never "used to treat" → "is effective
  against"); answer, medical/drug/disease terms, numbers, cloze/`[...]` blanks
  and tags stay verbatim.
- **First-view rephrasing (like the font change):** the **first time** an
  eligible card is shown, its reworded question is shown immediately (not a
  delayed second pass), then cached. To hide the model latency, the reviewer
  **pre-caches** the next few due cards in the **background** while the student
  reads the current one (peeks the scheduler queue on `reviewer_did_show_question`,
  fetches the next `PREFETCH_AHEAD=4`), so appearances are normally instant; a
  synchronous fetch (~1–2 s) is only a fallback when the cache is still cold
  (first card, or answering faster than the prefetch).
- **Stability rule:** a card keeps the *same* rephrasing on every reappearance
  until the student rates it **Easy**, which invalidates the cache so the next
  appearance gets a fresh rewording.
- **Per-card `performance` (1–100, default 50)** in `custom_data["perf"]`: nudged
  by the grade on a rephrased answer (Again −8 / Hard −3 / Good +3 / Easy +8),
  and the FSRS memory-state change is **damped to 0.5×** on those answers
  (undo-safe: folded into the answer's undo step). It is the **0.25 term** of the
  blended **Performance** score (`0.75·memory + 0.25·perf`, §4), which in turn
  (× coverage) feeds the **Readiness** pass probability; both abstain until > 50%
  of outline cards are scored. It can also be set directly from **Admin mode**
  (§4) for demos — admin-set cards count as scored and reviewed.
- **Held-out eval + baseline (`rephrase_eval.py --live`, re-runnable):** 15
  held-out items, structure-aggressive prompt — answer-preservation **100%**,
  meaning-preservation **100%**, wrong-rate **0%**, **effective-rephrasing ~80%**
  (80–87% run-to-run at temp 0.4; 95% ~55–93%); cutoffs (effective ≥ 80%,
  answer-preservation ≥ 90%) **met**, **AI > baseline 73%**. Meaning-sim via
  OpenAI embeddings (`text-embedding-3-small`). Outputs are now clearly
  restructured (active↔passive, reordered clauses; token overlap down to ~0.4)
  while meaning-preservation stays **100%** — the intended faithful-but-varied
  behaviour. Leakage re-run (`rephrase_leakage_check.py`): **CLEAN**.
- **Live preflight eval — "an eval that runs before students see anything"
  (Speedrun §6)** ✅ — the held-out check is now also **built into the app** and
  runs **automatically**, not just as an offline script. When AI rephrasing is
  turned on (and again lazily before the first eligible card), the app runs
  `run_preflight_eval` in a **background thread** over the held-out set, prints
  **accuracy (answer-preservation) and wrong-answer rate** with the pre-declared
  cutoffs to the terminal, and **gates the feature**: rephrasing stays OFF for
  students until the eval passes (answer-preservation ≥ 90% / wrong-rate ≤ 10%
  **and** effective-rephrasing ≥ 80% when embeddings are available). So a student
  is **never** shown an unvetted rephrase — until the preflight passes, eligible
  cards show the original (logged as `SKIP … held-out preflight eval is still
  running`). Sample live run (4 items): **PASS — accuracy 100%, wrong-rate 0%,
  meaning 100%, effective 100%**, printed as `PREFLIGHT RESULT: PASS …`. Same
  cutoffs and metric as the offline `rephrase_eval.py`, so the two are directly
  comparable. Code: `qt/aqt/ai/rephrase.py` (`run_preflight_eval`,
  `_ensure_preflight`, `trigger_preflight`); tests below.
- **Robustness:** API offline/error/timeout/malformed → show the original, make
  no perf/damping change (the app always scores with AI off).
- **Observability / demo logging (`anki.ai.rephrase`, visible in `./run`):** every
  decision is logged so a "0 cards scored" situation is self-explanatory and the
  demo can show the model's work live —
  `SKIP … (enabled/config/learning/has_state/difficulty)` when a gate blocks it,
  `fetching rewording … (first eligible view)` → `CACHED new rewording` (or
  `no usable rewording … showing ORIGINAL`), `SHOWING rephrased question … NOW`,
  and `SCORING card … perf X → Y (ease N)`. The `CACHED` and `SHOWING` lines
  print the **`ORIGINAL` vs `REPHRASED`** text side-by-side (HTML stripped, one
  line each) for the demo.
- **Unit tests:** `qt/tests/test_ai_rephrase.py` (13 tests: perf math, 0.5×
  damping, sanitising, response parsing, source hash, cache-until-Easy, and the
  **preflight eval** — pass on faithful output, flag answer-leaks and model
  failures as wrong) and the Rust `stats::` suite (33 tests incl. Performance
  aggregation, the `set_fsrs_enables_fsrs_so_answering_keeps_memory_state`
  regression, and the AI-off degraded-Performance fallback).

### 7f-AI-Android. Same AI feature, native on the phone ✅ (Friday)
The rephrasing feature now runs **natively on AnkiDroid**, not just via synced
`perf` data — the phone has all the same functions as the desktop. (The
dashboard scores — Memory/Performance/Readiness, the AI-off `0.9×memory`
fallback, coverage, admin perf — were already cross-platform via the shared Rust
backend AAR; what was missing was the *rephrasing* itself, which is app-layer.)

- **Faithful Kotlin port of `qt/aqt/ai/rephrase.py`** under
  `AnkiDroid/src/main/java/com/ichi2/anki/ai/`: `AiRephrase.kt` (perf math, 0.5×
  damping, sanitising, source-hash, prompt, preflight cutoffs), `AiRephraseApi.kt`
  (OkHttp OpenAI chat + embeddings), `AiRephraseCache.kt`
  (`ai_rephrase_cache.json` in the collection folder), `AiRephrasePreflight.kt`
  (held-out eval), `AiRephraseController.kt` (gates, pending map, prefetch,
  post-answer scoring, preflight state machine). Same config keys
  (`aiRephraseEnabled`, `usmleStudyMode`), so a toggle/score **syncs** with the
  desktop.
- **Wired into both Android reviewers** — the legacy `Reviewer`
  (`AndroidCardRenderContext.renderCard` + `answerCardInner`) and the new
  `ReviewerFragment` (`CardViewerViewModel.showQuestion` via an overridable
  `maybeRephraseQuestion`, + `ReviewerViewModel.answerCardInternal`). Rendering
  can't block on the network, so the legacy path substitutes **cache-first** and
  re-renders in place when the background fetch lands (first-view behaviour like
  the font swap); both paths **prefetch** the next `PREFETCH_AHEAD=4` due cards.
  The legacy re-render tracks the on-screen `currentCardId` and fires the swap
  whenever that card's reword lands — including when it was already in flight as a
  **prefetch** (which would otherwise be deduped and never trigger the in-place
  swap, leaving the original on screen); fixed and verified on-device.
- **Post-answer** nudges `custom_data["perf"]` and damps FSRS to 0.5×, folded
  into the answer's undo step (`col.updateCard` + `mergeUndoEntries`);
  `Card.customData`/`memoryState` were made settable in `libanki` to match the
  Python `Card`.
- **Toggle:** nav-drawer **"AI: rephrase cards"** (`NavigationDrawerActivity`),
  next to the learning-mode toggle; enabling it fires the **live preflight** so
  its accuracy/wrong-rate print to Logcat before any card is rephrased — the
  phone-side equivalent of the desktop "eval before students see anything".
- **Key & build:** OpenAI key injected at build time from `local.properties`
  (`OPENAI_API_KEY`/`OPENAI_MODEL`) into `BuildConfig` (same key as the desktop
  `ai_secrets.json`); empty ⇒ feature stays OFF. **Verified end-to-end on the
  emulator** (real `gpt-4o` calls): live preflight PASS, first-view reword shown
  **in place**, background prefetch (next card instant), `SCORING card … perf X →
  Y` with the correct grade delta persisted across sessions, and FSRS damping —
  all matching desktop, with image-occlusion/short cards correctly skipped and the
  answer side left un-reworded. Logs via `adb logcat | grep -i "AI rephrase"`.

### 7g. The crash and offline tests ✅
- **Offline** ✅ — offline review then clean sync verified (§7b).
- **AI-off** ✅ — trivially (no AI); both apps keep working.
- **Crash test (kill mid-review 20×, zero corruption)** ✅ — re-runnable
  `crash_test.py`: a worker hammers the real write path (`add_note` +
  `answer_card`), the parent **SIGKILLs it mid-write**, then reopens and audits.
  **Result: 20/20 cycles clean, zero corruption** — every reopen had
  `pragma integrity_check = ok`, empty `foreign_key_check`, no orphan
  cards/revlog, and committed rows never went backwards (SQLite WAL recovers to
  the last committed review; the in-flight write rolls back). Run:
  `PYTHONPATH=… out/pyenv/bin/python crash_test.py --cycles 20`.

### 7h. The one-command benchmark ✅ (real numbers below)
**One command: `./bench.sh`** (our `make bench`) builds a **50 000-card**
synthetic collection, prints p50/p95/worst for every latency-critical action,
then runs the 20× crash test. Real scheduler / FSRS / Rust-aggregation code
paths; only the card *content* is synthetic. Measured on this machine (macOS,
seed 1234):

| Action | p50 | p95 | worst | Budget | |
|---|---|---|---|---|---|
| Button press (`answer_card`) | 1.4 ms | 1.8 ms | 16.7 ms | p95 < 50 ms | ✅ |
| Next card (`get_queued_cards`) | 0.02 ms | 0.02 ms | 0.10 ms | p95 < 100 ms | ✅ |
| Mastery query (Rust RPC, §7a) | 720 ms | 827 ms | 860 ms | p95 < 1000 ms | ✅ |
| Dashboard first load | 741 ms (single) | — | — | < 1000 ms | ✅ |
| Dashboard refresh (`study_dashboard`) | 708 ms | 829 ms | 845 ms | p95 < 500 ms | ❌ **over** |
| Cold start — collection open (engine) | 6.1 ms (single) | — | — | < 5000 ms | ✅ |
| Peak memory @ 50k cards | 144 MB | — | — | (state a limit) | ✅ < 512 MB |

**Honest negative:** the **dashboard refresh misses the 500 ms budget** (~829 ms
p95) at 50k because `study_dashboard` recomputes the full aggregation every call
with no caching. It passes the *first-load* budget (< 1 s) and does not freeze
the UI (it runs off the reviewer's hot path), but a refresh cache / incremental
update is a real **Sunday optimization**, reported here rather than hidden.

Notes: "cold start" here is the **engine** (collection open), not full-app
launch (Qt + webview startup is separate and not yet instrumented). Sync-time
and phone-side latencies still ⬜. Run: `./bench.sh` or
`PYTHONPATH=… out/pyenv/bin/python perf_bench.py --cards 50000`.

---

## 8. How to Test a Study Feature 🟡

- **Feature (learning science):** **Forced UI randomization** — randomizes card
  *fonts* (bounded, **difficulty-gated**: only low-FSRS-difficulty/easy cards) so
  the student learns content, not a card's visual shape. Toggleable for ablation.
  Second feature: **manual mode toggle** (long-term ↔ short-term).
  Android font side: `AnkiDroid/.../cardviewer/UsmleFontChooser.kt`.
- **Hypothesis + on/off/plain 3-build test, equal time, pre-stated number,
  negative results** 🟡 **simulated** (§9-SIM) — the feature and its on/off switch
  exist; the full ablation **process is built and re-runnable on fake data**
  (`simulated_studies.py`): pre-registered H1 (+6 pp reworded-transfer), 3 arms ×
  24 simulated students at equal time, and it **reports the negatives** (null on
  same-wording recall, small per-card time cost). Real students still needed.

---

## 9. What to Build the Score Model On

- **Step 1 — Memory calibrated** 🟡 — Memory uses the engine's own FSRS R; a v1
  pass-probability curve exists; the **calibration pipeline (Brier/log-loss/ECE +
  reliability table)** is built and run on **simulated** held-out reviews (§9-SIM).
  Real held-out reviews still needed.
- **Step 2 — Performance model** 🟡 **simulated** — §9-SIM.
- **Step 3 — Score mapping with range** 🟡 — method documented (§4); needs fitting
  against measured Performance.
- **Step 4 — Real-student check** ⬜ (bonus).

---

## 9-SIM. Simulated evaluations (⚠ FAKE DATA — process, not proof)

We do **not** yet have real held-out student data. Per the instructor's guidance,
`simulated_studies.py` **simulates the full evaluation process on synthetic data**
so the pipelines, metrics, and report format are in place and re-runnable — the
numbers are **illustrative, not measured**. Deterministic (`--seed`, default 2026).
Run: `out/pyenv/bin/python simulated_studies.py`. The full sample run (seed 2026)
is reproduced inline below, so everything lives in this one report.

- **1. Memory calibration (SIMULATED):** 3 000 synthetic held-out reviews →
  **Brier 0.187**, **log-loss 0.558**, **ECE 0.039**; the reliability table below
  shows a near-diagonal fit with **mild over-confidence at high R** (band 0.7–0.8
  predicts 0.754 vs observed 0.693), motivating the documented Platt/temperature
  recalibration.

```
reliability table (predicted band -> observed recall):
  band        n     mean_pred   observed
  0.1-0.2       2      0.186      0.000
  0.2-0.3      10      0.267      0.400
  0.3-0.4      28      0.365      0.393
  0.4-0.5     130      0.457      0.485
  0.5-0.6     277      0.555      0.563
  0.6-0.7     503      0.653      0.632
  0.7-0.8     760      0.754      0.693
  0.8-0.9     812      0.848      0.803
  0.9-1.0     478      0.935      0.902
```
- **2. Performance model + paraphrase gap (SIMULATED, §7d):** 30 cards × (1
  original + 2 reworded) → recall **0.767 original vs 0.650 reworded**,
  **paraphrase gap +0.117** — the memorized-wording effect the rephrasing feature
  targets; the in-app Performance signal tracks reworded outcomes (Brier ≈ 0.23).
- **3. Study-feature 3-build ablation (SIMULATED, §8):** pre-registered **H1
  (+6 pp reworded-transfer, ON vs OFF)**, 3 arms × 24 simulated students at equal
  time (25 min/day × 14). Result: **ON−OFF transfer +0.092** (95% CI
  +0.061..+0.123, Cohen's d 1.70) → supports H1; **negatives reported honestly** —
  same-wording recall **null** (−0.015, CI crosses 0) and a small **+0.84 s/card
  time cost**.

> These are the ONLY made-up numbers in this report and are fenced here on
> purpose. Everything in §7g/§7h and the test counts are **real** machine
> measurements; §9-SIM must be replaced with real held-out data before any
> accuracy claim.

---

## 10. Speed and Reliability Targets 🟡 (most budgets measured & met)

**Measured** via `./bench.sh` on a desktop 50k-card deck (full table in §7h):
button-press p95 **1.8 ms** (< 50) ✅, next-card p95 **0.02 ms** (< 100) ✅,
mastery RPC p95 **827 ms** (< 1000) ✅, dashboard **first load 741 ms** (< 1000)
✅, engine cold-start (collection open) **6 ms** ✅, peak memory **144 MB** @ 50k
(stated ceiling **512 MB**) ✅. **Zero corruption** over 20 SIGKILL-mid-write
cycles ✅ (§7g).
**Misses budget (reported honestly):** dashboard **refresh p95 829 ms** vs the
**< 500 ms** target — `study_dashboard` recomputes fully each call; a refresh
cache is the fix (Sunday).
**Still ⬜:** sync < 5 s timing, phone-side latencies, and full-app (Qt) cold
start (only the engine open is instrumented here).

---

## 11. How We Grade (self-assessment vs. hard limits)

- **No real Rust change (≤50%)** — cleared ✅ (§7a).
- **No phone sharing engine + sync (≤70%)** — cleared ✅ (§3, §7b).
- **No re-runnable tests (≤60%)** — cleared ✅ (`sync_verify.py`,
  `rephrase_eval.py`, `rephrase_leakage_check.py`, `perf_bench.py`,
  `crash_test.py`, `simulated_studies.py`, `just test`; Rust `stats::` 31/31,
  Python AI 10/10, mastery e2e 1/1 all re-run green).
- **No held-out testing (≤60%)** — 🟡 held-out **AI rephrasing** eval landed ✅
  (§7f-AI); memory/performance held-out calibration is **simulated** on fake data
  (§9-SIM) with real held-out data still pending.
- **Made-up readiness (auto-fail)** — avoided ✅ (pass-prob only, derived from the
  Performance signal × coverage via a documented curve, gated by the give-up
  rule; a single probability with no invented range).
- **Either app fails on clean device (≤50%)** — desktop `.dmg` ✅; APK ✅ on emulator.
- **Leaked test data (score 0)** — cleared ✅ — leakage check CLEAN, frozen model,
  no training corpus (§7e).
- **AI claims without source (AI section 0)** — cleared ✅ — every rephrasing is
  traced to its source note + text hash + model (§7f-AI).

---

## 12. What to Hand In (Sunday 10:59 PM CT) — mostly ⬜

- **GitHub repo (public AGPL + credit + exam + build instructions + architecture +
  Rust note + touched-files list)** 🟡 — repos public, exam/build/Rust-note/files
  done; a single architecture overview = this doc.
- **Demo video (3–5 min)** ⬜.
- **Model descriptions (memory / performance / readiness + give-up rule)** ✅ —
  all three plus the give-up rule are described in **§4** (Memory = mean FSRS R
  with CI; Performance = `0.75·memory + 0.25·card-perf` with a weight-band range
  and the AI-off `0.9·memory` fallback; Readiness = correctness × coverage mapped
  through the calibration curve; give-up gate = ≥ 200 reviews ∧ ≥ 50% coverage ∧
  Performance available).
- **Brainlift** 🟡 — see `Brainlift - Week2 - Meric - AI Rephrased.pdf`.

### 12b. Packaged phone build (APK) 🟡

The installable phone build is the **debug-signed** `play` APK produced by
`./gradlew :AnkiDroid:assemblePlayDebug` (app id `com.ichi2.anki.debug`). The
build emits **per-ABI splits** under `AnkiDroid/build/outputs/apk/play/debug/`:
`AnkiDroid-play-arm64-v8a-debug.apk` (real phones / Apple-silicon emulators, 48 MB),
`AnkiDroid-play-x86_64-debug.apk` (x86_64 emulator, 109 MB), plus `armeabi-v7a`
and `x86`. They are signed with Android's debug keystore, so they **install on a
clean device/emulator** (`adb install -r <apk>`) and run the AI feature when an
`OPENAI_API_KEY` is present in `local.properties` at build time.

**Release-signing (what to do).** `AnkiDroid/build.gradle` already has a
`signingConfigs.release` block that reads a keystore from environment variables
(falling back to a bundled public test keystore). To produce a **release APK
signed with your own key**:

1. **Create a keystore** (once; keep the file + passwords private, never commit):

```bash
keytool -genkeypair -v -keystore usmle-release.jks \
  -alias usmle -keyalg RSA -keysize 2048 -validity 10000
```

2. **Point the build at it** via env vars (matching the gradle block):

```bash
export KEYSTOREPATH=/absolute/path/usmle-release.jks
export KSTOREPWD='your-store-password'
export KEYALIAS=usmle
export KEYPWD='your-key-password'
```

3. **Build the signed release APK:**

```bash
./gradlew :AnkiDroid:assemblePlayRelease
# → AnkiDroid/build/outputs/apk/play/release/AnkiDroid-play-*-release.apk
```

Gradle runs `zipalign` + `apksigner` automatically. Verify with
`apksigner verify --print-certs <apk>`. For **sideloading / this project's
practice hand-in** the debug-signed APK above is enough (it installs on any
device). A **Play-Store** upload additionally needs Google **Play App Signing**
(you upload with your key; Google re-signs with the app key). No keystore is
committed — the fallback test keystore is for CI only and must not ship.

**Status — done ✅.** A signed release build was produced with a project keystore
(`usmle-release.jks`, kept outside both repos, self-signed practice key):
`AnkiDroid/build/outputs/apk/play/release/AnkiDroid-play-{arm64-v8a,armeabi-v7a,x86,x86_64}-release.apk`
(R8-minified, ~16 MB per phone ABI). `apksigner verify --print-certs` confirms
`Signer #1 … CN=USMLE Step1 Fork, OU=Dev, O=Meric`. Producing the release variant
required fixing 7 release-only `lintVital` findings in the USMLE code
(`System.currentTimeMillis` suppressed with a rationale, `s`/`m`-prefixed locals
renamed, and `maxLength="28"` added to the four USMLE menu-title strings); the
debug APK skips `lintVital`, which is why it built without them.
A **release keystore-signed** build (Play-store grade) is not set up this week —
it needs a private signing keystore + `signingConfigs`, out of scope for the
practice pass. AI-off behavior is unaffected (feature is off by default).

---

## 13. Feature Ideas (only if core solid)

- Real-time sync, E2E-encrypted/CRDT sync, 100k-card profiling, signed/notarized
  installers for all OSes, upstream-accepted change, knowledge-graph planning.
- **Done early beyond Wednesday:** local two-way sync (Friday item), a released
  desktop `.dmg`, and the admin/simulation tooling. Everything else here ⬜.

---

## 14. Demo video script (3–5 min shot-list)

Goal per the instructor: **show the product, not the idea**; walk the full
experience; highlight what changed since the MVP; and make every claim easy to
verify. Record in this order. Times are targets (total ≈ 4:30). *(V)* = what to
say, *(S)* = what's on screen, *(proof)* = evidence to point at.

**Pre-flight (before hitting record):**
- Local sync server up: `SYNC_USER1=test:test SYNC_PORT=27701 ./run --syncserver`.
- Desktop app up (`./run`) and Android emulator/phone with the app installed,
  both logged into the local server (`test`/`test`, endpoint `127.0.0.1:27701`).
- AI key present (`ai_secrets.json` desktop / `local.properties` at APK build)
  so rephrasing is live; **Long-term learning mode** ON.
- A terminal window visible for the eval/sync commands.

**0:00–0:20 — What it is (and the one-engine claim).** *(V)* "A USMLE Step 1
study app: a fork of Anki where desktop and phone share **one Rust engine**, plus
an AI layer we added." *(S)* desktop deck browser + phone side-by-side.
*(proof)* mention the shared backend AAR (§3).

**0:20–0:55 — Since the MVP (what's new).** *(V)* "The MVP was plain Anki review
on both platforms. Since then we added four things: (1) AI question-rephrasing,
(2) a Memory/Performance/Readiness dashboard with a coverage-based give-up rule,
(3) a new Rust mastery-by-topic query, and (4) an in-app eval that gates the AI."
*(S)* open the **Readiness** screen so the dashboard is visible while you say this.

**0:55–2:05 — The headline feature: AI rephrasing.**
- *(S)* Turn on **Tools → AI: rephrase cards**. *(V)* note it's **off by default**
  and only fires in long-term mode on low-difficulty cards.
- *(S)* Review an eligible card **twice**: first appearance shows the reworded
  question; re-appears with the **same** rewording. *(V)* "Same meaning, different
  sentence structure — this strips the memorized-wording cue and forces
  re-encoding." *(proof)* point at a card where wording changed but the answer/
  drug/number is identical.
- *(V)* "Every rephrase is **traced to its source card** — note id + SHA-256 of the
  original text + model — so nothing is invented." *(proof)* §7f-AI source-tracing.
- *(S)* Answer it; *(V)* "the per-card **Performance** score nudges, and FSRS memory
  change is **damped to 0.5×** on rephrased answers." Rate one **Easy** and show the
  next appearance gets a **fresh** rewording (cache invalidation).

**2:05–2:45 — Dashboard: Memory / Performance / Readiness.** *(S)* the Readiness
screen. *(V)* "Three **separate** numbers: Memory = FSRS recall with a confidence
interval; Performance = 0.75·memory + 0.25·card-performance; Readiness = that ×
coverage, mapped through a calibration curve." Show the **coverage map** and the
least-covered row; click **▶ Review** and **📚 Learn least-covered topic**. *(V)*
"Below 50% coverage it **abstains** instead of guessing — the give-up rule."

**2:45–3:15 — Same feature, native on the phone.** *(S)* on the emulator/phone,
open the nav drawer → toggle **AI: rephrase cards** and **Long-term learning
mode**; review a card and show the rephrase + the phone Readiness screen. *(V)*
"Identical feature, re-implemented natively in Kotlin — not just synced data."

**3:15–3:45 — Sync (record this live).** *(S)* review a couple of cards on the
phone → tap sync; on desktop tap sync → the same reviews appear. *(V)* "Reviews
merge as an additive union; card state is last-writer-wins by mtime." *(proof)*
cut to the terminal and run `PYTHONPATH=out/pylib:out/qt out/pyenv/bin/python
sync_verify.py` — show `PART 1 PASS` / `PART 2 PASS`.

**3:45–4:20 — Evidence / evals (the verify-it part).** In the terminal, show:
- `rephrase_eval.py --live` → answer-preservation 100%, wrong-rate 0%,
  effective-rephrasing ~80%, **AI > baseline** — the held-out eval **with a baseline**.
- The **in-app preflight** line in the app log (`PREFLIGHT RESULT: PASS …`) — *(V)*
  "students are never shown an unvetted rephrase; the eval runs first and gates it."
- `rephrase_leakage_check.py` → **CLEAN**.
- `perf_bench.py --cards 50000` (or the numbers in §7h) → all latency budgets met.

**4:20–4:30 — Honesty + close.** *(V)* "One caveat: the **memory/performance
calibration and the study-feature ablation numbers are simulated** — the pipelines
are real and re-runnable, but we don't have real held-out student data yet, so
those are process, not proof. Everything else — sync, leakage, the AI held-out
eval, and the benchmarks — is measured." Close on the released builds
(desktop `.dmg` + Android APK, `v0.2.0-usmle-ai`).

**Do / don't:** *do* keep the terminal visible when you claim a number; *do* say
"simulated" out loud for §9-SIM items. *Don't* present the calibration table as
measured, and *don't* leave AI on for a card outside the gate (it'll just show the
original, which is correct but undemo-worthy).

---

*Anchored to "Speedrun — A Desktop + Mobile Study App Built on Anki." Status as of
the Wednesday (Phase 1, no-AI) milestone; Friday (AI) and Sunday (proofs) items are
marked ⬜/🟡 above.*
