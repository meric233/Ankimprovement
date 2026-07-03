// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Study dashboard (USMLE Step 1 project): the three honest scores.
//!
//! * **Memory** — aggregated current FSRS recall over studied cards, with a
//!   confidence interval that narrows as more reviews accumulate.
//! * **Coverage** — fraction of outline cards seen at least once.
//! * **Readiness** — probability of passing, projected to one or more exam
//!   horizons (default today + 5 days with no studying). Uses both R and
//!   stability, so "crammed" high-R/low-S cards are correctly discounted as the
//!   horizon grows. Abstains under the give-up rule.
//!
//! All three are computed in Rust so the desktop and the Android companion can
//! share the same tested numbers.

use std::collections::HashMap;

use anki_proto::stats::DashboardCoverage;
use anki_proto::stats::DashboardMemory;
use anki_proto::stats::DashboardMemoryHorizon;
use anki_proto::stats::DashboardPerformance;
use anki_proto::stats::DashboardPerformanceHorizon;
use anki_proto::stats::DashboardReadiness;
use anki_proto::stats::StudyDashboardRequest;
use anki_proto::stats::StudyDashboardResponse;
use anki_proto::stats::TopicMastery;
use fsrs::FSRS;
use fsrs::FSRS5_DEFAULT_DECAY;

use crate::card::CardType;
use crate::prelude::*;
use crate::search::SortMode;
use crate::stats::mastery::topic_for_tag;
use crate::stats::mastery::MATURE_INTERVAL_DAYS;
use crate::tags::split_tags;

const DEFAULT_MIN_GRADED_REVIEWS: u32 = 200;
const DEFAULT_MIN_COVERAGE: f32 = 0.5;
const DEFAULT_HORIZONS_DAYS: [u32; 3] = [0, 5, 10];
const SECS_PER_DAY: u32 = 86_400;
/// Per-card performance signal (custom_data key "perf"). See
/// [`DashboardPerformance`]. Cards that have never been performance-scored
/// contribute this default; the score band is 1..100.
const PERF_DEFAULT: f64 = 50.0;
const PERF_MIN: f64 = 1.0;
const PERF_MAX: f64 = 100.0;
/// Performance abstains until strictly more than this fraction of outline cards
/// have an actual (non-default) perf value written by a rephrased-card answer.
const DEFAULT_MIN_PERF_FRACTION: f32 = 0.5;
/// custom_data key holding the per-card performance score.
const PERF_KEY: &str = "perf";

/// Blended Performance = `PERF_W_MEMORY` * memory retrievability +
/// (1 - `PERF_W_MEMORY`) * card performance score. The mean uses 0.75/0.25; the
/// uncertainty band comes from ranging the memory weight over [0.65, 0.85]
/// (card weight = 1 - memory weight), i.e. the pairs (0.65,0.35)..(0.85,0.15).
///
/// These weights are an ARBITRARY qualitative prior (memory dominates transfer,
/// but rephrasing performance matters); the honest weights can only be fit from
/// real held-out testing data, which we do not yet have.
const PERF_W_MEMORY: f64 = 0.75;
const PERF_W_MEMORY_LOW: f64 = 0.65;
const PERF_W_MEMORY_HIGH: f64 = 0.85;

/// AI-OFF fallback. With AI rephrasing disabled there is no per-card
/// performance signal (no card is ever rephrased & scored), so the blended
/// Performance score cannot be computed. Rather than hide Performance and
/// Readiness entirely, we fall back to `PERF_AI_OFF_W_MEMORY` * memory
/// retrievability — an explicitly *compromised*, less-accurate estimate that is
/// flagged as `degraded` so the UI can warn the student. The 0.9 factor is a
/// deliberate haircut: with no evidence about transfer under rephrasing we
/// assume performance is a little worse than raw recall.
const PERF_AI_OFF_W_MEMORY: f64 = 0.9;
/// Collection-config flag (set from Python) that turns AI rephrasing on.
const AI_ENABLED_CONFIG_KEY: &str = "aiRephraseEnabled";
const AI_OFF_NOTE: &str = "AI rephrasing is off, so there is no per-card \
    performance signal. Performance is a compromised estimate (0.9 × Memory) \
    and is less accurate — enable AI rephrasing for a true Performance score.";

/// Parse the per-card performance score from a card's `custom_data` JSON.
///
/// Returns `Some(clamped 1..100)` when the card has been performance-scored,
/// or `None` when it has not (so callers can both count "scored" cards and use
/// the default for the mean).
fn perf_from_custom_data(custom_data: &str) -> Option<f64> {
    if custom_data.is_empty() {
        return None;
    }
    let value: serde_json::Value = serde_json::from_str(custom_data).ok()?;
    let perf = value.get(PERF_KEY)?.as_f64()?;
    Some(perf.clamp(PERF_MIN, PERF_MAX))
}
/// A studied card counts as "fragile" (over-confidence risk) when it is highly
/// recallable right now but not yet durably learned.
const FRAGILE_RECALL: f32 = 0.9;

