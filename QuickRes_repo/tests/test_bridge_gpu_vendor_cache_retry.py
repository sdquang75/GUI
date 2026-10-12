"""Round 24 finding (R4 Resilience, low): a single transient GPU-vendor-
detection failure used to be cached for the lifetime of the process, with no
retry -- permanently degrading the "resolution not found" dialog's
vendor-specific button.

`display.detect_gpu_vendors()` runs a PowerShell subprocess with a 5-second
timeout and returns an EMPTY set (falsy, but not None) on any failure
(timeout, PowerShell/WMI not yet warmed up right after boot, a transient
AV/EDR scan delay). `Api.pick_resolution` only re-ran detection when
`self._gpu_vendors is None` -- once a failed detection produced an empty
set, that empty result was cached forever, and every LATER "resolution not
found" dialog fell back to listing all three vendor buttons regardless of
what GPU is actually present, even though a retry moments later (once
PowerShell/WMI finished warming up) would likely succeed.

Fix: only a NON-EMPTY detection result is cached and reused. `None` or an
empty set both mean "no confirmed answer yet" and re-trigger
`detect_gpu_vendors()` on the next `pick_resolution` call.
"""
import pytest

from quickres.webview.bridge import Api


class TestGpuVendorCacheRetriesAfterAFailedDetection:
    def test_empty_detection_is_not_cached_and_a_later_call_retries(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.display.get_supported_resolutions", lambda: {(1920, 1080)}
        )
        calls = {"n": 0}

        def flaky_detect():
            calls["n"] += 1
            if calls["n"] == 1:
                return set()  # transient failure -- e.g. early-boot PowerShell timeout
            return {"nvidia"}

        monkeypatch.setattr("quickres.webview.bridge.display.detect_gpu_vendors", flaky_detect)
        api = Api()

        first = api.pick_resolution(1280, 720)
        assert sorted(first["data"]["vendors"]) == ["amd", "intel", "nvidia"]

        second = api.pick_resolution(1280, 720)

        assert calls["n"] == 2, "an empty/failed detection must not be cached -- the next call must retry"
        assert second["data"]["vendors"] == ["nvidia"], (
            "a later successful detection must actually be picked up, not "
            "shadowed by the earlier failed (empty) cached result"
        )

    def test_non_empty_detection_is_still_cached_across_calls(self, monkeypatch):
        monkeypatch.setattr(
            "quickres.webview.bridge.display.get_supported_resolutions", lambda: {(1920, 1080)}
        )
        calls = {"n": 0}

        def detect():
            calls["n"] += 1
            return {"amd"}

        monkeypatch.setattr("quickres.webview.bridge.display.detect_gpu_vendors", detect)
        api = Api()

        api.pick_resolution(1280, 720)
        api.pick_resolution(1280, 720)

        assert calls["n"] == 1, (
            "a genuinely successful (non-empty) detection must still be "
            "cached and reused, not re-run on every call"
        )
