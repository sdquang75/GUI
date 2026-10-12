import winreg

import pytest

from quickres import config


class TestDetectSystemTheme:
    def test_light_when_registry_value_is_one(self, monkeypatch):
        monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: object())
        monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (1, winreg.REG_DWORD))

        assert config.detect_system_theme() == "light"

    def test_dark_when_registry_value_is_zero(self, monkeypatch):
        monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: object())
        monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (0, winreg.REG_DWORD))

        assert config.detect_system_theme() == "dark"

    def test_falls_back_to_dark_when_key_missing(self, monkeypatch):
        def _raise(*a, **k):
            raise OSError("key not found")

        monkeypatch.setattr(winreg, "OpenKey", _raise)

        assert config.detect_system_theme() == "dark"
