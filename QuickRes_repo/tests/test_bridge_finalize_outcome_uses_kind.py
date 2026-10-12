"""_finalize_disable_outcome() used to classify each per-target result by
equality-comparing the human-readable `message` field against three imported
sentinel constants (monitors.TIMEOUT_MESSAGE,
monitors.HELPER_RESULT_UNCONFIRMED_MESSAGE,
monitors.HELPER_OBSERVED_MISMATCH_MESSAGE) -- a message that happened not to
match any of the three silently fell into the "genuine failure" bucket, even
when it was really an ambiguous/unconfirmed outcome. `message` is display/
logging text (used verbatim by panel.html's notice banner and by log_msg)
with nothing in its name or type signalling it also doubled as a hidden
control-flow discriminant.

Fix: `monitors.set_monitors_enabled` now produces an explicit 4th field
(`kind`, one of `monitors.OUTCOME_CONFIRMED` / `OUTCOME_GENUINE_FAILURE` /
`OUTCOME_AMBIGUOUS`) alongside `message`, decided structurally at the exact
branch that produces each outcome. `_finalize_disable_outcome` branches on
this field directly instead of message-equality.

These tests prove the fix by feeding `_finalize_disable_outcome` a message
string that is NOT any of the three legacy sentinel constants -- exactly the
"a future contributor adds a similar message without remembering to update
every equality chain" risk the old design was exposed to -- and showing the
outcome is classified purely from `kind`, never from what the message text
happens to say.
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


def _known_monitor(monkeypatch, instance_id="DISPLAY\\A\\1", friendly_name="A"):
    monkeypatch.setattr(
        "quickres.webview.bridge.monitors.enumerate_monitors",
        lambda: [{"instance_id": instance_id, "friendly_name": friendly_name, "enabled": True}],
    )


class TestClassificationReadsKindNotMessageText:
    def test_brand_new_ambiguous_message_not_matching_any_sentinel_is_still_left_pending(
        self, monkeypatch
    ):
        # This message string is deliberately something none of the three
        # legacy sentinel constants equal, and is not itself one of those
        # constants -- under the old message-equality chain this would have
        # been silently misclassified as a genuine failure and trimmed.
        _known_monitor(monkeypatch)
        novel_message = "A brand-new elevated helper outcome nobody has named yet"
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, novel_message, monitors.OUTCOME_AMBIGUOUS) for iid in ids
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        record = config.load_pending()
        assert record is not None, (
            "kind=OUTCOME_AMBIGUOUS must survive regardless of what the "
            "message text says, or whether it matches any known constant"
        )
        assert {t["instance_id"] for t in record["targets"]} == {"DISPLAY\\A\\1"}

    def test_a_message_that_happens_to_equal_a_legacy_sentinel_but_is_tagged_genuine_failure_is_trimmed(
        self, monkeypatch
    ):
        # The inverse: even monitors.TIMEOUT_MESSAGE's own literal text must
        # not, by itself, protect a result from being trimmed -- only
        # `kind` decides. (monitors.py itself never actually produces this
        # combination; this is a direct unit-level proof that
        # _finalize_disable_outcome truly reads `kind`, not `message`.)
        _known_monitor(monkeypatch)
        monkeypatch.setattr(
            "quickres.webview.bridge.monitors.set_monitors_enabled",
            lambda ids, enabled, **kwargs: [
                (iid, False, monitors.TIMEOUT_MESSAGE, monitors.OUTCOME_GENUINE_FAILURE)
                for iid in ids
            ],
        )
        api = Api()

        result = api.set_monitors_enabled(["DISPLAY\\A\\1"], False)

        assert result["ok"] is True
        assert config.load_pending() is None, (
            "kind=OUTCOME_GENUINE_FAILURE must be trimmed even though the "
            "message text is verbatim monitors.TIMEOUT_MESSAGE"
        )

    def test_real_set_monitors_enabled_tags_a_never_before_seen_failure_branch_as_ambiguous(
        self, monkeypatch, tmp_path
    ):
        # Integration-level proof against the REAL monitors.set_monitors_enabled
        # (not a bridge.py mock): the "helper completed, reported nothing,
        # and the observed-state re-check also came back undetermined"
        # branch produces monitors.HELPER_RESULT_UNCONFIRMED_MESSAGE tagged
        # with OUTCOME_AMBIGUOUS, produced structurally at the point of
        # decision -- not inferred afterward from the message text.
        import quickres.monitors as monitors_mod

        monkeypatch.setattr(monitors_mod, "_launch_elevated_helper", lambda params: "handle")
        monkeypatch.setattr(monitors_mod, "_wait_for_helper", lambda handle, timeout_s: True)
        monkeypatch.setattr(monitors_mod, "read_op_result", lambda path, app_dir: None)
        monkeypatch.setattr(monitors_mod, "sample_device_states", lambda ids: {"A": None})

        result = monitors_mod.set_monitors_enabled(["A"], False, app_dir=str(tmp_path))

        assert len(result) == 1
        instance_id, ok, message, kind = result[0]
        assert ok is False
        assert kind == monitors.OUTCOME_AMBIGUOUS
        assert message == monitors.HELPER_RESULT_UNCONFIRMED_MESSAGE
