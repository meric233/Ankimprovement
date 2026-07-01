// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Mastery query (USMLE Step 1 project).
//!
//! Aggregates per-card FSRS memory state into per-topic statistics, where a
//! "topic" is a tag at a configurable prefix + depth. This is the backend piece
//! that turns Anki's per-card scheduler data into the per-topic numbers the
//! dashboard needs (cards mastered + average recall), fast enough for 50k cards.

use std::collections::HashMap;

use anki_proto::stats::MasteryByTopicRequest;
use anki_proto::stats::MasteryByTopicResponse;
use anki_proto::stats::TopicMastery;
use fsrs::FSRS;
use fsrs::FSRS5_DEFAULT_DECAY;

use crate::card::CardType;
use crate::prelude::*;
use crate::search::SortMode;
use crate::tags::split_tags;

/// A card is "mastered" once it is a review card whose interval has reached
/// Anki's "mature" threshold (>= 21 days). This matches Anki's own definition
/// of a well-learned card and is stable from day to day.
pub(super) const MATURE_INTERVAL_DAYS: u32 = 21;

#[derive(Default)]
struct TopicAccumulator {
    total_cards: u32,
    cards_mastered: u32,
    recall_sum: f64,
}

impl Collection {
    /// Per-topic mastery aggregation. See [`MasteryByTopicRequest`].
    pub fn mastery_by_topic(
        &mut self,
        req: &MasteryByTopicRequest,
    ) -> Result<MasteryByTopicResponse> {
        let depth = req.topic_depth.max(1) as usize;
        let prefix = req.tag_prefix.as_str();

        let guard = self.search_cards_into_table(&req.search, SortMode::NoOrder)?;
        let timing = guard.col.timing_today()?;
        let cards = guard.col.storage.all_searched_cards()?;

        // Bulk-load tags once (note tags are shared by sibling cards).
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
        let mut topics: HashMap<String, TopicAccumulator> = HashMap::new();

        for card in &cards {
            // Compute this card's recall (FSRS R now) and mastery once.
            let recall = match card.memory_state {
                Some(state) => {
                    let elapsed = card.seconds_since_last_review(&timing).unwrap_or_default();
                    fsrs.current_retrievability_seconds(
                        state.into(),
                        elapsed,
                        card.decay.unwrap_or(FSRS5_DEFAULT_DECAY),
                    ) as f64
                }
                // New / unseen cards count toward the topic but contribute 0 recall.
                None => 0.0,
            };
            let mastered =
                card.ctype == CardType::Review && card.interval >= MATURE_INTERVAL_DAYS;

            // A card can belong to several topics (multiple matching tags); count
            // it once per distinct topic.
            let empty = Vec::new();
            let card_tags = tags_by_note.get(&card.note_id).unwrap_or(&empty);
            let mut card_topics: Vec<String> = card_tags
                .iter()
                .filter_map(|tag| topic_for_tag(tag, prefix, depth))
                .collect();
            card_topics.sort_unstable();
            card_topics.dedup();

            for topic in card_topics {
                let acc = topics.entry(topic).or_default();
                acc.total_cards += 1;
                if mastered {
                    acc.cards_mastered += 1;
                }
                acc.recall_sum += recall;
            }
        }

        let mut rows: Vec<TopicMastery> = topics
            .into_iter()
            .map(|(topic, acc)| TopicMastery {
                topic,
                total_cards: acc.total_cards,
                cards_mastered: acc.cards_mastered,
                average_recall: if acc.total_cards > 0 {
                    (acc.recall_sum / acc.total_cards as f64) as f32
                } else {
                    0.0
                },
            })
            .collect();
        // Deterministic, dashboard-friendly ordering.
        rows.sort_by(|a, b| a.topic.cmp(&b.topic));

        Ok(MasteryByTopicResponse { topics: rows })
    }
}

/// Derive the topic key for a tag, or `None` if the tag is not under `prefix`.
///
/// The topic is `prefix` plus the first `depth` "::"-separated components that
/// follow it. When `prefix` is empty, the topic is the first `depth` components
/// of the tag itself.
pub(super) fn topic_for_tag(tag: &str, prefix: &str, depth: usize) -> Option<String> {
    let remainder = if prefix.is_empty() {
        tag
    } else {
        tag.strip_prefix(prefix)?
    };
    let remainder = remainder.trim_start_matches("::");
    if remainder.is_empty() {
        return None;
    }
    let components: Vec<&str> = remainder.split("::").take(depth).collect();
    if components.is_empty() {
        return None;
    }
    let suffix = components.join("::");
    if prefix.is_empty() {
        Some(suffix)
    } else {
        Some(format!("{}{}", prefix, suffix))
    }
}

#[cfg(test)]
mod test {
    use super::*;
    use crate::card::FsrsMemoryState;
    use crate::tests::NoteAdder;

    const FA: &str = "#AK_Step1_v11::#FirstAid::";

    #[test]
    fn topic_for_tag_respects_prefix_and_depth() {
        let tag = "#AK_Step1_v11::#FirstAid::07_Cardiovascular::03_Physiology";
        // depth 1 -> section level
        assert_eq!(
            topic_for_tag(tag, FA, 1).as_deref(),
            Some("#AK_Step1_v11::#FirstAid::07_Cardiovascular")
        );
        // depth 2 -> section + subsection
        assert_eq!(
            topic_for_tag(tag, FA, 2).as_deref(),
            Some("#AK_Step1_v11::#FirstAid::07_Cardiovascular::03_Physiology")
        );
    }