#[derive(Default)]
struct TopicAcc {
    total: u32,
    seen: u32,
    mastered: u32,
    recall_sum: f64,
}

impl Collection {
    pub fn study_dashboard(
        &mut self,
        req: &StudyDashboardRequest,
    ) -> Result<StudyDashboardResponse> {
        let depth = req.topic_depth.max(1) as usize;
        let prefix = req.tag_prefix.as_str();
        let min_reviews = if req.min_graded_reviews == 0 {
            DEFAULT_MIN_GRADED_REVIEWS
        } else {
            req.min_graded_reviews
        };
        let min_coverage = if req.min_coverage <= 0.0 {
            DEFAULT_MIN_COVERAGE
        } else {
            req.min_coverage
        };
        let horizons: Vec<u32> = if req.readiness_horizons_days.is_empty() {
            DEFAULT_HORIZONS_DAYS.to_vec()
        } else {
            req.readiness_horizons_days.clone()
        };

        let graded_reviews = self.storage.graded_review_count()?;
        // When AI rephrasing is off there is no per-card performance signal, so
        // Performance falls back to a compromised 0.9 × Memory estimate.
        let ai_enabled = self
            .get_config_optional::<bool, _>(AI_ENABLED_CONFIG_KEY)
            .unwrap_or(false);

        let guard = self.search_cards_into_table(&req.search, SortMode::NoOrder)?;
        let timing = guard.col.timing_today()?;
        let cards = guard.col.storage.all_searched_cards()?;

        let mut note_ids: Vec<NoteId> = cards.iter().map(|c| c.note_id).collect();
        note_ids.sort_unstable();
        note_ids.dedup();
        let tags_by_note: HashMap<NoteId, Vec<String>> = guard
            .col
            .storage
            .get_note_tags_by_id_list(&note_ids)?
            .into_iter()
            .map(|nt| (nt.id, split_tags(&nt.tags).map(str::to_string).collect()))
            .collect();

        let fsrs = FSRS::new(None)?;

        // Per-card aggregates (each outline card counted once).
        let mut total_outline_cards: u32 = 0;
        let mut cards_seen: u32 = 0;
        let mut memory_n: u32 = 0;
        let mut memory_sum: f64 = 0.0;
        let mut memory_sumsq: f64 = 0.0;
        // Mean recall over studied cards projected forward at each horizon
        // (ungated over-confidence view). Same projections as readiness, but
        // averaged over studied cards only.
        let mut memory_horizon_sums: Vec<f64> = vec![0.0; horizons.len()];
        let mut fragile_count: u32 = 0;
        // Card performance score: mean per-card perf over ALL outline cards
        // (unscored cards contribute the default), plus a count of scored cards.
        let mut perf_sum: f64 = 0.0;
        let mut perf_scored: u32 = 0;
        // Per-topic aggregates (a card counts once per distinct topic).
        let mut topics_map: HashMap<String, TopicAcc> = HashMap::new();

        let empty = Vec::new();
        for card in &cards {
            let card_tags = tags_by_note.get(&card.note_id).unwrap_or(&empty);
            let mut card_topics: Vec<String> = card_tags
                .iter()
                .filter_map(|tag| topic_for_tag(tag, prefix, depth))
                .collect();
            card_topics.sort_unstable();
            card_topics.dedup();
            if card_topics.is_empty() {
                continue; // not part of the outline
            }

            total_outline_cards += 1;
            if card.reps > 0 {
                cards_seen += 1;
            }

            // Card performance score: per-card perf (default when never scored)
            // as a 0..1 fraction, plus a count of the cards actually scored.
            let perf_value = match perf_from_custom_data(&card.custom_data) {
                Some(p) => {
                    perf_scored += 1;
                    p
                }
                None => PERF_DEFAULT,
            } / PERF_MAX;
            perf_sum += perf_value;

            // Compute current + projected recall once per card.
            let (current_recall, projected) = match card.memory_state {
                Some(state) => {
                    let elapsed = card.seconds_since_last_review(&timing).unwrap_or_default();
                    let decay = card.decay.unwrap_or(FSRS5_DEFAULT_DECAY);
                    let fsrs_state = state.into();
                    let cur = fsrs.current_retrievability_seconds(fsrs_state, elapsed, decay);
                    let proj: Vec<f32> = horizons
                        .iter()
                        .map(|h| {
                            let secs = elapsed.saturating_add(h.saturating_mul(SECS_PER_DAY));
                            fsrs.current_retrievability_seconds(fsrs_state, secs, decay)
                        })
                        .collect();
                    (Some((cur, state.stability)), proj)
                }
                // New / unseen card: contributes 0 recall at every horizon.
                None => (None, vec![0.0; horizons.len()]),
            };

            if let Some((cur, stability)) = current_recall {
                memory_n += 1;
                memory_sum += cur as f64;
                memory_sumsq += (cur as f64) * (cur as f64);
                // Memory decay view: same projections, over studied cards only.
                for (i, pr) in projected.iter().enumerate() {
                    memory_horizon_sums[i] += *pr as f64;
                }
                if cur >= FRAGILE_RECALL && stability < MATURE_INTERVAL_DAYS as f32 {
                    fragile_count += 1;
                }
            }

            let mastered =
                card.ctype == CardType::Review && card.interval >= MATURE_INTERVAL_DAYS;
            let card_current = current_recall.map(|(cur, _)| cur).unwrap_or(0.0) as f64;
            let seen = card.reps > 0;
            for topic in card_topics {
                let acc = topics_map.entry(topic).or_default();
                acc.total += 1;
                if seen {
                    acc.seen += 1;
                }
                if mastered {
                    acc.mastered += 1;
                }
                acc.recall_sum += card_current;
            }
        }

        // ---- Memory -------------------------------------------------------
        let memory = build_memory(
            memory_n,
            memory_sum,
            memory_sumsq,
            &horizons,
            &memory_horizon_sums,
        );

        // ---- Coverage -----------------------------------------------------
        let coverage_fraction = if total_outline_cards > 0 {
            cards_seen as f32 / total_outline_cards as f32
        } else {
            0.0
        };
        let coverage = DashboardCoverage {
            fraction: coverage_fraction,
            cards_seen,
            total_cards: total_outline_cards,
        };

        // ---- Performance (blended memory + card-performance signal) -------
        // Built before Readiness because Readiness is derived from it. The
        // "card performance score" is the mean per-card perf over ALL outline
        // cards (unscored cards contribute the default).
        let card_perf_score = if total_outline_cards > 0 {
            perf_sum / total_outline_cards as f64
        } else {
            0.0
        };
        let performance = build_performance(
            total_outline_cards,
            perf_scored,
            card_perf_score,
            &horizons,
            &memory_horizon_sums,
            memory_n,
            ai_enabled,
        );

        // ---- Give-up rule -------------------------------------------------
        // Readiness is derived from the blended Performance score, so on top of
        // the graded-reviews + coverage give-up rule it also requires the
        // Performance signal to be available (> 50% of outline cards scored).
        let mut blocked = Vec::new();
        if graded_reviews < min_reviews {
            blocked.push(format!(
                "Need at least {min_reviews} graded reviews (you have {graded_reviews})."
            ));
        }
        if coverage_fraction < min_coverage {
            blocked.push(format!(
                "Need at least {:.0}% outline coverage (you have {:.0}%).",
                min_coverage * 100.0,
                coverage_fraction * 100.0
            ));
        }
        if !performance.available {
            blocked.push(
                "Readiness is based on the Performance score: need more than 50% \
                 of outline cards rephrased & scored (via AI-rephrased reviews or admin)."
                    .to_string(),
            );
        }
        let readiness_available = blocked.is_empty();

        // ---- Readiness (per horizon; pass probability, no range) ----------
        // Expected exam correctness at each horizon = blended Performance ×
        // coverage (so uncovered material honestly drags it down). This is a
        // probability of passing, so it carries no separate uncertainty range.
        let readiness = if readiness_available {
            performance
                .horizons
                .iter()
                .map(|h| {
                    let correctness = (h.mean as f64 * coverage_fraction as f64) as f32;
                    build_readiness(h.days, correctness)
                })
                .collect()
        } else {
            Vec::new()
        };

        // ---- Per-topic rows + next-best-topic -----------------------------
        let mut topics: Vec<TopicMastery> = Vec::with_capacity(topics_map.len());
        let mut next_best_topic = String::new();
        let mut best_opportunity = f64::MIN;
        for (topic, acc) in topics_map.into_iter() {
            let avg = if acc.total > 0 {
                (acc.recall_sum / acc.total as f64) as f32
            } else {
                0.0
            };
            // Biggest opportunity = weakest recall scaled by how much material it
            // represents.
            let opportunity = (1.0 - avg as f64) * acc.total as f64;
            if opportunity > best_opportunity {
                best_opportunity = opportunity;
                next_best_topic = topic.clone();
            }
            topics.push(TopicMastery {
                topic,
                total_cards: acc.total,
                cards_mastered: acc.mastered,
                average_recall: avg,
                cards_seen: acc.seen,
            });
        }
        topics.sort_by(|a, b| a.topic.cmp(&b.topic));

        let fragile_fraction = if memory_n > 0 {
            fragile_count as f32 / memory_n as f32
        } else {
            0.0
        };

        Ok(StudyDashboardResponse {
            memory: Some(memory),
            coverage: Some(coverage),
            graded_reviews,
            readiness_available,
            readiness_blocked_reasons: blocked,
            readiness,
            fragile_fraction,
            topics,
            next_best_topic,
            performance: Some(performance),
        })
    }
}

