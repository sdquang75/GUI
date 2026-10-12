import quickres.config as config


class _FakeKernel32:
    """Stands in for config.kernel32 so _create_or_open_mutex() can be
    driven through CreateMutexW failure modes other than the expected
    ERROR_ALREADY_EXISTS happy path."""

    def __init__(self, mutex_handle, last_error):
        self.mutex_handle = mutex_handle
        self.last_error = last_error
        self.create_mutex_calls = []

    def CreateMutexW(self, security_attrs, initial_owner, name):
        self.create_mutex_calls.append((security_attrs, initial_owner, name))
        return self.mutex_handle

    def GetLastError(self):
        return self.last_error

    def LocalFree(self, ptr):
        pass


def test_null_handle_with_unexpected_error_is_logged_and_fails_closed(monkeypatch):
    """Only ERROR_ALREADY_EXISTS ever meant "another instance is running".
    Any OTHER CreateMutexW failure (e.g. a pre-existing differently-typed
    kernel object squatting the same name, returning ERROR_INVALID_HANDLE)
    used to be silently folded into "this is the first instance" with zero
    logging -- a same-user process could exploit this to make the guard
    fail open. This proves an unexpected failure is now logged (for
    diagnosability) and treated as already_running=True (fail closed --
    refusing to start a second instance is safer than silently allowing
    one because the guard itself broke)."""
    ERROR_INVALID_HANDLE = 6
    fake = _FakeKernel32(mutex_handle=0, last_error=ERROR_INVALID_HANDLE)
    monkeypatch.setattr(config, "kernel32", fake)
    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    mutex, already_running = config._create_or_open_mutex()

    assert not mutex
    assert already_running is True
    assert len(log_calls) == 1
    assert str(ERROR_INVALID_HANDLE) in log_calls[0]


def test_expected_already_exists_failure_is_not_logged(monkeypatch):
    """Regression guard: the normal "another instance is already running"
    path (a valid, non-null handle to the pre-existing mutex, with
    GetLastError() == ERROR_ALREADY_EXISTS) must not start logging noise on
    every ordinary second launch."""
    fake = _FakeKernel32(mutex_handle=123, last_error=config.ERROR_ALREADY_EXISTS)
    monkeypatch.setattr(config, "kernel32", fake)
    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    mutex, already_running = config._create_or_open_mutex()

    assert mutex == 123
    assert already_running is True
    assert log_calls == []


def test_success_first_instance_is_not_logged(monkeypatch):
    fake = _FakeKernel32(mutex_handle=42, last_error=0)
    monkeypatch.setattr(config, "kernel32", fake)
    log_calls = []
    monkeypatch.setattr(config, "log_msg", lambda msg: log_calls.append(msg))

    mutex, already_running = config._create_or_open_mutex()

    assert mutex == 42
    assert already_running is False
    assert log_calls == []
