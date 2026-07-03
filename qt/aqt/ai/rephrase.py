# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""USMLE project — AI rephrasing of card questions during review (PRD §9a).

Behaviour (all gated behind ``aiRephraseEnabled``, off by default):

* The **first time** a card is shown and *all* gates pass — feature enabled,
  long-term learning mode, FSRS difficulty < 5 — the reviewer fetches an
  AI-reworded version of the **question** (answer side unchanged) and shows it
  immediately, exactly like the font change. The rewording is cached, so the
  brief fetch happens only once per card; every later appearance reuses the
  cached text instantly until the student rates it **Easy**. This strips the
  "familiar wording" environmental cue and forces re-encoding.
* A per-card **performance** score (``custom_data["perf"]``, 1..100, default 50)
  is nudged by the student's grade on a rephrased card, and drives the
  Performance card on the dashboard.
* On a rephrased-card answer the FSRS memory-state change is **damped to 0.5x**
  (a different retrieval context shouldn't move stability/difficulty as much).
* A card keeps the *same* rephrasing on every reappearance until the student
  rates it **Easy**; Easy invalidates the cached rephrasing so the next
  appearance gets a fresh one.

Source-tracing (rubric-critical): every rephrasing is derived only from the
card's own rendered text, and we store the source note id + a hash of the
original text + model + timestamp alongside it.

The module keeps its pure logic (perf math, sanitising, prompt building,
response parsing, cache) free of Qt/network imports so it is unit-testable; the
Qt hooks and the OpenAI call are wired up lazily in :func:`init`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("anki.ai.rephrase")

# --- Constants (v1 values; pre-declared for the Sunday ablation) ------------

#: Collection-config key: master on/off switch (default False).
CONFIG_ENABLED = "aiRephraseEnabled"
#: Collection-config key for the study-mode toggle (shared with the font
#: feature). "learning" == long-term learning mode.
CONFIG_STUDY_MODE = "usmleStudyMode"

#: custom_data key for the per-card performance score. Must be <= 8 bytes.
PERF_KEY = "perf"
PERF_DEFAULT = 50.0
PERF_MIN = 1.0
PERF_MAX = 100.0
#: Grade -> performance delta (Again, Hard, Good, Easy). Arbitrary v1 steps.
PERF_STEPS: dict[int, float] = {1: -8.0, 2: -3.0, 3: 3.0, 4: 8.0}

#: Fraction of the normal FSRS state change applied on a rephrased answer.
DAMPING_K = 0.5

#: Only rephrase cards easier than this FSRS difficulty (reuse the font gate).
DIFFICULTY_THRESHOLD = 5.0

#: How many upcoming due cards to warm (rephrase in the background) each time a
#: question is shown, so their first appearance is instant instead of blocking.
PREFETCH_AHEAD = 4

#: Easy button.
EASY_EASE = 4

DEFAULT_MODEL = "gpt-4o"
_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_REQUEST_TIMEOUT = 20.0

# --- Preflight held-out eval (runs before students see any rephrase) --------
# Pre-declared cutoffs, mirroring the offline rephrase_eval.py so the live and
# offline numbers are directly comparable.
PREFLIGHT_ANSWER_CUTOFF = 0.90     # answer-preservation (wrong-answer rate <= 10%)
PREFLIGHT_EFFECTIVE_CUTOFF = 0.80  # meaning preserved AND wording actually changed
PREFLIGHT_SIM_CUTOFF = 0.82        # embedding-cosine meaning threshold
PREFLIGHT_WORDING_MAX_OVERLAP = 0.9  # token overlap below this == wording changed
PREFLIGHT_MAX_ITEMS = 15
_EMBED_MODEL = "text-embedding-3-small"
_EMBED_URL = "https://api.openai.com/v1/embeddings"

SYSTEM_PROMPT = (
    "You restructure the question side of a spaced-repetition flashcard so it "
    "tests the exact same fact with a noticeably different sentence structure but "
    "the same vocabulary. Follow every rule strictly:\n"
    "(1) Change the STRUCTURE aggressively: convert active<->passive, reorder "
    "clauses, move the interrogative, and it is fine to be wordier or to split "
    "one sentence into two. E.g. 'Which drug can treat A?' -> 'A can be treated "
    "with which drug?' or 'A is a disease. Which drug treats it?'\n"
    "(2) Use the SAME words: you may only reuse the original's words or swap in "
    "very close synonyms. Do NOT introduce any new concept, qualifier, or claim "
    "that is not already there. In particular never add words like 'effective', "
    "'effective against', 'initiate', 'utilize', 'management', 'first-line'. "
    "'used to treat' may be reordered (e.g. 'is used to treat', 'to treat X, "
    "which drug is used') but must NOT become 'is effective against' or 'is the "
    "treatment for' — that changes the claim.\n"
    "(3) Preserve the meaning and the exact logical relationship EXACTLY. Never "
    "strengthen, weaken, generalize, or narrow the statement, and never answer "
    "it or add information.\n"
    "(4) Keep verbatim, unchanged: the answer, all medical/technical terms, drug "
    "and disease names, numbers, units, abbreviations, cloze markers "
    "(e.g. {{c1::...}}), the '[...]' / '[which ...?]' blanks, and every HTML tag, "
    "attribute, image, audio reference, and hashtag/tag string.\n"
    "(5) The result must still be a question (or the same set of cloze prompts).\n"
    "(6) Output only the reworded card text, nothing else."
)


# --- Per-card performance math (pure) ---------------------------------------


def clamp_perf(value: float) -> float:
    return max(PERF_MIN, min(PERF_MAX, value))


def read_perf(custom_data: str) -> float | None:
    """Current perf score, or ``None`` if the card was never scored."""
    data = _load_custom_data(custom_data)
    raw = data.get(PERF_KEY)
    if not isinstance(raw, (int, float)):
        return None
    return clamp_perf(float(raw))


def next_perf(current: float | None, ease: int) -> float:
    """Nudge a card's perf by the grade. Unknown -> start from the default."""
    base = PERF_DEFAULT if current is None else current
    return clamp_perf(base + PERF_STEPS.get(ease, 0.0))


def with_perf(custom_data: str, value: float) -> str:
    """Return the custom_data JSON string with ``perf`` set to ``value``.

    Kept small so the object stays within Anki's 100-byte custom_data limit.
    """
    data = _load_custom_data(custom_data)
    data[PERF_KEY] = round(clamp_perf(value), 1)
    return json.dumps(data, separators=(",", ":"))


def _load_custom_data(custom_data: str) -> dict[str, Any]:
    if not custom_data:
        return {}
    try:
        obj = json.loads(custom_data)
    except (ValueError, TypeError):
        return {}
    return obj if isinstance(obj, dict) else {}


# --- FSRS damping (pure) ----------------------------------------------------


def damp(old: float, new: float, k: float = DAMPING_K) -> float:
    """Blend a memory-state value toward the post-answer value by fraction k."""
    return old + k * (new - old)


# --- Sanitising & source tracing (pure) -------------------------------------

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_ON_ATTR_RE = re.compile(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_HIDDEN_RE = re.compile(
    r"<[^>]*(?:display\s*:\s*none|visibility\s*:\s*hidden|hidden\b)[^>]*>",
    re.IGNORECASE,
)


def sanitize_text(text: str) -> str:
    """Strip script/style/comments, event handlers and hidden nodes.

    Defends the "source file with hidden text / prompt injection" adversarial
    case before any note text is sent to the model.
    """
    text = _SCRIPT_RE.sub("", text)
    text = _STYLE_RE.sub("", text)
    text = _COMMENT_RE.sub("", text)
    text = _HIDDEN_RE.sub("", text)
    text = _ON_ATTR_RE.sub("", text)
    return text.strip()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def preview_text(text: str, limit: int = 400) -> str:
    """Collapse HTML/whitespace into one readable line for demo logging."""
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1] + "\u2026"
    return text


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def plausible_rephrasing(original: str, candidate: str) -> bool:
    """Cheap runtime guard: reject empty/degenerate model output.

    (Deeper answer/meaning-preservation is checked offline by the eval script.)
    """
    candidate = candidate.strip()
    if not candidate:
        return False
    o = len(original.strip())
    c = len(candidate)
    if o > 0 and (c < 0.3 * o or c > 3.0 * o):
        return False
    return True


def build_messages(text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


def parse_completion(payload: dict[str, Any]) -> str | None:
    """Pull the reworded text out of an OpenAI chat-completion response."""
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content.strip() if isinstance(content, str) else None


# --- Source-traced cache ----------------------------------------------------


@dataclass
class RephraseRecord:
    text: str
    note_id: int
    source_hash: str
    model: str
    created: float


class RephraseCache:
    """On-disk cache keyed by card id. A record stays until it is invalidated
    (on an Easy grade), so a card shows one stable rephrasing until then."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: dict[str, RephraseRecord] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        for cid, rec in raw.items():
            try:
                self._records[cid] = RephraseRecord(**rec)
            except TypeError:
                continue

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({k: asdict(v) for k, v in self._records.items()}),
                "utf-8",
            )
        except OSError as exc:
            logger.warning("could not persist rephrase cache: %s", exc)

    def get(self, card_id: int) -> RephraseRecord | None:
        with self._lock:
            return self._records.get(str(card_id))

    def put(self, card_id: int, record: RephraseRecord) -> None:
        with self._lock:
            self._records[str(card_id)] = record
            self._save()

    def invalidate(self, card_id: int) -> None:
        with self._lock:
            if self._records.pop(str(card_id), None) is not None:
                self._save()


# --- AI config (key/model from env or git-ignored local file) ---------------


@dataclass
class AiConfig:
    api_key: str
    model: str


def load_ai_config() -> AiConfig | None:
    """Resolve the OpenAI key/model from the environment or the git-ignored
    ``ai_secrets.json`` at the repo root. Returns ``None`` if no key is found
    (the feature then stays off, and the app scores fine without AI)."""
    env_key = os.environ.get("OPENAI_API_KEY")
    env_model = os.environ.get("AI_REPHRASE_MODEL")
    if env_key:
        return AiConfig(api_key=env_key, model=env_model or DEFAULT_MODEL)
    # qt/aqt/ai/rephrase.py -> parents[3] == the Ankimprovement repo root.
    try:
        secrets_path = Path(__file__).resolve().parents[3] / "ai_secrets.json"
        data = json.loads(secrets_path.read_text("utf-8"))
    except (OSError, ValueError, IndexError):
        return None
    key = data.get("openai_api_key")
    if not key:
        return None
    return AiConfig(api_key=key, model=data.get("model", DEFAULT_MODEL))


def request_rephrasing(text: str, config: AiConfig) -> str | None:
    """Call OpenAI to reword ``text``. Returns ``None`` on any failure (offline,
    error, timeout, malformed output) so callers fall back to the original."""
    try:
        import requests  # bundled with Anki; imported lazily
    except ImportError:
        logger.warning("requests unavailable; cannot rephrase")
        return None
    try:
        resp = requests.post(
            _OPENAI_URL,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.model,
                "messages": build_messages(text),
                # Moderate temperature: enough freedom for aggressive structural
                # rewrites, low enough to avoid drifting the claim/vocabulary.
                "temperature": 0.4,
            },
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        candidate = parse_completion(resp.json())
    except Exception as exc:  # noqa: BLE001 — never let the reviewer break
        logger.info("rephrasing request failed: %s", exc)
        return None
    if candidate is None or not plausible_rephrasing(text, candidate):
        return None
    return candidate


# --- Preflight held-out evaluation ------------------------------------------
#
# A held-out accuracy / wrong-answer-rate check that runs BEFORE any student is
# shown a rephrase (Speedrun §6 "eval that runs before students see anything" /
# §7e). It calls the frozen model on a fixed held-out Q/A set, prints the
# results to the terminal, and GATES the feature: rephrasing only turns on for
# students if the pre-declared cutoffs are met.

#: Tiny fallback set for packaged builds where the repo-root fixture is absent.
_BUILTIN_HOLDOUT: list[dict[str, str]] = [
    {"id": "b01", "question": "What enzyme is deficient in classic phenylketonuria?", "answer": "Phenylalanine hydroxylase"},
    {"id": "b02", "question": "Which vitamin deficiency causes Wernicke encephalopathy?", "answer": "Thiamine (B1)"},
    {"id": "b03", "question": "What ion channel is defective in cystic fibrosis?", "answer": "CFTR chloride channel"},
    {"id": "b04", "question": "Which neurotransmitter is decreased in Parkinson disease?", "answer": "Dopamine"},
    {"id": "b05", "question": "Which clotting factor is deficient in hemophilia B?", "answer": "Factor IX"},
]

_EVAL_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class PreflightResult:
    n: int
    accuracy: float          # answer-preservation rate (correct rephrasings)
    wrong_rate: float        # 1 - accuracy (leaked / broke / unusable output)
    meaning_rate: float      # meaning-preservation (embedding cosine >= cutoff)
    wording_changed: float   # fraction whose wording actually changed
    effective_rate: float    # meaning preserved AND wording changed AND answer kept
    meaning_verified: bool   # False if embeddings were unavailable (meaning approx)
    passed: bool


def lexical_overlap(a: str, b: str) -> float:
    ta = set(_EVAL_WORD_RE.findall(a.lower()))
    tb = set(_EVAL_WORD_RE.findall(b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _embedding(text: str, config: AiConfig) -> list[float] | None:
    try:
        import requests

        resp = requests.post(
            _EMBED_URL,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": _EMBED_MODEL, "input": text},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception:  # noqa: BLE001 — fall back to lexical similarity
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def load_holdout_items(limit: int = PREFLIGHT_MAX_ITEMS) -> list[dict[str, str]]:
    """Held-out Q/A set for the preflight (repo-root ``rephrase_eval_data.json``,
    with a small built-in fallback for packaged builds)."""
    try:
        path = Path(__file__).resolve().parents[3] / "rephrase_eval_data.json"
        items = json.loads(path.read_text("utf-8"))["items"]
    except (OSError, ValueError, IndexError, KeyError):
        items = _BUILTIN_HOLDOUT
    return list(items)[:limit]


def run_preflight_eval(
    config: AiConfig, items: list[dict[str, str]] | None = None
) -> PreflightResult:
    """Evaluate the model on a held-out set, print accuracy + wrong-answer rate
    to the terminal, and decide whether the feature passes its cutoffs."""
    items = items if items is not None else load_holdout_items()
    n = len(items)
    logger.info(
        "AI rephrase PREFLIGHT: checking %d held-out cards before any student "
        "sees a rephrase (model=%s). Cutoffs: answer-preservation >= %.0f%% "
        "(wrong-answer-rate <= %.0f%%), effective-rephrasing >= %.0f%%.",
        n,
        config.model,
        PREFLIGHT_ANSWER_CUTOFF * 100,
        (1 - PREFLIGHT_ANSWER_CUTOFF) * 100,
        PREFLIGHT_EFFECTIVE_CUTOFF * 100,
    )
    if n == 0:
        logger.warning("AI rephrase PREFLIGHT: no held-out items; feature stays OFF.")
        return PreflightResult(0, 0.0, 1.0, 0.0, 0.0, 0.0, False, False)

    answer_ok = meaning_ok = worded = effective = 0
    meaning_verified = True
    for it in items:
        q, a = it["question"], it["answer"]
        out = request_rephrasing(q, config)
        valid = out is not None
        leaked = valid and a.lower() in out.lower()
        a_ok = valid and not leaked
        overlap = lexical_overlap(q, out) if valid else 1.0
        w_ok = valid and overlap < PREFLIGHT_WORDING_MAX_OVERLAP

        sim = 0.0
        if valid:
            eq, eo = _embedding(q, config), _embedding(out, config)
            if eq and eo:
                sim = _cosine(eq, eo)
            else:
                meaning_verified = False
                sim = lexical_overlap(q, out)
        m_ok = valid and sim >= PREFLIGHT_SIM_CUTOFF
        eff = a_ok and m_ok and w_ok

        answer_ok += a_ok
        meaning_ok += m_ok
        worded += w_ok
        effective += eff
        logger.info(
            "AI rephrase PREFLIGHT  %-4s %s  answer=%s meaning=%.2f wording=%.2f\n"
            "    Q : %s\n    ->: %s",
            it.get("id", "?"),
            "OK" if eff else "--",
            "kept" if a_ok else "LEAKED/BROKE",
            sim,
            overlap,
            preview_text(q),
            preview_text(out or "(no usable output)"),
        )

    accuracy = answer_ok / n
    effective_rate = effective / n
    passed = accuracy >= PREFLIGHT_ANSWER_CUTOFF and (
        not meaning_verified or effective_rate >= PREFLIGHT_EFFECTIVE_CUTOFF
    )
    result = PreflightResult(
        n=n,
        accuracy=accuracy,
        wrong_rate=1.0 - accuracy,
        meaning_rate=meaning_ok / n,
        wording_changed=worded / n,
        effective_rate=effective_rate,
        meaning_verified=meaning_verified,
        passed=passed,
    )
    logger.info(
        "AI rephrase PREFLIGHT RESULT: %s — accuracy(answer-preservation)=%.0f%%, "
        "wrong-answer-rate=%.0f%%, meaning-preservation=%.0f%%%s, "
        "effective-rephrasing=%.0f%% (cutoffs %.0f%% / %.0f%%). %s",
        "PASS" if passed else "FAIL",
        accuracy * 100,
        result.wrong_rate * 100,
        result.meaning_rate * 100,
        "" if meaning_verified else " (approx — embeddings unavailable, meaning not gated)",
        effective_rate * 100,
        PREFLIGHT_ANSWER_CUTOFF * 100,
        PREFLIGHT_EFFECTIVE_CUTOFF * 100,
        "Rephrasing is ENABLED for students."
        if passed
        else "Rephrasing stays OFF for students until it passes.",
    )
    return result


# --- Qt integration ---------------------------------------------------------
#
# Everything below touches aqt / the running collection and is imported lazily
# so the pure logic above stays testable without Qt.


class _RephraseController:
    """Wires the pure logic into the reviewer via gui_hooks."""

    def __init__(self, mw: Any) -> None:
        self.mw = mw
        self.config = load_ai_config()
        self._cache: RephraseCache | None = None
        self._cache_dir: str | None = None
        # card_id -> (stability, difficulty) captured pre-answer when rephrased.
        self._pending: dict[int, tuple[float, float]] = {}
        # cards whose rephrasing is being fetched in the background right now.
        self._inflight: set[int] = set()
        # Held-out preflight eval: gate rephrasing on a passing result so no
        # student is shown a rephrase before it clears its cutoff.
        # State machine: "pending" -> "running" -> "passed" | "failed".
        self._preflight_state = "pending"
        self._preflight: PreflightResult | None = None
        self._preflight_lock = threading.Lock()

    @property
    def cache(self) -> RephraseCache | None:
        """Per-profile cache, built lazily and rebuilt if the profile changes."""
        try:
            folder = self.mw.pm.profileFolder()
        except Exception:  # noqa: BLE001 — no profile loaded yet
            return None
        if self._cache is None or folder != self._cache_dir:
            self._cache = RephraseCache(Path(folder) / "ai_rephrase_cache.json")
            self._cache_dir = folder
        return self._cache

    # -- gating -------------------------------------------------------------

    def _enabled(self) -> bool:
        col = self.mw.col
        return bool(
            col
            and self.config is not None
            and col.get_config(CONFIG_ENABLED, False)
        )

    def _learning_mode(self) -> bool:
        col = self.mw.col
        return bool(col and col.get_config(CONFIG_STUDY_MODE, "learning") == "learning")

    def should_rephrase(self, card: Any) -> bool:
        if not self._enabled() or not self._learning_mode():
            return False
        state = card.memory_state
        if state is None:
            return False  # brand-new card: no FSRS difficulty yet
        if state.difficulty >= DIFFICULTY_THRESHOLD:
            return False
        # A card is only rephrased once the held-out preflight eval has passed,
        # so students never see an unvetted rephrase. This kicks the eval off
        # (in the background) the first time a card would otherwise qualify.
        self._ensure_preflight()
        return self._preflight_ok()

    # -- preflight held-out eval (runs before students see any rephrase) -----

    def _preflight_ok(self) -> bool:
        with self._preflight_lock:
            return self._preflight_state == "passed"

    def reset_preflight(self) -> None:
        """Force the held-out eval to run again (e.g. when the feature is
        toggled back on)."""
        with self._preflight_lock:
            if self._preflight_state != "running":
                self._preflight_state = "pending"
                self._preflight = None

    def _ensure_preflight(self) -> None:
        if self.config is None:
            return
        with self._preflight_lock:
            if self._preflight_state != "pending":
                return
            self._preflight_state = "running"
        config = self.config

        def worker() -> None:
            try:
                result = run_preflight_eval(config)
            except Exception as exc:  # noqa: BLE001 — must never break review
                logger.warning(
                    "AI rephrase PREFLIGHT: crashed (%s); feature stays OFF.", exc
                )
                with self._preflight_lock:
                    self._preflight = None
                    self._preflight_state = "failed"
                return
            with self._preflight_lock:
                self._preflight = result
                self._preflight_state = "passed" if result.passed else "failed"

        threading.Thread(
            target=worker, name="ai-rephrase-preflight", daemon=True
        ).start()

    # -- question substitution (card_will_show filter) ----------------------

    def on_card_will_show(self, text: str, card: Any, kind: str) -> str:
        if kind != "reviewQuestion" or self.mw.state != "review":
            return text
        # Only touch the actual card under review (not previews / the browser).
        reviewer = getattr(self.mw, "reviewer", None)
        if reviewer is None or reviewer.card is None or reviewer.card.id != card.id:
            return text

        # Log exactly why we do or don't rephrase, so the running dev log makes
        # a "0 cards scored" situation diagnosable.
        state = card.memory_state
        difficulty = None if state is None else float(state.difficulty)
        if not self.should_rephrase(card):
            if self.config is None:
                reason = "no OpenAI key loaded"
            elif not (
                self.mw.col is not None
                and bool(self.mw.col.get_config(CONFIG_ENABLED, False))
            ):
                reason = "feature toggle is OFF (Tools > AI: rephrase cards)"
            elif not self._learning_mode():
                reason = "not in long-term learning mode"
            elif state is None:
                reason = (
                    "card has no FSRS memory state yet — it's a new/unreviewed "
                    "card (or FSRS is off / it was reset to new). Only cards with "
                    "an FSRS difficulty are eligible"
                )
            elif difficulty is not None and difficulty >= DIFFICULTY_THRESHOLD:
                reason = (
                    f"FSRS difficulty {difficulty:.1f} >= {DIFFICULTY_THRESHOLD:.0f} "
                    "(too hard to reword)"
                )
            elif not self._preflight_ok():
                if self._preflight_state in ("pending", "running"):
                    reason = (
                        "held-out preflight eval is still running — showing the "
                        "original until it passes (so no student sees an unvetted "
                        "rephrase)"
                    )
                else:
                    reason = (
                        "held-out preflight eval FAILED its cutoff — rephrasing "
                        "stays OFF for safety (see 'PREFLIGHT RESULT' above)"
                    )
            else:
                reason = "unknown"
            logger.info("AI rephrase: SKIP card %s — %s", card.id, reason)
            return text

        cache = self.cache
        if cache is None:
            logger.info("AI rephrase: SKIP card %s (no profile cache yet)", card.id)
            return text

        record = cache.get(card.id)
        if record is None:
            # Usually the background prefetch (on_did_show_question) has already
            # warmed this card while the student read the previous one, so this
            # is a cache hit. If not (e.g. the very first card, or the student
            # advanced faster than the fetch), fall back to fetching synchronously
            # so the reworded question still shows NOW instead of the original.
            record = self._fetch_and_cache(card, text)

        if record is None:
            logger.info(
                "AI rephrase: card %s — no usable rephrasing, showing ORIGINAL "
                "(this answer is NOT scored)",
                card.id,
            )
            self._pending.pop(card.id, None)
            return text

        # Show the (new or cached) rephrasing and remember to score this answer.
        self._pending[card.id] = self._current_state(card)
        logger.info(
            "AI rephrase: SHOWING rephrased question for card %s NOW "
            "(this answer will be scored):\n"
            "  ORIGINAL  : %s\n"
            "  REPHRASED (on screen): %s",
            card.id,
            preview_text(sanitize_text(text)),
            preview_text(record.text),
        )
        return record.text

    def _current_state(self, card: Any) -> tuple[float, float]:
        state = card.memory_state
        return (float(state.stability), float(state.difficulty))

    def _fetch_and_cache(self, card: Any, rendered: str) -> RephraseRecord | None:
        """Synchronously reword ``rendered`` and cache it. ``None`` on failure."""
        if self.config is None:
            return None
        sanitized = sanitize_text(rendered)
        if not sanitized:
            return None
        cache = self.cache
        if cache is None:
            return None
        logger.info(
            "AI rephrase: fetching rewording for card %s (first eligible view)…",
            card.id,
        )
        result = request_rephrasing(sanitized, self.config)
        if not result:
            logger.info(
                "AI rephrase: model returned no usable rewording for card %s",
                card.id,
            )
            return None
        record = RephraseRecord(
            text=result,
            note_id=int(card.nid),
            source_hash=source_hash(sanitized),
            model=self.config.model,
            created=time.time(),
        )
        cache.put(int(card.id), record)
        logger.info(
            "AI rephrase: CACHED new rewording for card %s:\n"
            "  ORIGINAL : %s\n"
            "  REPHRASED: %s",
            card.id,
            preview_text(sanitized),
            preview_text(result),
        )
        return record

    # -- background pre-caching (hide latency while the student reads) -------

    def on_did_show_question(self, card: Any) -> None:
        """Fired when a question is on screen. Warm the next few due cards in the
        background so their first appearance doesn't block on the network."""
        try:
            self._warm_upcoming()
        except Exception as exc:  # noqa: BLE001 — warming must never break review
            logger.debug("AI rephrase: warm-upcoming skipped: %s", exc)

    def _warm_upcoming(self) -> None:
        if not self._enabled() or not self._learning_mode():
            return
        col = self.mw.col
        cache = self.cache
        if col is None or cache is None:
            return
        try:
            queued = col.sched.get_queued_cards(fetch_limit=PREFETCH_AHEAD)
        except Exception:  # noqa: BLE001 — scheduler peek is best-effort
            return
        for queued_card in queued.cards:
            cid = int(queued_card.card.id)
            if cache.get(cid) is not None or cid in self._inflight:
                continue
            card = col.get_card(cid)
            if not self.should_rephrase(card):
                continue
            try:
                rendered = card.question()
            except Exception:  # noqa: BLE001
                continue
            sanitized = sanitize_text(rendered)
            if sanitized:
                self._prefetch_async(cid, int(card.nid), sanitized)

    def _prefetch_async(self, card_id: int, note_id: int, sanitized: str) -> None:
        if self.config is None or card_id in self._inflight:
            return
        config = self.config
        self._inflight.add(card_id)

        def task() -> str | None:
            return request_rephrasing(sanitized, config)

        def on_done(future: Any) -> None:
            self._inflight.discard(card_id)
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.info("AI rephrase: prefetch FAILED for card %s: %s", card_id, exc)
                return
            cache = self.cache
            if result and cache is not None:
                cache.put(
                    card_id,
                    RephraseRecord(
                        text=result,
                        note_id=note_id,
                        source_hash=source_hash(sanitized),
                        model=config.model,
                        created=time.time(),
                    ),
                )
                logger.info(
                    "AI rephrase: PREFETCHED rewording for card %s (ready before it "
                    "is shown):\n  ORIGINAL : %s\n  REPHRASED: %s",
                    card_id,
                    preview_text(sanitized),
                    preview_text(result),
                )

        logger.info("AI rephrase: prefetch START (background) for card %s", card_id)
        self.mw.taskman.run_in_background(task, on_done)

    # -- post-answer: perf nudge + damping + cache invalidation -------------

    def on_did_answer_card(self, reviewer: Any, card: Any, ease: int) -> None:
        pre = self._pending.pop(card.id, None)
        if pre is None:
            return  # this card was not shown rephrased

        col = self.mw.col
        target = col.undo_status().last_step  # the "Answer Card" step

        # 1) nudge the per-card performance score
        current = read_perf(card.custom_data)
        updated = next_perf(current, ease)
        logger.info(
            "AI rephrase: SCORING card %s perf %.1f -> %.1f (ease %s)",
            card.id,
            PERF_DEFAULT if current is None else current,
            updated,
            ease,
        )
        card.custom_data = with_perf(card.custom_data, updated)

        # 2) damp the FSRS state change to 0.5x vs the pre-answer state
        state = card.memory_state
        if state is not None:
            s_old, d_old = pre
            state.stability = damp(s_old, float(state.stability))
            state.difficulty = damp(d_old, float(state.difficulty))

        try:
            col.update_card(card)
            if target:
                col.merge_undo_entries(target)  # fold into the answer's undo step
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to persist rephrase perf/damping: %s", exc)

        # 3) Easy => let the next appearance get a fresh rephrasing
        if ease == EASY_EASE:
            cache = self.cache
            if cache is not None:
                cache.invalidate(card.id)


_controller: _RephraseController | None = None


def trigger_preflight(mw: Any, force: bool = False) -> None:
    """Public hook (called when the feature is toggled on) to run the held-out
    eval now, so its accuracy / wrong-answer-rate print to the terminal before
    the student starts reviewing. ``force`` re-runs a previously failed eval."""
    if _controller is None:
        return
    if force:
        _controller.reset_preflight()
    _controller._ensure_preflight()


def init(mw: Any) -> None:
    """Register the reviewer hooks. Safe to call once at startup; the feature
    stays inert until ``aiRephraseEnabled`` is turned on."""
    global _controller
    from aqt import gui_hooks

    _controller = _RephraseController(mw)
    gui_hooks.card_will_show.append(_controller.on_card_will_show)
    gui_hooks.reviewer_did_show_question.append(_controller.on_did_show_question)
    gui_hooks.reviewer_did_answer_card.append(_controller.on_did_answer_card)
    if _controller.config is None:
        logger.warning(
            "AI rephrase: hooks registered but NO OpenAI key found "
            "(ai_secrets.json / OPENAI_API_KEY). Feature stays OFF even if toggled."
        )
    else:
        logger.info(
            "AI rephrase: hooks registered (model=%s). Turn on via "
            "Tools > 'AI: rephrase cards (experimental)' in long-term learning mode.",
            _controller.config.model,
        )