fn build_memory(
    n: u32,
    sum: f64,
    sumsq: f64,
    horizons: &[u32],
    horizon_sums: &[f64],
) -> DashboardMemory {
    let nf = (n.max(1)) as f64;
    let build_horizons = |sums: &[f64]| -> Vec<DashboardMemoryHorizon> {
        horizons
            .iter()
            .zip(sums.iter())
            .map(|(&days, &s)| DashboardMemoryHorizon {
                days,
                mean_recall: if n == 0 { 0.0 } else { (s / nf) as f32 },
            })
            .collect()
    };
    if n == 0 {
        return DashboardMemory {
            mean_recall: 0.0,
            ci_low: 0.0,
            ci_high: 0.0,
            studied_cards: 0,
            horizons: build_horizons(horizon_sums),
        };
    }
    let mean = sum / nf;
    // Sample variance of per-card recall; standard error of the mean = sd/sqrt(n).
    let variance = (sumsq / nf - mean * mean).max(0.0);
    let std_err = (variance / nf).sqrt();
    let margin = 1.96 * std_err;
    DashboardMemory {
        mean_recall: mean as f32,
        ci_low: (mean - margin).clamp(0.0, 1.0) as f32,
        ci_high: (mean + margin).clamp(0.0, 1.0) as f32,
        studied_cards: n,
        horizons: build_horizons(horizon_sums),
    }
}

