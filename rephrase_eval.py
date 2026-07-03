"""
Held-out evaluation of the AI rephrasing feature (PRD 9a section C; Speedrun
"AI added and checked", section 6/7e).

It runs BEFORE students see anything and checks, on a held-out fixture set
(rephrase_eval_data.json):

  * answer-preservation rate  - the reworded question never leaks/changes the
    answer, and passes the runtime plausibility guard,
  * meaning-preservation rate - the reworded question still means the same
    thing (embedding cosine >= SIM_CUTOFF, with a lexical fallback offline),
  * effective-rephrasing rate (the HEADLINE, pre-declared) - meaning preserved
    AND the wording actually changed AND the answer not leaked,
  * wrong rate                - 1 - answer-preservation rate.

...and it does the same for a simpler BASELINE (naive synonym substitution) so
we can show the AI beats it. A naive baseline barely changes the wording, so it
fails the effective-rephrasing goal even though it "preserves meaning".

Pre-declared cutoffs (state the number ahead of time):
    AI effective-rephrasing rate >= 0.80  AND  AI > baseline.
    AI answer-preservation rate  >= 0.90.

Re-runnable, mirroring sync_verify.py's style:

    cd Ankimprovement
    # offline harness check (deterministic, no network, no key):
    python3 rephrase_eval.py --dry-run
    # real evaluation (uses ai_secrets.json / OPENAI_API_KEY, needs network):
    python3 rephrase_eval.py --live

Exit code 0 == all pre-declared cutoffs met.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE / "rephrase_eval_data.json"

# --- pre-declared cutoffs ---------------------------------------------------
EFFECTIVE_CUTOFF = 0.80
ANSWER_PRESERVATION_CUTOFF = 0.90
SIM_CUTOFF = 0.82  # embedding cosine (or lexical fallback) meaning threshold
WORDING_MAX_OVERLAP = 0.9  # below this token overlap == wording actually changed

EMBED_MODEL = "text-embedding-3-small"
_EMBED_URL = "https://api.openai.com/v1/embeddings"


def log(msg: str) -> None:
    print(msg, flush=True)


# Load the pure logic of the feature module directly (no Qt / no built anki).
def _load_rephrase():
    path = HERE / "qt" / "aqt" / "ai" / "rephrase.py"
    spec = importlib.util.spec_from_file_location("_rephrase_eval_mod", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rephrase = _load_rephrase()


# --- similarity -------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def lexical_overlap(a: str, b: str) -> float:
    """Jaccard token overlap (used to detect whether wording changed, and as an
    offline meaning-similarity fallback)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _embedding(text: str, api_key: str) -> list[float] | None:
    try:
        import requests

        resp = requests.post(
            _EMBED_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": EMBED_MODEL, "input": text},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
    except Exception as exc:  # noqa: BLE001
        log(f"    (embedding failed, falling back to lexical: {exc})")
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def semantic_sim(a: str, b: str, api_key: str | None) -> float:
    if api_key:
        ea, eb = _embedding(a, api_key), _embedding(b, api_key)
        if ea and eb:
            return _cosine(ea, eb)
    return lexical_overlap(a, b)


# --- baseline: naive synonym substitution -----------------------------------

_SYNONYMS = {
    "what": "which", "cause": "leads to", "causes": "leads to",
    "deficient": "lacking", "common": "frequent", "treatment": "therapy",
    "most": "the most", "responsible": "accountable", "associated": "linked",
    "defective": "faulty", "decreased": "reduced", "abnormality": "disturbance",
}


def naive_baseline(question: str) -> str:
    def sub(m: re.Match[str]) -> str:
        return _SYNONYMS.get(m.group(0).lower(), m.group(0))

    return _WORD_RE.sub(sub, question)


# --- mock rephraser for --dry-run (deterministic, no network) ---------------

def mock_ai(question: str) -> str:
    """A stand-in that genuinely reorders/rewords so the harness can be checked
    end to end without a key/network."""
    q = question.rstrip("?. ")
    return f"Consider the following: {q}. Which single answer fits best?"


# --- evaluation core --------------------------------------------------------

def evaluate(items, rephraser, api_key, label):
    n = len(items)
    answer_ok = meaning_ok = worded = effective = 0
    per_item = []
    for it in items:
        q, a = it["question"], it["answer"]
        out = rephraser(q)
        valid = bool(out) and rephrase.plausible_rephrasing(q, out or "")
        leaked = bool(out) and a.lower() in out.lower()
        overlap = lexical_overlap(q, out) if out else 1.0
        sim = semantic_sim(q, out, api_key) if out else 0.0

        a_ok = valid and not leaked
        m_ok = valid and sim >= SIM_CUTOFF
        w_ok = valid and overlap < WORDING_MAX_OVERLAP
        eff = a_ok and m_ok and w_ok

        answer_ok += a_ok
        meaning_ok += m_ok
        worded += w_ok
        effective += eff
        per_item.append((it["id"], a_ok, m_ok, w_ok, sim, overlap, out))

    log(f"\n=== {label} ===")
    for cid, a_ok, m_ok, w_ok, sim, ov, out in per_item:
        flag = "OK " if (a_ok and m_ok and w_ok) else "-- "
        log(f"  {flag}{cid}: sim={sim:.2f} overlap={ov:.2f}  {out!r}")
    return {
        "n": n,
        "answer_preservation": answer_ok / n,
        "meaning_preservation": meaning_ok / n,
        "wording_changed": worded / n,
        "effective_rephrasing": effective / n,
        "wrong_rate": 1 - answer_ok / n,
    }


def wilson_range(p: float, n: int) -> tuple[float, float]:
    """95% Wilson interval, so we report a range not a single number."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def report(name, m):
    lo, hi = wilson_range(m["effective_rephrasing"], m["n"])
    log(f"\n[{name}] n={m['n']}")
    log(f"  answer-preservation : {m['answer_preservation']:.2%}")
    log(f"  meaning-preservation: {m['meaning_preservation']:.2%}")
    log(f"  wording-changed     : {m['wording_changed']:.2%}")
    log(f"  EFFECTIVE-REPHRASING: {m['effective_rephrasing']:.2%}  "
        f"(95% range {lo:.2%}-{hi:.2%})")
    log(f"  wrong-rate          : {m['wrong_rate']:.2%}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true", help="call the real OpenAI model")
    ap.add_argument("--dry-run", action="store_true", help="offline harness check")
    args = ap.parse_args()
    if not args.live and not args.dry_run:
        args.dry_run = True

    items = json.loads(DATA_PATH.read_text("utf-8"))["items"]
    log(f"Loaded {len(items)} held-out eval items from {DATA_PATH.name}.")
    log("Pre-declared cutoffs: effective-rephrasing >= "
        f"{EFFECTIVE_CUTOFF:.0%} AND AI > baseline; "
        f"answer-preservation >= {ANSWER_PRESERVATION_CUTOFF:.0%}.")

    if args.live:
        cfg = rephrase.load_ai_config()
        if cfg is None:
            log("ERROR: no OpenAI key found (ai_secrets.json / OPENAI_API_KEY).")
            return 2
        api_key = cfg.api_key
        ai_rephraser = lambda q: rephrase.request_rephrasing(q, cfg)  # noqa: E731
        log(f"Live mode: model={cfg.model}, embeddings={EMBED_MODEL}.")
    else:
        api_key = None
        ai_rephraser = mock_ai
        log("Dry-run mode: deterministic mock rephraser, lexical similarity.")

    ai = evaluate(items, ai_rephraser, api_key, "AI rephrasing")
    base = evaluate(items, naive_baseline, api_key, "Baseline (naive synonyms)")
    report("AI", ai)
    report("BASELINE", base)

    if args.dry_run:
        # Offline meaning-similarity uses a lexical fallback that cannot tell
        # semantic equivalence from literal overlap, so cutoffs are meaningless
        # here. This mode only verifies the harness runs end to end.
        log("\nDRY-RUN OK: harness executed. Run with --live for scored results.")
        return 0

    beats = ai["effective_rephrasing"] > base["effective_rephrasing"]
    passed = (
        ai["effective_rephrasing"] >= EFFECTIVE_CUTOFF
        and ai["answer_preservation"] >= ANSWER_PRESERVATION_CUTOFF
        and beats
    )
    log("\n" + ("PASS" if passed else "FAIL") + f": AI beats baseline={beats}, "
        f"effective={ai['effective_rephrasing']:.0%} "
        f"(cutoff {EFFECTIVE_CUTOFF:.0%}), "
        f"answer-preservation={ai['answer_preservation']:.0%} "
        f"(cutoff {ANSWER_PRESERVATION_CUTOFF:.0%}).")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
