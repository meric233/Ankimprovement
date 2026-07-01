// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Admin / simulation mode (USMLE Step 1 project, developer tooling).
//!
//! These operations mutate the collection directly so a demo/test deck can be
//! driven into an arbitrary FSRS state and "fast-forwarded" through time. They
//! are **not** part of the honest scoring path and are gated behind a UI toggle.
//! Both operations run inside an undoable transaction.

use anki_proto::stats::AdminAdvanceDaysRequest;
use anki_proto::stats::AdminOpResponse;
use anki_proto::stats::AdminResetCardsRequest;
use anki_proto::stats::AdminSetFsrsRequest;
use fsrs::FSRS5_DEFAULT_DECAY;
use rand::seq::SliceRandom;

use crate::card::CardQueue;
use crate::card::CardType;
use crate::card::FsrsMemoryState;
use crate::ops::Op;
use crate::ops::OpOutput;
use crate::prelude::*;
use crate::revlog::RevlogEntry;
use crate::revlog::RevlogId;
use crate::revlog::RevlogReviewKind;
use crate::search::SortMode;

const SECS_PER_DAY: i64 = 86_400;
const MIN_STABILITY: f32 = 0.01;

/// Randomly keep `percent` (1..100) of the cids. 0 or >=100 keeps them all.
fn sample_cids(mut cids: Vec<CardId>, percent: u32) -> Vec<CardId> {
    if percent == 0 || percent >= 100 {
        return cids;
    }
    cids.shuffle(&mut rand::rng());
    let keep = ((cids.len() as f64) * (percent as f64 / 100.0)).round() as usize;
    cids.truncate(keep);
    cids
}

/// Days of elapsed time at which the FSRS forgetting curve reaches retrievability
/// `target_r` for the given `stability`, under the given (negative) `decay`.
///
/// FSRS-5 curve: `R(t) = (1 + FACTOR * t/S)^decay`, with `R(S) = 0.9`, so
/// `FACTOR = 0.9^(1/decay) - 1`. Inverting for `t` gives the formula below. We
/// invert in closed form (rather than `FSRS::next_interval`) so it works without
/// trained parameters and stays correct if the decay constant changes.
fn days_for_target_recall(stability: f32, target_r: f32, decay: f32) -> f64 {
    let decay = decay as f64;
    let factor = 0.9_f64.powf(1.0 / decay) - 1.0;
    let s = stability as f64;
    let r = target_r as f64;
    ((s / factor) * (r.powf(1.0 / decay) - 1.0)).max(0.0)
}

impl Collection {
    /// Bulk-set FSRS memory state (stability, difficulty) on matched cards, and
    /// back-date their last-review time so current retrievability equals the
    /// requested target. Undoable.
    pub fn admin_set_fsrs(
        &mut self,
        req: &AdminSetFsrsRequest,
    ) -> Result<OpOutput<AdminOpResponse>> {
        let cids = sample_cids(
            self.search_cards(&req.search, SortMode::NoOrder)?,
            req.sample_percent,
        );
        let usn = self.usn()?;

        let stability = req.stability.max(MIN_STABILITY);
        let difficulty = req.difficulty.clamp(1.0, 10.0);
        let target_r = req.target_retrievability.clamp(0.01, 0.99);

        // Back-date last review so current recall equals the requested target.
        let elapsed_days =
            days_for_target_recall(stability, target_r, FSRS5_DEFAULT_DECAY).round() as i64;
        let now = TimestampSecs::now();
        let last_review = TimestampSecs(now.0 - elapsed_days.max(0) * SECS_PER_DAY);

        let interval = elapsed_days.max(1) as u32;
        // Review-card `due` is a day number (days since collection creation), not
        // the new-card position the card currently carries. The card was last
        // reviewed `elapsed_days` ago with scheduling interval `interval`, so its
        // next due day is `today - elapsed_days + interval` (≈ today). Without
        // this, converted cards keep their huge new-card position as `due` and
        // never come due, no matter how many days we advance.
        let today = self.timing_today()?.days_elapsed as i32;
        let due = today - elapsed_days.max(0) as i32 + interval as i32;
        // Base for synthetic revlog ids (ms). Offset per card keeps them unique.
        let revlog_base = TimestampMillis::now().0;

        self.transact(Op::Custom("Admin: set FSRS state".into()), |col| {
            let mut updated = 0u32;
            for &cid in &cids {
                let mut card = col.storage.get_card(cid)?.or_not_found(cid)?;
                let original = card.clone();
                card.memory_state = Some(FsrsMemoryState {
                    stability,
                    difficulty,
                });
                card.decay = Some(FSRS5_DEFAULT_DECAY);
                // Make it a normal review card so the dashboard treats it as seen.
                card.ctype = CardType::Review;
                card.queue = CardQueue::Review;
                if card.reps == 0 {
                    card.reps = 1;
                }
                card.interval = interval;
                card.due = due;
                card.last_review_time = Some(last_review);
                col.update_card_inner(&mut card, original, usn)?;

                // Log one synthetic graded review so the dashboard's give-up rule
                // (which counts graded reviews) can be exercised from admin mode.
                let entry = RevlogEntry {
                    id: RevlogId(revlog_base + updated as i64),
                    cid,
                    usn,
                    button_chosen: 3,
                    interval: interval as i32,
                    last_interval: interval as i32,
                    ease_factor: 2500,
                    taken_millis: 0,
                    review_kind: RevlogReviewKind::Review,
                };
                col.add_revlog_entry_undoable(entry)?;
                updated += 1;
            }
            Ok(AdminOpResponse { updated })
        })
    }