/// Build the blended Performance signal:
/// `0.75 * memory_retrievability(horizon) + 0.25 * card_perf_score`, per
/// requested horizon (today / +5d / +10d). The uncertainty band comes from
/// ranging the memory weight over [0.65, 0.85] (card weight = 1 - memory
/// weight), i.e. the pairs (0.65,0.35)..(0.85,0.15) — an ARBITRARY qualitative
/// prior, not a fitted value.
///
/// `card_perf_score` is the mean per-card perf over **all** outline cards
/// (never-scored cards contribute the default). It abstains (with reasons) until
/// strictly more than [`DEFAULT_MIN_PERF_FRACTION`] of outline cards have a real
/// perf value, so a mostly-default mean is never presented as a measurement.
fn build_performance(
    total_cards: u32,
    scored_cards: u32,
    card_perf_score: f64,
    horizons: &[u32],
    memory_horizon_sums: &[f64],
    memory_n: u32,
    ai_enabled: bool,
) -> DashboardPerformance {
    let mem_at = |i: usize| {
        if memory_n > 0 {
            memory_horizon_sums.get(i).copied().unwrap_or(0.0) / memory_n as f64
        } else {
            0.0
        }
    };

    // AI-OFF fallback: no per-card performance signal exists, so we cannot
    // compute the blended score. Instead we report a compromised, explicitly
    // degraded estimate = 0.9 × Memory (no weight band; the caveat carries the
    // uncertainty). It still needs at least one studied card to read memory.
    if !ai_enabled {
        let available = memory_n > 0;
        let mut blocked_reasons = Vec::new();
        if !available {
            blocked_reasons.push(
                "Performance needs at least one studied card to estimate from Memory \
                 (AI rephrasing is off)."
                    .to_string(),
            );
        }
        let horizons_out: Vec<DashboardPerformanceHorizon> = horizons
            .iter()
            .enumerate()
            .map(|(i, &days)| {
                let v = (PERF_AI_OFF_W_MEMORY * mem_at(i)).clamp(0.0, 1.0) as f32;
                DashboardPerformanceHorizon {
                    days,
                    mean: v,
                    // Degraded point estimate: bounds mirror the mean.
                    range_low: v,
                    range_high: v,
                }
            })
            .collect();
        let (mean, range_low, range_high) = horizons_out
            .first()
            .map(|h| (h.mean, h.range_low, h.range_high))
            .unwrap_or((0.0, 0.0, 0.0));
        return DashboardPerformance {
            available,
            mean,
            range_low,
            range_high,
            scored_cards,
            total_cards,
            blocked_reasons,
            horizons: horizons_out,
            degraded: true,
            degraded_note: AI_OFF_NOTE.to_string(),
        };
    }

    let available = total_cards > 0
        && (scored_cards as f32 / total_cards as f32) > DEFAULT_MIN_PERF_FRACTION;

    let mut blocked_reasons = Vec::new();
    if !available {
        let pct = DEFAULT_MIN_PERF_FRACTION * 100.0;
        blocked_reasons.push(format!(
            "Performance needs more than {pct:.0}% of outline cards rephrased & \
             scored (scored {scored_cards} / {total_cards})."
        ));
    }

    // memory(h) * w + card_perf * (1 - w).
    let blend = |w_mem: f64, mem: f64| w_mem * mem + (1.0 - w_mem) * card_perf_score;
    let horizons_out: Vec<DashboardPerformanceHorizon> = horizons
        .iter()
        .enumerate()
        .map(|(i, &days)| {
            let mem = mem_at(i);
            let lo = blend(PERF_W_MEMORY_LOW, mem);
            let hi = blend(PERF_W_MEMORY_HIGH, mem);
            DashboardPerformanceHorizon {
                days,
                mean: blend(PERF_W_MEMORY, mem).clamp(0.0, 1.0) as f32,
                range_low: lo.min(hi).clamp(0.0, 1.0) as f32,
                range_high: lo.max(hi).clamp(0.0, 1.0) as f32,
            }
        })
        .collect();

    let (mean, range_low, range_high) = horizons_out
        .first()
        .map(|h| (h.mean, h.range_low, h.range_high))
        .unwrap_or((0.0, 0.0, 0.0));
    DashboardPerformance {
        available,
        mean,
        range_low,
        range_high,
        scored_cards,
        total_cards,
        blocked_reasons,
        horizons: horizons_out,
        degraded: false,
        degraded_note: String::new(),
    }
}

