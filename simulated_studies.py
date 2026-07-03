"""SIMULATED evaluations for the Sunday "prove it" items (PRD §9, §7d, §8).

⚠️  ALL DATA HERE IS SYNTHETIC / MADE-UP.  We do not yet have real held-out
student data, so — per the instructor's guidance — this script *simulates the
process end to end* with plausible fake data so the pipelines, metrics, and
report format are in place and re-runnable. Every number below is illustrative,
NOT a measured result. Deterministic given --seed.

Covers:
  1. Memory calibration        (Brier / log-loss / reliability table / ECE)
  2. Performance model + §7d    paraphrase (recall-vs-reworded) gap + Brier/AUC
  3. Study-feature 3-build      ablation (ON / OFF / plain), equal time,
                                pre-stated hypothesis, effect size, negatives

    cd Ankimprovement
    out/pyenv/bin/python simulated_studies.py
"""

from __future__ import annotations

import argparse
import math
import random
import statistics


def clip(x: float, lo: float = 1e-6, hi: float = 1 - 1e-6) -> float:
    return max(lo, min(hi, x))


def brier(preds: list[float], outcomes: list[int]) -> float:
    return statistics.fmean((p - o) ** 2 for p, o in zip(preds, outcomes))


def log_loss(preds: list[float], outcomes: list[int]) -> float:
    return -statistics.fmean(
        o * math.log(clip(p)) + (1 - o) * math.log(clip(1 - p))
        for p, o in zip(preds, outcomes)
    )


def reliability(preds: list[float], outcomes: list[int], bins: int = 10):
    rows, ece, n = [], 0.0, len(preds)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(preds) if (lo <= p < hi or (b == bins - 1 and p == hi))]
        if not idx:
            continue
        conf = statistics.fmean(preds[i] for i in idx)
        acc = statistics.fmean(outcomes[i] for i in idx)
        rows.append((lo, hi, len(idx), conf, acc))
        ece += (len(idx) / n) * abs(conf - acc)
    return rows, ece


def welch_ci(a: list[float], b: list[float]):
    """Return (mean_diff, lo, hi, cohen_d) for a - b, 95% normal-approx CI."""
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va, vb = statistics.pvariance(a), statistics.pvariance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    diff = ma - mb
    pooled_sd = math.sqrt((va + vb) / 2) or 1e-9
    return diff, diff - 1.96 * se, diff + 1.96 * se, diff / pooled_sd


# ---------------------------------------------------------------------------
# 1. Memory calibration
# ---------------------------------------------------------------------------
def sim_memory_calibration(rng: random.Random, n: int = 3000) -> None:
    print("\n" + "=" * 74)
    print("1. MEMORY CALIBRATION  (SIMULATED held-out reviews)")
    print("=" * 74)
    preds, outcomes = [], []
    for _ in range(n):
        # predicted FSRS retrievability the model showed
        p = clip(rng.betavariate(6, 2))  # skews high, like a studied deck
        # true recall is slightly LOWER at the high end (mild overconfidence)
        true_p = clip(p - 0.05 * p)
        preds.append(p)
        outcomes.append(1 if rng.random() < true_p else 0)
    print(f"  n = {n} held-out reviews (synthetic)")
    print(f"  Brier score : {brier(preds, outcomes):.4f}   (0 = perfect, 0.25 = coin flip)")
    print(f"  Log-loss    : {log_loss(preds, outcomes):.4f}")
    rows, ece = reliability(preds, outcomes)
    print(f"  ECE         : {ece:.4f}")
    print("  reliability table (predicted band -> observed recall):")
    print("    band        n     mean_pred   observed")
    for lo, hi, cnt, conf, acc in rows:
        print(f"    {lo:.1f}-{hi:.1f}  {cnt:>6}   {conf:8.3f}   {acc:8.3f}")
    print("  Interpretation: near-diagonal but mild over-confidence at high R;")
    print("  a Platt/temperature recalibration is the documented Sunday fix.")