    /// Simulate `days` of no study by shifting matched cards' last-review time
    /// back that many days (so FSRS recall decays) and pulling review-card due
    /// dates earlier by the same amount. Undoable.
    pub fn admin_advance_days(
        &mut self,
        req: &AdminAdvanceDaysRequest,
    ) -> Result<OpOutput<AdminOpResponse>> {
        let cids = self.search_cards(&req.search, SortMode::NoOrder)?;
        let usn = self.usn()?;
        let shift_secs = req.days as i64 * SECS_PER_DAY;

        self.transact(Op::Custom("Admin: advance days".into()), |col| {
            let mut updated = 0u32;
            for &cid in &cids {
                let mut card = col.storage.get_card(cid)?.or_not_found(cid)?;
                let Some(last_review) = card.last_review_time else {
                    continue; // never reviewed: nothing to decay
                };
                let original = card.clone();
                card.last_review_time = Some(TimestampSecs(last_review.0 - shift_secs));
                // Review-card `due` is a day number; pulling it earlier makes the
                // card come due, mirroring real elapsed time.
                if card.ctype == CardType::Review {
                    card.due -= req.days as i32;
                }
                col.update_card_inner(&mut card, original, usn)?;
                updated += 1;
            }
            Ok(AdminOpResponse { updated })
        })
    }

    /// Reset matched cards (or a random `sample_percent` of them) to
    /// "not learned yet" (new): clears FSRS state, returns them to the new
    /// queue, and resets the review/lapse counts. Undoable.
    pub fn admin_reset_cards(
        &mut self,
        req: &AdminResetCardsRequest,
    ) -> Result<OpOutput<AdminOpResponse>> {
        let cids = sample_cids(
            self.search_cards(&req.search, SortMode::NoOrder)?,
            req.sample_percent,
        );
        let updated = cids.len() as u32;
        let out = self.reschedule_cards_as_new(&cids, false, true, true, None)?;
        Ok(OpOutput {
            output: AdminOpResponse { updated },
            changes: out.changes,
        })
    }
}

#[cfg(test)]
mod test {
    use fsrs::FSRS;

    use super::*;
    use crate::card::CardType;
    use crate::tests::NoteAdder;

    fn add_card(col: &mut Collection) -> CardId {
        let mut note = NoteAdder::basic(col).note();
        col.add_note(&mut note, DeckId(1)).unwrap();
        col.storage
            .all_cards_of_note(note.id)
            .unwrap()
            .pop()
            .unwrap()
            .id
    }

    fn add_tagged_card(col: &mut Collection, tag: &str) {
        let mut note = NoteAdder::basic(col).note();
        note.tags = vec![tag.to_string()];
        col.add_note(&mut note, DeckId(1)).unwrap();
    }

    fn set_fsrs(col: &mut Collection, stability: f32, difficulty: f32, r: f32) -> u32 {
        col.admin_set_fsrs(&AdminSetFsrsRequest {
            search: String::new(),
            stability,
            difficulty,
            target_retrievability: r,
            sample_percent: 0,
        })
        .unwrap()
        .output
        .updated
    }

    #[test]
    fn set_fsrs_applies_state_and_hits_target_recall() {
        let mut col = Collection::new();
        let cid = add_card(&mut col);
        let updated = set_fsrs(&mut col, 30.0, 6.0, 0.8);
        assert_eq!(updated, 1);

        let card = col.storage.get_card(cid).unwrap().unwrap();
        assert_eq!(card.ctype, CardType::Review);
        let state = card.memory_state.unwrap();
        assert!((state.stability - 30.0).abs() < 1e-3);
        assert!((state.difficulty - 6.0).abs() < 1e-3);

        // Current retrievability should be ~ the requested target.
        let fsrs = FSRS::new(None).unwrap();
        let timing = col.timing_today().unwrap();
        let elapsed = card.seconds_since_last_review(&timing).unwrap_or_default();
        let r = fsrs.current_retrievability_seconds(state.into(), elapsed, FSRS5_DEFAULT_DECAY);
        assert!((r - 0.8).abs() < 0.05, "expected ~0.8, got {r}");
    }

    #[test]
    fn set_fsrs_makes_card_due_now() {
        let mut col = Collection::new();
        let cid = add_card(&mut col);
        // Target recall = desired retention (0.9): next due day should be today.
        set_fsrs(&mut col, 30.0, 5.0, 0.9);

        let today = col.timing_today().unwrap().days_elapsed as i32;
        let card = col.storage.get_card(cid).unwrap().unwrap();
        assert_eq!(card.queue, CardQueue::Review);
        assert!(
            card.due <= today,
            "review card should be due now: due={} today={today}",
            card.due
        );
    }