/// Calibrated mapping from *expected exam correctness* (mean projected recall,
/// read as the fraction of questions answered correctly) to the probability of
/// **passing** the exam.
///
/// Anchored to students' self-reported outcomes after the official practice
/// exam (the pass mark is 60% correct):
///
/// | correct | pass |
/// |---------|------|
/// | 70%     | 99%  |
/// | 65%     | 95%  |
/// | 62%     | 92%  |
/// | 60%     | 65%  | (right at the cut score: near coin-flip, high variance) |
///
/// Below 60% the pass probability falls steeply toward 0; above 70% it
/// saturates toward 1. Interpolated linearly between the anchors.
fn correctness_to_pass_probability(correctness: f32) -> f32 {
    // (expected correctness, pass probability), ascending in correctness. The
    // 0.552 point makes the sub-threshold slope match the steep 60%->62% region.
    const ANCHORS: [(f32, f32); 7] = [
        (0.000, 0.00),
        (0.552, 0.00),
        (0.600, 0.65),
        (0.620, 0.92),
        (0.650, 0.95),
        (0.700, 0.99),
        (1.000, 1.00),
    ];
    let x = correctness.clamp(0.0, 1.0);
    for w in ANCHORS.windows(2) {
        let (x0, y0) = w[0];
        let (x1, y1) = w[1];
        if x <= x1 {
            if (x1 - x0).abs() < f32::EPSILON {
                return y0;
            }
            let t = (x - x0) / (x1 - x0);
            return (y0 + t * (y1 - y0)).clamp(0.0, 1.0);
        }
    }
    1.0
}

