"""Round-2 finding: `_set_devnode_enabled` (the elevated helper's own
mutation primitive) enabled/disabled any PnP device whose instance id
merely passed the injection-safety regex, without ever verifying the
resolved devnode actually belongs to GUID_DEVCLASS_MONITOR before calling
CM_Enable_DevNode/CM_Disable_DevNode. Not currently reachable via the
shipped UI (panel.html only ever sends ids sourced from
enumerate_monitors()), but the elevated primitive's own authorization
boundary should not rely entirely on a well-behaved caller.
"""

import pytest

import quickres.monitors as monitors_mod
from quickres.monitors import GUID_DEVCLASS_MONITOR

CR_SUCCESS = 0
CR_NO_SUCH_DEVNODE = 0x0000000D


def test_set_devnode_enabled_refuses_non_monitor_class_device(monkeypatch):
    monkeypatch.setattr(monitors_mod, "_locate_devnode", lambda instance_id: (CR_SUCCESS, 42))
    monkeypatch.setattr(
        monitors_mod, "_devnode_class_guid",
        lambda devinst: "00000000-0000-0000-0000-000000000000",
    )
    mutate_calls = []
    monkeypatch.setattr(
        monitors_mod.cfgmgr32, "CM_Disable_DevNode",
        lambda devinst, flags: mutate_calls.append(devinst) or CR_SUCCESS,
    )

    with pytest.raises(PermissionError):
        monitors_mod._set_devnode_enabled("USB\\NOT_A_MONITOR\\1", enable=False)

    assert mutate_calls == [], "must never mutate a non-GUID_DEVCLASS_MONITOR devnode"


def test_set_devnode_enabled_allows_a_real_monitor_class_device(monkeypatch):
    monkeypatch.setattr(monitors_mod, "_locate_devnode", lambda instance_id: (CR_SUCCESS, 42))
    monkeypatch.setattr(monitors_mod, "_devnode_class_guid", lambda devinst: GUID_DEVCLASS_MONITOR)
    monkeypatch.setattr(
        monitors_mod.cfgmgr32, "CM_Disable_DevNode", lambda devinst, flags: CR_SUCCESS
    )

    ok, message = monitors_mod._set_devnode_enabled("DISPLAY\\REAL\\1", enable=False)

    assert ok is True


def test_get_devnode_class_guid_normalizes_case_and_braces(monkeypatch):
    # _devnode_class_guid is a thin passthrough seam over this raw ctypes
    # query -- the actual case/brace normalization lives here, and this is
    # what _set_devnode_enabled's GUID_DEVCLASS_MONITOR.lower() comparison
    # relies on to match regardless of how the OS happened to format it.
    def fake_get_property(devinst, prop_id, reg_type_ptr, buf, length_ptr, flags):
        buf.value = "{4D36E96E-E325-11CE-BFC1-08002BE10318}"
        return CR_SUCCESS

    monkeypatch.setattr(
        monitors_mod.cfgmgr32, "CM_Get_DevNode_Registry_PropertyW", fake_get_property
    )

    result = monitors_mod._get_devnode_class_guid(42)

    assert result == GUID_DEVCLASS_MONITOR.lower()


def test_devnode_not_found_never_reaches_the_class_check(monkeypatch):
    class_check_calls = []
    monkeypatch.setattr(
        monitors_mod, "_locate_devnode", lambda instance_id: (CR_NO_SUCH_DEVNODE, 0)
    )
    monkeypatch.setattr(
        monitors_mod, "_devnode_class_guid",
        lambda devinst: class_check_calls.append(devinst) or GUID_DEVCLASS_MONITOR,
    )

    ok, message = monitors_mod._set_devnode_enabled("DISPLAY\\GONE\\1", enable=True)

    assert ok is False
    assert class_check_calls == []
