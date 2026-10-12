"""R3 Reliability finding: a disable batch whose outcome for one target is
`monitors.OUTCOME_AMBIGUOUS` (a timed-out elevated helper, or a helper result
that never got written -- see `_finalize_disable_outcome`'s own comment that
an ambiguous target is deliberately left untouched in the on-disk record)
arms no auto-revert guard for that target, and the target is never
force-unlocked either. `Api.set_monitors_enabled`'s `enabled=True` branch
only trimmed the on-disk `pending_restore.json` entry for a target that
either had a live guard covering it (`_resolve_guard_for_enabled_ids`) or had
been force-unlocked (`_clear_force_unlocked_targets_from_pending`) -- an
ambiguous-outcome target matches neither, so its stale entry survived even
after the device's own observed state later confirmed, via an ordinary
enable call, that it really is enabled. `recheck_pending()`/`recover_on_boot`
would keep re-resolving that stale entry to `Resolution.CLEAR` forever,
which is neither IN_FLIGHT nor UNCONFIRMABLE, so `force_unlockable` stays
permanently False and the client's stale-disabled notice never clears on its
own.

The fix: `set_monitors_enabled`'s enable branch also unconditionally trims
`succeeded_ids` out of the on-disk record via `_remove_targets_from_pending`,
independent of whether a guard existed or a force-unlock was ever stamped --
the device's own observed state confirming it enabled is sufficient reason
to drop any record of it regardless of how its earlier disable outcome
resolved.
"""
import pytest

from quickres.webview.bridge import Api
from quickres import config, monitors


@pytest.fixture(autouse=True)
def _isolated_app_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(config, "PENDING_PATH", str(tmp_path / "pending_restore.json"))
    yield


class _FakeTimer:
    instances = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False
        _FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


@pytest.fixture(autouse=True)
def _fake_timer(monkeypatch):
    _FakeTimer.instances = []
    monkeypatch.setattr("quickres.webview.bridge.threading.Timer", _FakeTimer)
    yield _FakeTimer
    _FakeTimer.instances = []


_ALL_MONITORS = [
    {"instance_id": "DISPLAY\\A\\1", "friendly_name": "A", "enabled": True},
]


class TestAmbiguousDisableStaleEntryTrimmedOnConfirmedEnable:
    def test_no_guard_armed_but_entry_survives_an_ambiguous_disable(self, monkeypatch):
        """Baseline: confirms the setup actually reproduces the documented
        ambiguous-outcome gap before asserting the fix clears it."""
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors", lambda: _ALL_MONITORS,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Timed out waiting for helper", monitors.OUTCOME_AMBIGUOUS)
                for iid in ids
            ],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert api._pending_guard is None, "an ambiguous outcome must not arm a guard"
        record = config.load_pending()
        assert record is not None
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

    def test_confirmed_enable_trims_the_stale_ambiguous_entry(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors", lambda: _ALL_MONITORS,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Timed out waiting for helper", monitors.OUTCOME_AMBIGUOUS)
                for iid in ids
            ],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)
        assert config.load_pending() is not None, "sanity: the ambiguous entry is on disk"

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, True, "Confirmed by observed device state", monitors.OUTCOME_CONFIRMED)
                for iid in ids
            ],
        )
        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        assert result["ok"] is True
        assert result["data"]["results"][0][1] is True
        assert config.load_pending() is None, (
            "a confirmed enable must trim the stale ambiguous-outcome entry "
            "even though no guard was ever armed for it and it was never "
            "force-unlocked"
        )

    def test_notice_state_clears_after_the_confirmed_enable(self, monkeypatch):
        """Proxy for panel.html's guardStillUnresolved(): once the stale
        entry is trimmed, recheck_pending() must report no outcomes at all,
        which is exactly what makes the client's stale-disabled banner and
        its endless polling loop stop."""
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.enumerate_monitors", lambda: _ALL_MONITORS,
        )
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, "Timed out waiting for helper", monitors.OUTCOME_AMBIGUOUS)
                for iid in ids
            ],
        )
        api = Api()
        api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, True, "Confirmed by observed device state", monitors.OUTCOME_CONFIRMED)
                for iid in ids
            ],
        )
        api.set_monitors_enabled(["DISPLAY\\A\\1"], True)

        recheck = api.recheck_pending()
        assert recheck["ok"] is True
        assert recheck["data"]["outcomes"] == []
        assert recheck["data"]["force_unlockable"] is False