    #[test]
    fn advance_days_decays_recall() {
        let mut col = Collection::new();
        let cid = add_card(&mut col);
        // Low stability so the decay over a few days is clearly measurable.
        set_fsrs(&mut col, 2.0, 5.0, 0.95);

        let fsrs = FSRS::new(None).unwrap();
        let recall_now = |col: &mut Collection| {
            let card = col.storage.get_card(cid).unwrap().unwrap();
            let timing = col.timing_today().unwrap();
            let elapsed = card.seconds_since_last_review(&timing).unwrap_or_default();
            fsrs.current_retrievability_seconds(
                card.memory_state.unwrap().into(),
                elapsed,
                FSRS5_DEFAULT_DECAY,
            )
        };
        let before = recall_now(&mut col);
        let updated = col
            .admin_advance_days(&AdminAdvanceDaysRequest {
                search: String::new(),
                days: 10,
            })
            .unwrap()
            .output
            .updated;
        assert_eq!(updated, 1);
        let after = recall_now(&mut col);
        assert!(after < before, "recall {after} should be below {before}");
    }

    #[test]
    fn set_fsrs_logs_graded_reviews_to_unlock_readiness() {
        use anki_proto::stats::StudyDashboardRequest;

        let mut col = Collection::new();
        let tag = "#AK_Step1_v11::#FirstAid::07_Cardiovascular::03_Physiology";
        // Enough cards that, once set, both give-up thresholds are crossed.
        for _ in 0..205 {
            add_tagged_card(&mut col, tag);
        }

        // Before: readiness abstains (no reviews, nothing seen).
        let req = || StudyDashboardRequest {
            search: String::new(),
            tag_prefix: "#AK_Step1_v11::#FirstAid::".to_string(),
            topic_depth: 1,
            readiness_horizons_days: vec![0, 5],
            min_graded_reviews: 0,
            min_coverage: 0.0,
        };
        assert!(!col.study_dashboard(&req()).unwrap().readiness_available);

        let updated = set_fsrs(&mut col, 30.0, 5.0, 0.9);
        assert_eq!(updated, 205);
        // One graded review logged per card.
        assert_eq!(col.storage.graded_review_count().unwrap(), 205);

        // After: 205 reviews (>200) and full coverage (>50%) -> readiness shows
        // both horizons, including the +5-day projection.
        let resp = col.study_dashboard(&req()).unwrap();
        assert!(
            resp.readiness_available,
            "{:?}",
            resp.readiness_blocked_reasons
        );
        assert_eq!(resp.readiness.len(), 2);
    }

    #[test]
    fn sample_percent_applies_to_a_random_subset() {
        let mut col = Collection::new();
        for _ in 0..100 {
            add_card(&mut col);
        }
        let updated = col
            .admin_set_fsrs(&AdminSetFsrsRequest {
                search: String::new(),
                stability: 30.0,
                difficulty: 5.0,
                target_retrievability: 0.9,
                sample_percent: 25,
            })
            .unwrap()
            .output
            .updated;
        assert_eq!(updated, 25);

        // Exactly 25 cards now have FSRS state; the rest are untouched.
        let mut with_state = 0;
        for cid in col.search_cards("", SortMode::NoOrder).unwrap() {
            if col.storage.get_card(cid).unwrap().unwrap().memory_state.is_some() {
                with_state += 1;
            }
        }
        assert_eq!(with_state, 25);
    }

    #[test]
    fn reset_cards_returns_them_to_new() {
        let mut col = Collection::new();
        for _ in 0..10 {
            add_card(&mut col);
        }
        set_fsrs(&mut col, 30.0, 5.0, 0.9);
        // All are review cards with FSRS state now.

        let reset = col
            .admin_reset_cards(&AdminResetCardsRequest {
                search: String::new(),
                sample_percent: 50,
            })
            .unwrap()
            .output
            .updated;
        assert_eq!(reset, 5);

        // Exactly 5 cards are back to New with no memory state.
        let (mut new_cards, mut still_review) = (0, 0);
        for cid in col.search_cards("", SortMode::NoOrder).unwrap() {
            let card = col.storage.get_card(cid).unwrap().unwrap();
            match card.ctype {
                CardType::New => {
                    new_cards += 1;
                    assert!(card.memory_state.is_none());
                }
                _ => still_review += 1,
            }
        }
        assert_eq!(new_cards, 5);
        assert_eq!(still_review, 5);
    }

    #[test]
    fn admin_ops_are_undoable() {
        let mut col = Collection::new();
        let cid = add_card(&mut col);
        assert!(col.storage.get_card(cid).unwrap().unwrap().memory_state.is_none());

        set_fsrs(&mut col, 30.0, 5.0, 0.9);
        assert!(col.storage.get_card(cid).unwrap().unwrap().memory_state.is_some());

        col.undo().unwrap();
        assert!(
            col.storage.get_card(cid).unwrap().unwrap().memory_state.is_none(),
            "undo should restore the pre-admin memory state"
        );
    }
}