/// Build one Readiness horizon from expected exam `correctness` (blended
/// Performance × coverage). The headline is a probability of *passing*, mapped
/// through the calibrated curve; being a probability it carries **no** separate
/// uncertainty range (range fields mirror the point estimate).
fn build_readiness(horizon_days: u32, correctness: f32) -> DashboardReadiness {
    let correctness = correctness.clamp(0.0, 1.0);
    let p_pass = correctness_to_pass_probability(correctness);
    DashboardReadiness {
        horizon_days,
        p_pass,
        range_low: p_pass,
        range_high: p_pass,
        mean_projected_recall: correctness,
    }
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::card::FsrsMemoryState;
    use crate::revlog::RevlogEntry;
    use crate::revlog::RevlogId;
    use crate::revlog::RevlogReviewKind;
    use crate::tests::NoteAdder;

    const FA: &str = "#AK_Step1_v11::#FirstAid::";

    fn add_card(col: &mut Collection, tag: &str) -> CardId {
        let mut note = NoteAdder::basic(col).note();
        note.tags = vec![tag.to_string()];
        col.add_note(&mut note, DeckId(1)).unwrap();
        col.storage
            .all_cards_of_note(note.id)
            .unwrap()
            .pop()
            .unwrap()
            .id
    }

    /// Mark a card as studied with the given FSRS stability, reviewed just now.
    fn study(col: &mut Collection, cid: CardId, stability: f32, interval: u32) {
        let mut card = col.storage.get_card(cid).unwrap().unwrap();
        card.ctype = CardType::Review;
        card.reps = 1;
        card.interval = interval;
        card.memory_state = Some(FsrsMemoryState {
            stability,
            difficulty: 5.0,
        });
        card.decay = Some(FSRS5_DEFAULT_DECAY);
        card.last_review_time = Some(TimestampSecs::now());
        col.storage.update_card(&card).unwrap();
    }

    /// Write a per-card performance score into the card's custom_data.
    fn set_perf(col: &mut Collection, cid: CardId, perf: f64) {
        let mut card = col.storage.get_card(cid).unwrap().unwrap();
        card.custom_data = format!(r#"{{"perf":{perf}}}"#);
        col.storage.update_card(&card).unwrap();
    }

    /// Turn AI rephrasing on so Performance uses the blended (AI-on) signal.
    /// Without this, Performance falls back to the degraded 0.9 × Memory
    /// estimate (see `ai_off_performance_is_degraded_memory_estimate`).
    fn enable_ai(col: &mut Collection) {
        col.transact(Op::UpdateConfig, |col| {
            col.set_config(AI_ENABLED_CONFIG_KEY, &true)?;
            Ok(())
        })
        .unwrap();
    }

    fn add_graded_reviews(col: &mut Collection, cid: CardId, n: u32) {
        for i in 0..n {
            let entry = RevlogEntry {
                id: RevlogId(1_000 + i as i64),
                cid,
                button_chosen: 3,
                review_kind: RevlogReviewKind::Review,
                ..Default::default()
            };
            col.storage.add_revlog_entry(&entry, true).unwrap();
        }
    }

    fn request(horizons: &[u32], min_reviews: u32, min_coverage: f32) -> StudyDashboardRequest {
        StudyDashboardRequest {
            search: String::new(),
            tag_prefix: FA.to_string(),
            topic_depth: 1,
            readiness_horizons_days: horizons.to_vec(),
            min_graded_reviews: min_reviews,
            min_coverage,
        }
    }

    #[test]
    fn abstains_under_give_up_rule_but_shows_memory_and_coverage() {
        let mut col = Collection::new();
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 50.0, 30);
        add_card(&mut col, &format!("{FA}14_Renal::02_Physiology")); // new, unseen

        // Defaults (200 reviews, 50% coverage) are not met by this tiny collection.
        let resp = col.study_dashboard(&request(&[0, 5], 0, 0.0)).unwrap();

        assert!(!resp.readiness_available);
        assert!(resp.readiness.is_empty());
        assert!(!resp.readiness_blocked_reasons.is_empty());
        // Memory + coverage are always reported.
        let cov = resp.coverage.unwrap();
        assert_eq!(cov.total_cards, 2);
        assert_eq!(cov.cards_seen, 1);
        assert!((cov.fraction - 0.5).abs() < 1e-6);
        assert_eq!(resp.memory.unwrap().studied_cards, 1);
    }

    #[test]
    fn memory_decay_horizons_are_reported_even_when_abstaining() {
        let mut col = Collection::new();
        // Low stability => recall drops noticeably over a few days.
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 2.0, 5);

        // Give-up thresholds unmet -> readiness abstains, but Memory is reported.
        let resp = col.study_dashboard(&request(&[0, 5, 10], 0, 0.0)).unwrap();
        assert!(!resp.readiness_available);
        let mem = resp.memory.unwrap();
        // One memory horizon per requested horizon, in order.
        let days: Vec<u32> = mem.horizons.iter().map(|h| h.days).collect();
        assert_eq!(days, vec![0, 5, 10]);
        // Horizon 0 equals current mean recall; later horizons decay strictly.
        assert!((mem.horizons[0].mean_recall - mem.mean_recall).abs() < 1e-6);
        assert!(mem.horizons[0].mean_recall > mem.horizons[1].mean_recall);
        assert!(mem.horizons[1].mean_recall > mem.horizons[2].mean_recall);
        assert!(mem.horizons[2].mean_recall > 0.0);
    }

    #[test]
    fn readiness_has_horizons_and_no_range() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 2.0, 5); // low stability -> visible decay
        add_graded_reviews(&mut col, c, 5);
        set_perf(&mut col, c, 80.0); // scored -> performance available

        let resp = col.study_dashboard(&request(&[0, 5, 10], 1, 0.01)).unwrap();
        assert!(resp.readiness_available, "{:?}", resp.readiness_blocked_reasons);
        // One readiness entry per requested horizon (today / +5d / +10d).
        let days: Vec<u32> = resp.readiness.iter().map(|r| r.horizon_days).collect();
        assert_eq!(days, vec![0, 5, 10]);
        // No uncertainty range on a probability: bounds mirror the point value.
        for r in &resp.readiness {
            assert_eq!(r.range_low, r.p_pass);
            assert_eq!(r.range_high, r.p_pass);
        }
        // Expected correctness (blended Performance × coverage) decays over the
        // horizons with no study; p_pass is non-increasing.
        assert!(
            resp.readiness[0].mean_projected_recall > resp.readiness[1].mean_projected_recall
        );
        assert!(
            resp.readiness[1].mean_projected_recall >= resp.readiness[2].mean_projected_recall
        );
        assert!(resp.readiness[0].p_pass + 1e-6 >= resp.readiness[2].p_pass);
        let base = resp.readiness[0].mean_projected_recall;

        // Lower card performance lowers today's expected correctness.
        set_perf(&mut col, c, 20.0);
        let resp2 = col.study_dashboard(&request(&[0, 5, 10], 1, 0.01)).unwrap();
        assert!(resp2.readiness[0].mean_projected_recall < base);
    }

    #[test]
    fn performance_blends_memory_and_card_score_with_weight_band() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 50.0, 30); // reviewed now -> memory ~ 1.0
        set_perf(&mut col, c, 80.0); // card perf 0.8

        let resp = col.study_dashboard(&request(&[0, 5], 0, 0.0)).unwrap();
        let perf = resp.performance.unwrap();
        assert!(perf.available, "{:?}", perf.blocked_reasons);
        assert_eq!(perf.horizons.len(), 2);
        let h0 = &perf.horizons[0];
        let mem0 = resp.memory.as_ref().unwrap().horizons[0].mean_recall as f64;
        // mean = 0.75*memory + 0.25*card_perf.
        let expected = 0.75 * mem0 + 0.25 * 0.8;
        assert!((h0.mean as f64 - expected).abs() < 1e-3, "mean {}", h0.mean);
        // Band from the (0.65,0.35)..(0.85,0.15) weight pair straddles the mean.
        assert!(h0.range_low <= h0.mean && h0.mean <= h0.range_high);
        // Top-level mirrors horizon 0.
        assert_eq!(perf.mean, h0.mean);
    }

    #[test]
    fn readiness_abstains_without_performance_data() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 50.0, 30);
        add_graded_reviews(&mut col, c, 5);
        // No perf scored -> readiness abstains even though reviews+coverage pass.
        let resp = col.study_dashboard(&request(&[0], 1, 0.01)).unwrap();
        assert!(!resp.readiness_available);
        assert!(resp
            .readiness_blocked_reasons
            .iter()
            .any(|r| r.contains("Performance")));
    }

    #[test]
    fn pass_probability_is_calibrated_to_reported_outcomes() {
        // Anchor points from the students' practice-exam reports.
        assert!((correctness_to_pass_probability(0.70) - 0.99).abs() < 1e-4);
        assert!((correctness_to_pass_probability(0.65) - 0.95).abs() < 1e-4);
        assert!((correctness_to_pass_probability(0.62) - 0.92).abs() < 1e-4);
        assert!((correctness_to_pass_probability(0.60) - 0.65).abs() < 1e-4);
        // The 60% cut score maps well below the smooth trend (variance/coin-flip).
        assert!(correctness_to_pass_probability(0.60) < 0.8);
        // Just below the cut score the chance drops steeply; well below it -> ~0.
        assert!(correctness_to_pass_probability(0.58) < 0.45);
        assert!(correctness_to_pass_probability(0.50) < 1e-4);
        // Monotonic non-decreasing and bounded.
        let mut prev = 0.0;
        let mut x = 0.0;
        while x <= 1.0 {
            let p = correctness_to_pass_probability(x);
            assert!((0.0..=1.0).contains(&p));
            assert!(p + 1e-6 >= prev, "not monotonic at {x}: {p} < {prev}");
            prev = p;
            x += 0.01;
        }
    }

    #[test]
    fn default_memory_horizons_include_today_5d_and_10d() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 50.0, 30);
        add_graded_reviews(&mut col, c, 5);
        set_perf(&mut col, c, 70.0);

        // Empty horizons -> defaults [0, 5, 10] (these now drive Memory only).
        let mut req = request(&[], 1, 0.01);
        req.readiness_horizons_days = vec![];
        let resp = col.study_dashboard(&req).unwrap();
        assert!(resp.readiness_available, "{:?}", resp.readiness_blocked_reasons);
        // Readiness + Memory both report the default horizons [0, 5, 10].
        let days: Vec<u32> = resp.readiness.iter().map(|r| r.horizon_days).collect();
        assert_eq!(days, vec![0, 5, 10]);
        let mem_days: Vec<u32> = resp.memory.unwrap().horizons.iter().map(|h| h.days).collect();
        assert_eq!(mem_days, vec![0, 5, 10]);
    }

    #[test]
    fn flags_fragile_high_recall_low_stability_cards() {
        let mut col = Collection::new();
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        // Reviewed just now with low stability -> R ~ 1 now but fragile.
        study(&mut col, c, 1.0, 5);

        let resp = col.study_dashboard(&request(&[0, 5], 1, 0.01)).unwrap();
        assert!(resp.fragile_fraction > 0.0);
        // Weakest/only topic is the suggested next study target.
        assert!(resp.next_best_topic.ends_with("07_Cardiovascular"));
    }

    #[test]
    fn performance_abstains_until_half_of_cards_are_scored() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        let a = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        let b = add_card(&mut col, &format!("{FA}14_Renal::02_Physiology"));
        add_card(&mut col, &format!("{FA}01_Biochemistry::01_Molecular"));
        // Only 1 of 3 cards scored -> below the >50% line -> abstain.
        set_perf(&mut col, a, 80.0);

        let resp = col.study_dashboard(&request(&[0], 0, 0.0)).unwrap();
        let perf = resp.performance.unwrap();
        assert!(!perf.available);
        assert_eq!(perf.scored_cards, 1);
        assert_eq!(perf.total_cards, 3);
        assert!(!perf.blocked_reasons.is_empty());

        // Score a second card -> 2 of 3 scored (>50%) -> available.
        set_perf(&mut col, b, 60.0);
        let resp = col.study_dashboard(&request(&[0], 0, 0.0)).unwrap();
        let perf = resp.performance.unwrap();
        assert!(perf.available, "{:?}", perf.blocked_reasons);
        assert_eq!(perf.scored_cards, 2);
        // Card performance score over ALL 3 cards: (0.80 + 0.60 + 0.50) / 3 =
        // 0.6333. These cards are unseen (no memory), so the blended mean is
        // 0.75*0 + 0.25*0.6333 = 0.1583.
        assert!((perf.mean - 0.1583).abs() < 1e-3, "mean was {}", perf.mean);
        assert!(perf.range_low <= perf.mean && perf.mean <= perf.range_high);
    }

    #[test]
    fn performance_unscored_cards_use_the_default() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        // No card scored and none studied: card performance score is the default
        // (0.5) and memory is 0, so the blended mean = 0.75*0 + 0.25*0.5 = 0.125.
        add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        add_card(&mut col, &format!("{FA}14_Renal::02_Physiology"));
        let resp = col.study_dashboard(&request(&[0], 0, 0.0)).unwrap();
        let perf = resp.performance.unwrap();
        assert_eq!(perf.scored_cards, 0);
        assert!((perf.mean - 0.125).abs() < 1e-6, "mean was {}", perf.mean);
        assert!(!perf.available);
    }

    #[test]
    fn ai_off_performance_is_degraded_memory_estimate() {
        let mut col = Collection::new();
        // AI rephrasing is off (default) -> Performance falls back to 0.9 x Memory.
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 50.0, 30); // reviewed now -> memory ~ 1.0
        add_graded_reviews(&mut col, c, 5);

        let resp = col.study_dashboard(&request(&[0, 5, 10], 1, 0.01)).unwrap();
        let perf = resp.performance.unwrap();
        // Shown (not blocked) even with zero scored cards, but flagged degraded.
        assert!(perf.available, "{:?}", perf.blocked_reasons);
        assert!(perf.degraded);
        assert!(!perf.degraded_note.is_empty());
        assert_eq!(perf.scored_cards, 0);

        // mean = 0.9 * memory, with no weight band (bounds mirror the mean).
        let mem0 = resp.memory.as_ref().unwrap().horizons[0].mean_recall as f64;
        assert!((perf.mean as f64 - 0.9 * mem0).abs() < 1e-3, "mean {}", perf.mean);
        for h in &perf.horizons {
            assert_eq!(h.range_low, h.mean);
            assert_eq!(h.range_high, h.mean);
        }

        // Readiness is still available (derived from the degraded Performance).
        assert!(resp.readiness_available, "{:?}", resp.readiness_blocked_reasons);
        assert_eq!(resp.readiness.len(), 3);

        // Turning AI on flips Performance back to the blended (non-degraded) score.
        enable_ai(&mut col);
        let resp = col.study_dashboard(&request(&[0, 5, 10], 1, 0.01)).unwrap();
        assert!(!resp.performance.unwrap().degraded);
    }

    #[test]
    fn ai_off_without_studied_cards_cannot_estimate_performance() {
        let mut col = Collection::new();
        // AI off and nothing studied -> no memory to fall back on -> unavailable.
        add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        let resp = col.study_dashboard(&request(&[0], 0, 0.0)).unwrap();
        let perf = resp.performance.unwrap();
        assert!(!perf.available);
        assert!(perf.degraded);
        assert!(!perf.blocked_reasons.is_empty());
    }

    #[test]
    fn perf_parsing_handles_missing_and_out_of_range() {
        assert_eq!(perf_from_custom_data(""), None);
        assert_eq!(perf_from_custom_data("{}"), None);
        assert_eq!(perf_from_custom_data(r#"{"other":3}"#), None);
        assert_eq!(perf_from_custom_data(r#"{"perf":73}"#), Some(73.0));
        // Clamped into the 1..100 band.
        assert_eq!(perf_from_custom_data(r#"{"perf":250}"#), Some(100.0));
        assert_eq!(perf_from_custom_data(r#"{"perf":0}"#), Some(1.0));
    }

    #[test]
    fn blocked_reasons_name_both_missing_thresholds() {
        let mut col = Collection::new();
        enable_ai(&mut col);
        add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology")); // new, unseen

        let resp = col.study_dashboard(&request(&[0], 0, 0.0)).unwrap();
        assert!(!resp.readiness_available);
        // Review-count, coverage, AND performance thresholds are all unmet here.
        assert_eq!(resp.readiness_blocked_reasons.len(), 3);
    }
}
