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
/// A studied card counts as "fragile" (over-confidence risk) when it is highly
/// recallable right now but not yet durably learned.
const FRAGILE_RECALL: f32 = 0.9;

#[derive(Default)]
struct TopicAcc {
    total: u32,
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
        let mut horizon_sums: Vec<f64> = vec![0.0; horizons.len()];
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
            for (i, pr) in projected.iter().enumerate() {
                horizon_sums[i] += *pr as f64;
            }

            let mastered =
                card.ctype == CardType::Review && card.interval >= MATURE_INTERVAL_DAYS;
            let card_current = current_recall.map(|(cur, _)| cur).unwrap_or(0.0) as f64;
            for topic in card_topics {
                let acc = topics_map.entry(topic).or_default();
                acc.total += 1;
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

        // ---- Give-up rule -------------------------------------------------
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
        let readiness_available = blocked.is_empty();

        // ---- Readiness (only when the give-up rule is satisfied) ----------
        let readiness = if readiness_available {
            horizons
                .iter()
                .enumerate()
                .map(|(i, &h)| {
                    let mean_proj = if total_outline_cards > 0 {
                        (horizon_sums[i] / total_outline_cards as f64) as f32
                    } else {
                        0.0
                    };
                    build_readiness(h, mean_proj, coverage_fraction)
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

/// The official Step 1 pass mark, as a fraction of questions correct.
const PASS_MARK: f32 = 0.60;

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

fn build_readiness(horizon_days: u32, mean_projected_recall: f32, coverage: f32) -> DashboardReadiness {
    // `mean_projected_recall` is our estimate of exam *correctness* (fraction of
    // questions answered right). Run it through the calibrated correctness->pass
    // curve so the headline number is an honest probability of *passing*, not a
    // score.
    let correctness = mean_projected_recall.clamp(0.0, 1.0);
    let p_pass = correctness_to_pass_probability(correctness);
    // Uncertainty band: thin coverage widens it, and outcomes are most volatile
    // right at the 60% cut score (a point estimate there barely constrains
    // pass/fail), so widen it there too.
    let coverage_margin = 0.20 * (1.0 - coverage);
    let threshold_margin = 0.25 * (1.0 - ((correctness - PASS_MARK).abs() / 0.10).min(1.0));
    let margin = (0.08 + coverage_margin + threshold_margin).min(0.45);
    DashboardReadiness {
        horizon_days,
        p_pass,
        range_low: (p_pass - margin).max(0.0),
        range_high: (p_pass + margin).min(1.0),
        mean_projected_recall,
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
    fn readiness_decays_with_horizon_when_available() {
        let mut col = Collection::new();
        // Low stability -> recall decays quickly over 5 days (fragile knowledge).
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 1.0, 30);
        add_graded_reviews(&mut col, c, 5);

        // Loosen the give-up thresholds so readiness is populated.
        let resp = col.study_dashboard(&request(&[0, 5], 1, 0.01)).unwrap();

        assert!(resp.readiness_available, "{:?}", resp.readiness_blocked_reasons);
        assert_eq!(resp.readiness.len(), 2);
        let today = &resp.readiness[0];
        let plus5 = &resp.readiness[1];
        assert_eq!(today.horizon_days, 0);
        assert_eq!(plus5.horizon_days, 5);
        // The over-confidence signal: pass chance is lower 5 days out with no study.
        assert!(
            today.p_pass > plus5.p_pass,
            "today {} should exceed +5d {}",
            today.p_pass,
            plus5.p_pass
        );
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
    fn default_horizons_include_today_5d_and_10d() {
        let mut col = Collection::new();
        let c = add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology"));
        study(&mut col, c, 50.0, 30);
        add_graded_reviews(&mut col, c, 5);

        // Empty horizons -> defaults [0, 5, 10].
        let mut req = request(&[], 1, 0.01);
        req.readiness_horizons_days = vec![];
        let resp = col.study_dashboard(&req).unwrap();
        assert!(resp.readiness_available, "{:?}", resp.readiness_blocked_reasons);
        let days: Vec<u32> = resp.readiness.iter().map(|r| r.horizon_days).collect();
        assert_eq!(days, vec![0, 5, 10]);
        // Memory reports the same horizons.
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
    fn blocked_reasons_name_both_missing_thresholds() {
        let mut col = Collection::new();
        add_card(&mut col, &format!("{FA}07_Cardiovascular::03_Physiology")); // new, unseen

        let resp = col.study_dashboard(&request(&[0], 0, 0.0)).unwrap();
        assert!(!resp.readiness_available);
        // Both the review-count and coverage thresholds are unmet here.
        assert_eq!(resp.readiness_blocked_reasons.len(), 2);
    }
}