    #[test]
    fn topic_for_tag_skips_non_matching_and_handles_empty_prefix() {
        // Tag not under the prefix -> ignored.
        assert_eq!(topic_for_tag("#AK_Step1_v11::#Bootcamp::Cardio", FA, 1), None);
        // Tag equal to the prefix with nothing after -> ignored.
        assert_eq!(topic_for_tag("#AK_Step1_v11::#FirstAid", FA, 1), None);
        // Empty prefix -> first `depth` components of the tag itself.
        assert_eq!(topic_for_tag("A::B::C", "", 1).as_deref(), Some("A"));
        assert_eq!(topic_for_tag("A::B::C", "", 2).as_deref(), Some("A::B"));
    }

    fn add_note_with_tags(col: &mut Collection, tags: &[&str]) -> NoteId {
        let mut note = NoteAdder::basic(col).note();
        note.tags = tags.iter().map(ToString::to_string).collect();
        col.add_note(&mut note, DeckId(1)).unwrap();
        note.id
    }

    fn make_mastered(col: &mut Collection, nid: NoteId) {
        let mut card = col.storage.all_cards_of_note(nid).unwrap().pop().unwrap();
        card.ctype = CardType::Review;
        card.interval = 30; // >= 21 -> mature
        card.memory_state = Some(FsrsMemoryState {
            stability: 100.0,
            difficulty: 5.0,
        });
        card.decay = Some(FSRS5_DEFAULT_DECAY);
        col.storage.update_card(&card).unwrap();
    }

    fn request(prefix: &str, depth: u32) -> MasteryByTopicRequest {
        MasteryByTopicRequest {
            search: String::new(),
            tag_prefix: prefix.to_string(),
            topic_depth: depth,
        }
    }

    fn row<'a>(resp: &'a MasteryByTopicResponse, ends_with: &str) -> &'a TopicMastery {
        resp.topics
            .iter()
            .find(|t| t.topic.ends_with(ends_with))
            .unwrap_or_else(|| panic!("no topic ending in {ends_with}: {:?}", resp.topics))
    }

    #[test]
    fn groups_by_section_and_counts_mastered_and_new() {
        let mut col = Collection::new();
        // Two cardiovascular cards (one mastered, one brand-new) + one renal (new).
        let cardio_mastered =
            add_note_with_tags(&mut col, &[&format!("{FA}07_Cardiovascular::03_Physiology")]);
        add_note_with_tags(&mut col, &[&format!("{FA}07_Cardiovascular::01_Anatomy")]);
        add_note_with_tags(&mut col, &[&format!("{FA}14_Renal::02_Physiology")]);
        make_mastered(&mut col, cardio_mastered);

        let resp = col.mastery_by_topic(&request(FA, 1)).unwrap();

        assert_eq!(resp.topics.len(), 2);
        let cardio = row(&resp, "07_Cardiovascular");
        assert_eq!(cardio.total_cards, 2);
        assert_eq!(cardio.cards_mastered, 1);
        // One card has FSRS recall > 0, the other (new) is 0 -> mean is positive.
        assert!(cardio.average_recall > 0.0 && cardio.average_recall <= 1.0);

        let renal = row(&resp, "14_Renal");
        assert_eq!(renal.total_cards, 1);
        assert_eq!(renal.cards_mastered, 0);
        // Only an unseen card -> recall exactly 0.
        assert_eq!(renal.average_recall, 0.0);
    }

    #[test]
    fn card_with_two_section_tags_counts_in_both_topics() {
        let mut col = Collection::new();
        add_note_with_tags(
            &mut col,
            &[
                &format!("{FA}07_Cardiovascular::03_Physiology"),
                &format!("{FA}16_Respiratory::01_Anatomy"),
            ],
        );

        let resp = col.mastery_by_topic(&request(FA, 1)).unwrap();

        assert_eq!(resp.topics.len(), 2);
        assert_eq!(row(&resp, "07_Cardiovascular").total_cards, 1);
        assert_eq!(row(&resp, "16_Respiratory").total_cards, 1);
    }

    #[test]
    fn query_is_read_only_and_undo_safe() {
        let mut col = Collection::new();
        add_note_with_tags(&mut col, &[&format!("{FA}07_Cardiovascular::03_Physiology")]);

        // An undoable op (AddNote) is now the current undo point.
        let before = format!("{:?}", col.can_undo());
        assert!(col.can_undo().is_some());

        // Running the query must not add/clear undo state or mutate anything.
        let _ = col.mastery_by_topic(&request(FA, 1)).unwrap();
        assert_eq!(format!("{:?}", col.can_undo()), before);

        // Undo of the prior op still works after the query ran.
        col.undo().unwrap();
        assert!(col.can_undo().is_none());
    }

    #[test]
    fn depth_two_splits_into_subsections() {
        let mut col = Collection::new();
        add_note_with_tags(&mut col, &[&format!("{FA}07_Cardiovascular::03_Physiology")]);
        add_note_with_tags(&mut col, &[&format!("{FA}07_Cardiovascular::01_Anatomy")]);

        let resp = col.mastery_by_topic(&request(FA, 2)).unwrap();

        // Same section but two subsections -> two distinct topics at depth 2.
        assert_eq!(resp.topics.len(), 2);
        assert_eq!(row(&resp, "03_Physiology").total_cards, 1);
        assert_eq!(row(&resp, "01_Anatomy").total_cards, 1);
    }
}
