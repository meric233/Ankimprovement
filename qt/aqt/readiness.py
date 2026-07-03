# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""USMLE project: the Readiness dashboard (Memory + Coverage + Readiness).

A thin Qt shell that hosts the SvelteKit ``readiness`` page, which fetches its
data from the Rust ``study_dashboard`` backend call.
"""

from __future__ import annotations

import aqt
import aqt.main
from anki.decks import DeckId, FilteredDeckConfig
from anki.scheduler import FilteredDeckForUpdate
from aqt.operations.scheduling import add_or_update_filtered_deck
from aqt.qt import (
    QCloseEvent,
    QDialog,
    Qt,
    QVBoxLayout,
)
from aqt.utils import disable_help_button, restoreGeom, saveGeom, tooltip
from aqt.webview import AnkiWebView, AnkiWebViewKind


class ReadinessDialog(QDialog):
    "Study readiness dashboard."

    TITLE = "readiness"
    silentlyClose = True

    def __init__(self, mw: aqt.main.AnkiQt) -> None:
        QDialog.__init__(self, mw, Qt.WindowType.Window)
        self.mw = mw
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.mw.garbage_collect_on_dialog_finish(self)
        self.setMinimumWidth(500)
        self.setMinimumHeight(500)
        disable_help_button(self)
        restoreGeom(self, self.TITLE, default_size=(900, 800))

        self.web = AnkiWebView(kind=AnkiWebViewKind.READINESS)
        self.web.load_sveltekit_page("readiness")
        self.web.set_bridge_command(self._on_bridge_cmd, self)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        self.setLayout(layout)
        self.setWindowTitle("Readiness")
        self.show()

    # -- webview -> Python bridge (Review / Learn buttons) ------------------

    def _on_bridge_cmd(self, cmd: str) -> bool:
        if cmd == "review":
            self._start_review()
        elif cmd.startswith("studyTopic:"):
            self._start_topic_study(cmd.split(":", 1)[1])
        return False

    def _start_review(self) -> None:
        """Start a normal review session on the currently selected deck."""
        mw = self.mw
        self.close()
        if mw.state != "overview":
            mw.moveToState("overview")
        mw.col.startTimebox()
        mw.moveToState("review")

    def _start_topic_study(self, topic: str) -> None:
        """Build a filtered deck for one outline topic and start studying it.

        Used by the "Learn least-covered topic" button: gathers that topic's
        cards (including new/unseen ones, so it actually teaches new material)
        into a rescheduling filtered deck, then opens the reviewer on it.
        """
        mw = self.mw
        # Our topics come from our own tag data; refuse anything that could break
        # out of the tag search string.
        if not topic or '"' in topic or "\n" in topic:
            tooltip("Could not start topic study: invalid topic.", parent=mw)
            return

        deck = mw.col.sched.get_or_create_filtered_deck(DeckId(0))
        short = topic.split("::")[-1] or topic
        deck.name = f"Learn: {short}"
        config = deck.config
        config.reschedule = True  # real learning, updates FSRS state
        del config.search_terms[:]
        config.search_terms.append(
            FilteredDeckConfig.SearchTerm(
                # the topic tag itself plus any of its subtopics
                search=f'("tag:{topic}" OR "tag:{topic}::*")',
                limit=100,
                order=FilteredDeckConfig.SearchTerm.ADDED,
            )
        )

        def on_success(_out: object) -> None:
            # add_or_update_filtered_deck sets the filtered deck as current.
            self.close()
            mw.moveToState("review")

        def on_failure(exc: Exception) -> None:
            tooltip(f"No cards to learn for {short}: {exc}", parent=mw)

        add_or_update_filtered_deck(parent=mw, deck=deck).success(
            on_success
        ).failure(on_failure).run_in_background()

    def closeEvent(self, evt: QCloseEvent | None) -> None:
        saveGeom(self, self.TITLE)
        if self.web:
            self.web.cleanup()
            self.web = None  # type: ignore
        aqt.dialogs.markClosed("Readiness")
        if evt:
            evt.accept()

    def reject(self) -> None:
        self.close()
