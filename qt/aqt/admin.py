# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""USMLE project: Admin / simulation mode (developer tooling).

A small dialog, gated behind the Tools > "Admin: simulation mode" toggle, that
drives a demo/test collection into arbitrary FSRS states and fast-forwards time
so the Readiness dashboard (give-up rule, recall decay, +5-day projection) can be
exercised without weeks of real reviews.

These operations mutate the collection directly. They are undoable (Ctrl+Z) and
are NOT part of the honest scoring path.
"""

from __future__ import annotations

import aqt
import aqt.main
from aqt.operations import QueryOp
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    Qt,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import disable_help_button, restoreGeom, saveGeom, tooltip


class AdminSimulationDialog(QDialog):
    "Bulk-set FSRS state and simulate elapsed days (dev/admin tooling)."

    TITLE = "adminSimulation"

    def __init__(self, mw: aqt.main.AnkiQt) -> None:
        QDialog.__init__(self, mw, Qt.WindowType.Window)
        self.mw = mw
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.mw.garbage_collect_on_dialog_finish(self)
        self.setWindowTitle("Admin · Simulation mode")
        disable_help_button(self)
        restoreGeom(self, self.TITLE, default_size=(460, 460))

        layout = QVBoxLayout(self)

        warning = QLabel(
            "Developer tooling. These actions modify your collection directly "
            "(undoable with Ctrl+Z) and are not part of the honest scoring path. "
            "Use on a test/demo collection."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("color: #b9770e; font-weight: 600;")
        layout.addWidget(warning)

        # Shared scope ------------------------------------------------------
        scope_box = QGroupBox("Scope")
        scope_form = QFormLayout(scope_box)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("blank = all cards (e.g. deck:USMLE)")
        scope_form.addRow("Card search:", self.search_edit)
        layout.addWidget(scope_box)

        # Set FSRS state ----------------------------------------------------
        fsrs_box = QGroupBox("Set FSRS memory state")
        fsrs_form = QFormLayout(fsrs_box)

        self.stability = QDoubleSpinBox()
        self.stability.setRange(0.1, 3650.0)
        self.stability.setDecimals(1)
        self.stability.setSuffix(" days")
        self.stability.setValue(30.0)
        fsrs_form.addRow("Stability (S):", self.stability)

        self.difficulty = QDoubleSpinBox()
        self.difficulty.setRange(1.0, 10.0)
        self.difficulty.setDecimals(1)
        self.difficulty.setValue(5.0)
        fsrs_form.addRow("Difficulty (D):", self.difficulty)

        self.retrievability = QSpinBox()
        self.retrievability.setRange(1, 99)
        self.retrievability.setSuffix(" %")
        self.retrievability.setValue(90)
        fsrs_form.addRow("Retrievability now (R):", self.retrievability)

        self.fsrs_percent = QSpinBox()
        self.fsrs_percent.setRange(1, 100)
        self.fsrs_percent.setSuffix(" %")
        self.fsrs_percent.setValue(100)
        self.fsrs_percent.setToolTip(
            "Apply to a random subset of the matched cards (100% = all)."
        )
        fsrs_form.addRow("Apply to random:", self.fsrs_percent)

        self.apply_fsrs_btn = QPushButton("Apply FSRS state to matched cards")
        qconnect(self.apply_fsrs_btn.clicked, self._apply_fsrs)
        fsrs_form.addRow(self.apply_fsrs_btn)
        layout.addWidget(fsrs_box)

        # Reset to "not learned yet" ---------------------------------------
        reset_box = QGroupBox("Reset to \u201cnot learned yet\u201d (new)")
        reset_form = QFormLayout(reset_box)
        self.reset_percent = QSpinBox()
        self.reset_percent.setRange(1, 100)
        self.reset_percent.setSuffix(" %")
        self.reset_percent.setValue(100)
        self.reset_percent.setToolTip(
            "Reset a random subset of the matched cards to new (100% = all)."
        )
        reset_form.addRow("Reset random:", self.reset_percent)
        self.reset_btn = QPushButton("Reset matched cards to new")
        qconnect(self.reset_btn.clicked, self._reset_cards)
        reset_form.addRow(self.reset_btn)
        layout.addWidget(reset_box)

        # Advance days ------------------------------------------------------
        time_box = QGroupBox("Simulate elapsed time (no study)")
        time_form = QFormLayout(time_box)
        self.days = QSpinBox()
        self.days.setRange(1, 3650)
        self.days.setSuffix(" days")
        self.days.setValue(5)
        time_form.addRow("Advance by:", self.days)
        self.advance_btn = QPushButton("Advance matched cards")
        qconnect(self.advance_btn.clicked, self._advance_days)
        time_form.addRow(self.advance_btn)
        layout.addWidget(time_box)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        qconnect(buttons.rejected, self.close)
        layout.addWidget(buttons)

        self.show()

    # Operations ------------------------------------------------------------

    def _apply_fsrs(self) -> None:
        search = self.search_edit.text().strip()
        stability = self.stability.value()
        difficulty = self.difficulty.value()
        target_r = self.retrievability.value() / 100.0

        percent = self.fsrs_percent.value()
        sample = 0 if percent >= 100 else percent

        def op(col: aqt.Collection):
            return col.admin_set_fsrs(
                search=search,
                stability=stability,
                difficulty=difficulty,
                target_retrievability=target_r,
                sample_percent=sample,
            )

        def on_success(updated: int) -> None:
            scope = "" if sample == 0 else f" (random {percent}%)"
            msg = (
                f"Set S={stability:g}d, D={difficulty:g}, R={target_r:.0%} "
                f"on {updated} cards{scope}."
            )
            self.status.setText(msg)
            tooltip(msg, parent=self)
            self.mw.reset()

        QueryOp(parent=self, op=op, success=on_success).with_progress(
            "Setting FSRS state…"
        ).run_in_background()

    def _reset_cards(self) -> None:
        search = self.search_edit.text().strip()
        percent = self.reset_percent.value()
        sample = 0 if percent >= 100 else percent

        def op(col: aqt.Collection):
            return col.admin_reset_cards(search=search, sample_percent=sample)

        def on_success(updated: int) -> None:
            scope = "" if sample == 0 else f" (random {percent}%)"
            msg = f"Reset {updated} cards to not-learned{scope}."
            self.status.setText(msg)
            tooltip(msg, parent=self)
            self.mw.reset()

        QueryOp(parent=self, op=op, success=on_success).with_progress(
            "Resetting cards to new…"
        ).run_in_background()

    def _advance_days(self) -> None:
        search = self.search_edit.text().strip()
        days = self.days.value()

        def op(col: aqt.Collection):
            return col.admin_advance_days(search=search, days=days)

        def on_success(updated: int) -> None:
            msg = f"Advanced {updated} cards by {days} days (no study)."
            self.status.setText(msg)
            tooltip(msg, parent=self)
            self.mw.reset()

        QueryOp(parent=self, op=op, success=on_success).with_progress(
            "Simulating elapsed time…"
        ).run_in_background()

    # Window plumbing -------------------------------------------------------

    def closeEvent(self, evt) -> None:
        saveGeom(self, self.TITLE)
        aqt.dialogs.markClosed("AdminSimulation")
        if evt:
            evt.accept()

    def reject(self) -> None:
        self.close()
