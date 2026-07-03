# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

"""USMLE project: optional AI layer (off by default).

Currently hosts the Friday AI feature: AI rephrasing of a card's question text
on reappearance (SPOV4 / PRD §9a). Everything here is gated behind the
``aiRephraseEnabled`` collection-config flag, which defaults to ``False`` so the
no-AI build is unaffected and the app always scores with AI off.
"""
