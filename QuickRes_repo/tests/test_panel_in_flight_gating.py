"""R3 Reliability finding (CRITICAL), UI-level companion to bridge.py's
`_InFlightStillPending` fix: `panel.html`'s Keep-disabled/Revert-now buttons
used to render for ANY pending outcome, including one still resolved as
IN_FLIGHT -- clicking either while genuinely in flight only ever reaches
bridge.py's own "cannot act yet" rejection. Gating them out for that specific
resolution, mirroring how the Force Unlock button is already gated on
`force_unlockable`, keeps the user from reaching that dead-end click at all.
"""
import re
from pathlib import Path

import pytest

PANEL_PATH = Path(__file__).resolve().parent.parent / "quickres" / "webview" / "panel.html"


@pytest.fixture(scope="module")
def panel_html():
    return PANEL_PATH.read_text(encoding="utf-8")


def _script_block(html: str) -> str:
    matches = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert len(matches) == 1, f"expected exactly one <script> block, found {len(matches)}"
    return matches[0]


def _render_monitors_modal_body(js: str) -> str:
    fn_match = re.search(r"function renderMonitorsModal\(\) \{[\s\S]*?\n\}\n", js)
    assert fn_match, "expected a renderMonitorsModal function"
    return fn_match.group(0)


def test_keep_and_revert_buttons_are_gated_on_resolution_not_in_flight(panel_html):
    js = _script_block(panel_html)
    body = _render_monitors_modal_body(js)

    assert re.search(r"resolution\s*===\s*'in_flight'", body), (
        "expected renderMonitorsModal to check for an in_flight outcome "
        "resolution before offering Keep-disabled/Revert-now"
    )

    keep_idx = body.index("qr-notice-keep")
    revert_idx = body.index("qr-notice-revert")
    gate_idx = body.index("resolution === 'in_flight'")
    assert gate_idx < keep_idx and gate_idx < revert_idx, (
        "the in_flight check must precede both button ids, i.e. actually "
        "guard their creation rather than merely being present somewhere "
        "else in the function"
    )

    # Still present -- gated, not removed outright (matches test_43's
    # existing expectation that both ids remain in the function).
    assert "qr-notice-keep" in body and "qr-notice-revert" in body


def test_force_unlock_button_remains_gated_on_force_unlockable(panel_html):
    js = _script_block(panel_html)
    body = _render_monitors_modal_body(js)
    assert "if (S.pending.force_unlockable) {" in body, (
        "Force Unlock's existing gate must be unchanged by this fix"
    )