# ---------------------------------------------------------------------------
# 2. Performance model + §7d paraphrase gap
# ---------------------------------------------------------------------------
def sim_performance_and_paraphrase(rng: random.Random, cards: int = 30, reworded: int = 2) -> None:
    print("\n" + "=" * 74)
    print("2. PERFORMANCE MODEL + PARAPHRASE GAP  (SIMULATED, §7d)")
    print("=" * 74)
    orig_hits = orig_n = rw_hits = rw_n = 0
    perf_preds, rw_outcomes = [], []
    per_card = []
    for _ in range(cards):
        mastery = clip(rng.betavariate(5, 2))          # latent true mastery
        transfer_penalty = rng.uniform(0.08, 0.18)      # reworded is harder
        # original wording
        o = 1 if rng.random() < mastery else 0
        orig_hits += o
        orig_n += 1
        # reworded variants
        card_rw = []
        for _ in range(reworded):
            hit = 1 if rng.random() < clip(mastery - transfer_penalty) else 0
            rw_hits += hit
            rw_n += 1
            card_rw.append(hit)
            # the in-app Performance signal (perf/100) as the predictor
            perf_preds.append(clip(0.5 + 0.4 * (mastery - transfer_penalty)))
            rw_outcomes.append(hit)
        per_card.append((mastery, o, card_rw))
    orig_acc, rw_acc = orig_hits / orig_n, rw_hits / rw_n
    print(f"  {cards} cards x (1 original + {reworded} reworded) questions (synthetic)")
    print(f"  recall on ORIGINAL wording : {orig_acc:.3f}")
    print(f"  recall on REWORDED wording : {rw_acc:.3f}")
    print(f"  >> paraphrase gap          : {orig_acc - rw_acc:+.3f}  "
          f"(memorized-wording effect; the feature targets this)")
    print(f"  Performance-signal vs reworded outcome: "
          f"Brier {brier(perf_preds, rw_outcomes):.4f}, "
          f"accuracy@0.5 {statistics.fmean([1 if (p>=.5)==bool(o) else 0 for p,o in zip(perf_preds, rw_outcomes)]):.3f}")
    print("  Interpretation: reworded recall sits ~0.1-0.15 below same-wording")
    print("  recall — exactly the transfer gap the rephrasing feature attacks.")


# ---------------------------------------------------------------------------
# 3. Study-feature 3-build ablation
# ---------------------------------------------------------------------------
def sim_ablation(rng: random.Random, per_arm: int = 24, days: int = 14) -> None:
    print("\n" + "=" * 74)
    print("3. STUDY-FEATURE ABLATION  (SIMULATED, §8) — UI randomization")
    print("=" * 74)
    print("  Pre-registered hypothesis (stated BEFORE the sim):")
    print("    H1: font/UI randomization (ON) raises REWORDED-question transfer")
    print("        accuracy by ~+6 pp vs OFF, at equal study time (25 min/day x 14).")
    print("    H0 (expected NULL): no change in SAME-wording recall, and a small")
    print("        per-card time COST from the added visual variety.")
    print(f"  Design: 3 arms x {per_arm} simulated students, equal time.\n")

    def arm(transfer_base: float, transfer_lift: float, recall_base: float, secs_base: float):
        transfer, recall, secs = [], [], []
        for _ in range(per_arm):
            transfer.append(clip(rng.gauss(transfer_base + transfer_lift, 0.06)))
            recall.append(clip(rng.gauss(recall_base, 0.05)))
            secs.append(max(2.0, rng.gauss(secs_base, 1.0)))
        return transfer, recall, secs

    # ON gets the transfer lift; OFF/plain do not. Same-wording recall ~equal.
    on_t, on_r, on_s = arm(0.70, 0.06, 0.86, 9.5)   # ON: +transfer, slight time cost
    off_t, off_r, off_s = arm(0.70, 0.00, 0.86, 8.6)  # OFF: feature off
    pl_t, pl_r, pl_s = arm(0.70, 0.00, 0.86, 8.5)   # plain: no USMLE features

    def line(name, t, r, s):
        print(f"  {name:<7} reworded-transfer {statistics.fmean(t):.3f} | "
              f"same-wording recall {statistics.fmean(r):.3f} | "
              f"sec/card {statistics.fmean(s):.2f}")

    line("ON", on_t, on_r, on_s)
    line("OFF", off_t, off_r, off_s)
    line("plain", pl_t, pl_r, pl_s)

    diff, lo, hi, d = welch_ci(on_t, off_t)
    print(f"\n  PRIMARY  ON-OFF reworded-transfer: {diff:+.3f} "
          f"(95% CI {lo:+.3f}..{hi:+.3f}, Cohen's d {d:.2f})")
    print(f"           -> {'supports H1' if lo > 0 else 'inconclusive'} "
          f"(pre-stated target +0.06).")
    rdiff, rlo, rhi, rd = welch_ci(on_r, off_r)
    print(f"  NEGATIVE same-wording recall ON-OFF: {rdiff:+.3f} "
          f"(95% CI {rlo:+.3f}..{rhi:+.3f}) -> null, as predicted (no free lunch).")
    sdiff, slo, shi, sd = welch_ci(on_s, off_s)
    print(f"  COST     sec/card ON-OFF: {sdiff:+.2f}s "
          f"(95% CI {slo:+.2f}..{shi:+.2f}) -> small honest time cost.")
    print("  Honest reporting: the feature helps the metric it targets (transfer),")
    print("  does NOT boost same-wording recall, and adds a minor time cost.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    print("#" * 74)
    print("# SIMULATED STUDIES — ALL DATA IS SYNTHETIC (no real students yet).")
    print(f"# seed={args.seed}. Numbers are illustrative, not measured results.")
    print("#" * 74)
    sim_memory_calibration(rng)
    sim_performance_and_paraphrase(rng)
    sim_ablation(rng)
    print("\n(remember: replace with real held-out data before claiming accuracy.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
