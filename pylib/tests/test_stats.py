# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

import os
import tempfile

from anki.collection import CardStats
from tests.shared import getEmptyCol


def test_stats():
    col = getEmptyCol()
    note = col.newNote()
    note["Front"] = "foo"
    col.addNote(note)
    c = note.cards()[0]
    # card stats
    card_stats = col.card_stats_data(c.id)
    assert card_stats.note_id == note.id
    c = col.sched.getCard()
    col.sched.answerCard(c, 3)
    col.sched.answerCard(c, 2)
    card_stats = col.card_stats_data(c.id)
    assert len(card_stats.revlog) == 2


def test_mastery_by_topic():
    """End-to-end check of the Rust 'mastery query' from Python."""
    col = getEmptyCol()
    prefix = "#AK_Step1_v11::#FirstAid::"

    def add(tag: str) -> None:
        note = col.newNote()
        note["Front"] = "q"
        note["Back"] = "a"
        note.tags = [tag]
        col.addNote(note)

    add(f"{prefix}07_Cardiovascular::03_Physiology")
    add(f"{prefix}07_Cardiovascular::01_Anatomy")
    add(f"{prefix}14_Renal::02_Physiology")

    rows = col.mastery_by_topic(tag_prefix=prefix, topic_depth=1)
    by_topic = {r.topic: r for r in rows}

    assert set(by_topic) == {
        f"{prefix}07_Cardiovascular",
        f"{prefix}14_Renal",
    }
    cardio = by_topic[f"{prefix}07_Cardiovascular"]
    assert cardio.total_cards == 2
    # All cards are brand-new -> none mastered, recall 0.
    assert cardio.cards_mastered == 0
    assert cardio.average_recall == 0.0
    assert by_topic[f"{prefix}14_Renal"].total_cards == 1


def test_study_dashboard():
    """End-to-end check of the Rust study dashboard from Python."""
    col = getEmptyCol()
    prefix = "#AK_Step1_v11::#FirstAid::"

    def add(tag: str) -> None:
        note = col.newNote()
        note["Front"] = "q"
        note["Back"] = "a"
        note.tags = [tag]
        col.addNote(note)

    add(f"{prefix}07_Cardiovascular::03_Physiology")
    add(f"{prefix}14_Renal::02_Physiology")

    resp = col.study_dashboard(tag_prefix=prefix, readiness_horizons_days=[0, 5])

    # Coverage + memory always present; readiness abstains on a tiny collection.
    assert resp.coverage.total_cards == 2
    assert resp.readiness_available is False
    assert len(resp.readiness) == 0
    assert len(resp.readiness_blocked_reasons) >= 1
    assert len(resp.topics) == 2


def test_admin_set_fsrs_and_advance_days():
    """End-to-end check of the admin/simulation RPCs from Python."""
    col = getEmptyCol()
    prefix = "#AK_Step1_v11::#FirstAid::"

    def add() -> None:
        note = col.newNote()
        note["Front"] = "q"
        note["Back"] = "a"
        note.tags = [f"{prefix}07_Cardiovascular::03_Physiology"]
        col.addNote(note)

    add()
    add()

    updated = col.admin_set_fsrs(
        stability=40.0, difficulty=6.0, target_retrievability=0.85
    )
    assert updated == 2

    # Memory is now reported for both cards, near the requested retrievability.
    dash = col.study_dashboard(tag_prefix=prefix, readiness_horizons_days=[0, 5])
    assert dash.memory.studied_cards == 2
    assert abs(dash.memory.mean_recall - 0.85) < 0.06
    before = dash.memory.mean_recall

    # Advancing time with no study must lower current recall.
    assert col.admin_advance_days(days=30) == 2
    after = col.study_dashboard(tag_prefix=prefix).memory.mean_recall
    assert after < before

    # Resetting half of the cards to "not learned yet" drops studied count.
    for _ in range(8):
        add()
    # 10 cards total; set them all, then reset a random 50% to new.
    assert col.admin_set_fsrs(stability=40.0, difficulty=6.0, target_retrievability=0.85) == 10
    assert col.study_dashboard(tag_prefix=prefix).memory.studied_cards == 10
    assert col.admin_reset_cards(sample_percent=50) == 5
    assert col.study_dashboard(tag_prefix=prefix).memory.studied_cards == 5

    # A random-percentage FSRS apply touches only that fraction.
    assert col.admin_reset_cards() == 10  # all back to new
    assert (
        col.admin_set_fsrs(
            stability=40.0, difficulty=6.0, target_retrievability=0.85, sample_percent=30
        )
        == 3
    )


def test_graphs_empty():
    col = getEmptyCol()
    assert col.stats().report()


def test_graphs():
    dir = tempfile.gettempdir()
    col = getEmptyCol()
    g = col.stats()
    rep = g.report()
    with open(os.path.join(dir, "test.html"), "w", encoding="UTF-8") as note:
        note.write(rep)
    return
