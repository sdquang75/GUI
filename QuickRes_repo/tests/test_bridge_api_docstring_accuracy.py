"""Round 13 finding (medium, readability): `Api`'s class docstring used to
claim every public method was a "thin @bridge_op-wrapped delegation" into
display.py/hotkey.py/updater.py/config.py/monitors.py/recovery.py, with no
business logic of its own. That was false -- roughly half the file is
private helper methods implementing a self-contained crash-recovery/
auto-revert state machine directly in this class. This test guards against
that inaccuracy creeping back in: as long as those state-machine helpers
still exist on `Api`, the class docstring must not claim to be purely a
thin delegation surface.
"""
from quickres.webview.bridge import Api


# A representative sample of the private helpers that implement real
# business logic directly in Api (not delegated elsewhere).
_STATE_MACHINE_HELPERS = [
    "_resolve_guard_for_enabled_ids",
    "_build_and_save_pending_record",
    "_arm_auto_revert_guard",
    "_arm_guard_timer",
    "_maybe_retry_auto_revert",
    "_resolve_pending_now",
    "_resolve_pending_now_bounded_under_lock",
    "recover_on_boot",
]


def test_state_machine_helpers_still_exist_on_api():
    # Sanity check the premise: if these helpers ever get removed/renamed
    # (the god-object split is a deliberately deferred item, but could
    # still happen for other reasons), this test should be revisited
    # rather than silently passing for the wrong reason.
    for name in _STATE_MACHINE_HELPERS:
        assert hasattr(Api, name), f"expected helper {name} not found on Api"


def test_docstring_does_not_claim_purely_thin_delegation():
    doc = (Api.__doc__ or "").lower()
    assert "every public method is a thin" not in doc, (
        "Api's docstring appears to claim every public method is a thin "
        "delegation, but private state-machine helpers with real business "
        "logic exist on this class -- see the round 13 finding this guards "
        "against."
    )
    # More directly: the docstring must acknowledge it owns real logic,
    # not only delegate.
    assert "state machine" in doc or "state-machine" in doc, (
        "Api's docstring should describe its own crash-recovery/"
        "auto-revert state machine, not just describe it as a delegation "
        "surface"
    )
