from quickres import config


def test_foreground_succeeds_immediately_when_window_already_exists(monkeypatch):
    """A genuinely-already-running instance (window already exists) must
    resolve on the very first attempt -- no sleeping/polling at all."""
    monkeypatch.setattr(config, "_find_and_foreground_attempt", lambda: True)
    sleep_calls = []
    monkeypatch.setattr(config.time, "sleep", lambda s: sleep_calls.append(s))

    config._foreground_existing_window()

    assert sleep_calls == []


def test_foreground_retries_until_window_appears_then_foregrounds(monkeypatch):
    """Round 4 fix: the mutex is created before the pywebview window exists,
    so a second launch racing that startup gap used to get one immediate
    FindWindowW attempt that found nothing yet, then silently give up. This
    proves the first immediate failure is no longer final: if the window
    "appears" (the injectable attempt seam starts returning success)
    partway through the retry window, it still gets found and foregrounded
    instead of the caller giving up after attempt #1."""
    attempts = []

    def fake_attempt():
        attempts.append(1)
        return len(attempts) >= 3  # fails twice, then the window "appears"

    monkeypatch.setattr(config, "_find_and_foreground_attempt", fake_attempt)
    sleep_calls = []
    monkeypatch.setattr(config.time, "sleep", lambda s: sleep_calls.append(s))

    config._foreground_existing_window()

    assert len(attempts) == 3
    assert len(sleep_calls) == 2  # slept only between the two failed attempts


def test_foreground_gives_up_after_bounded_grace_period(monkeypatch):
    """A genuinely-not-running scenario (window never appears) must not hang
    the second launch indefinitely -- the retry loop is bounded by a small
    total grace period, driven off a fake monotonic clock advanced by the
    (mocked, non-blocking) sleep calls."""
    monkeypatch.setattr(config, "_find_and_foreground_attempt", lambda: False)
    clock = {"t": 0.0}
    monkeypatch.setattr(config.time, "monotonic", lambda: clock["t"])

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(config.time, "sleep", fake_sleep)

    config._foreground_existing_window()  # must return, not hang forever

    assert clock["t"] <= 3.0  # bounded well under a real-world-noticeable hang


def test_first_launch_acquires_and_returns_true(monkeypatch):
    monkeypatch.setattr(config, "_create_or_open_mutex", lambda: ("mutex-handle", False))
    foreground_calls = []
    monkeypatch.setattr(
        config, "_foreground_existing_window", lambda: foreground_calls.append(1)
    )

    result = config.enforce_single_instance()

    assert result is True
    assert foreground_calls == []


def test_second_launch_returns_false_and_foregrounds_existing_window(monkeypatch):
    monkeypatch.setattr(config, "_create_or_open_mutex", lambda: (None, True))
    foreground_calls = []
    monkeypatch.setattr(
        config, "_foreground_existing_window", lambda: foreground_calls.append(1)
    )

    result = config.enforce_single_instance()

    assert result is False
    assert foreground_calls == [1]
