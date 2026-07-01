# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""USMLE project: the Readiness dashboard (Memory + Coverage + Readiness).

A thin Qt shell that hosts the SvelteKit ``readiness`` page, which fetches its
data from the Rust ``study_dashboard`` backend call.
"""

from __future__ import annotations

import aqt
import aqt.main
from aqt.qt import (
    QCloseEvent,
    QDialog,
    Qt,
    QVBoxLayout,
)
from aqt.utils import disable_help_button, restoreGeom, saveGeom
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
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.web)
        self.setLayout(layout)
        self.setWindowTitle("Readiness")
        self.show()

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
