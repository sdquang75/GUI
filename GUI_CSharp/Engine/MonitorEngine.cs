using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

namespace GUI.Engine
{
    public class MonitorInfo
    {
        public string InstanceId { get; set; } = "";
        public string FriendlyName { get; set; } = "";
        public bool Enabled { get; set; } = true;
        public uint DevInst { get; set; } = 0;
        public bool IsPrimary { get; set; } = false;
        // Enabled AND currently showing part of the desktop. An enabled devnode can still be dark
        // (laptop lid closed, monitor set to "Disconnect this display", input switched away).
        public bool IsActive { get; set; } = false;
    }

    public static class MonitorEngine
    {
        private static readonly Guid GUID_DEVCLASS_MONITOR = new("4d36e96e-e325-11ce-bfc1-08002be10318");

        private const uint DIGCF_PRESENT = 0x00000002;
        private const uint SPDRP_DEVICEDESC = 0x00000000;
        private const uint SPDRP_FRIENDLYNAME = 0x0000000C;
        private const uint CR_SUCCESS = 0;
        private const uint CM_PROB_DISABLED = 22;
        private const uint CM_LOCATE_DEVNODE_NORMAL = 0;

        [StructLayout(LayoutKind.Sequential)]
        private struct SP_DEVINFO_DATA
        {
            public uint cbSize;
            public Guid ClassGuid;
            public uint DevInst;
            public IntPtr Reserved;
        }

        [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr SetupDiGetClassDevsW(
            ref Guid ClassGuid,
            string? Enumerator,
            IntPtr hwndParent,
            uint Flags);

        [DllImport("setupapi.dll", SetLastError = true)]
        private static extern bool SetupDiEnumDeviceInfo(
            IntPtr DeviceInfoSet,
            uint MemberIndex,
            ref SP_DEVINFO_DATA DeviceInfoData);

        [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool SetupDiGetDeviceInstanceIdW(
            IntPtr DeviceInfoSet,
            ref SP_DEVINFO_DATA DeviceInfoData,
            StringBuilder? DeviceInstanceId,
            uint DeviceInstanceIdSize,
            out uint RequiredSize);

        [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool SetupDiGetDeviceRegistryPropertyW(
            IntPtr DeviceInfoSet,
            ref SP_DEVINFO_DATA DeviceInfoData,
            uint Property,
            out uint PropertyRegDataType,
            byte[]? PropertyBuffer,
            uint PropertyBufferSize,
            out uint RequiredSize);

        [DllImport("setupapi.dll", SetLastError = true)]
        private static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        private static extern uint CM_Get_DevNode_Status(
            out uint pulStatus,
            out uint pulProblemNumber,
            uint dnDevInst,
            uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern uint CM_Locate_DevNodeW(
            out uint pdnDevInst,
            string pDeviceID,
            uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        private static extern uint CM_Enable_DevNode(uint dnDevInst, uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        private static extern uint CM_Disable_DevNode(uint dnDevInst, uint ulFlags);

        public static bool IsAdministrator()
        {
            try
            {
                using var identity = WindowsIdentity.GetCurrent();
                var principal = new WindowsPrincipal(identity);
                return principal.IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch
            {
                return false;
            }
        }

        public static List<MonitorInfo> EnumerateMonitors()
        {
            var list = new List<MonitorInfo>();
            Guid guid = GUID_DEVCLASS_MONITOR;
            IntPtr hDevInfo = SetupDiGetClassDevsW(ref guid, null, IntPtr.Zero, DIGCF_PRESENT);
            if (hDevInfo == IntPtr.Zero || hDevInfo == new IntPtr(-1))
            {
                return list;
            }

            try
            {
                uint index = 0;
                var devInfo = new SP_DEVINFO_DATA();
                devInfo.cbSize = (uint)Marshal.SizeOf<SP_DEVINFO_DATA>();

                while (SetupDiEnumDeviceInfo(hDevInfo, index, ref devInfo))
                {
                    // 1. Get Instance ID
                    SetupDiGetDeviceInstanceIdW(hDevInfo, ref devInfo, null, 0, out uint reqSize);
                    string instanceId = "";
                    if (reqSize > 0)
                    {
                        var sb = new StringBuilder((int)reqSize);
                        if (SetupDiGetDeviceInstanceIdW(hDevInfo, ref devInfo, sb, reqSize, out _))
                        {
                            instanceId = sb.ToString();
                        }
                    }

                    // 2. Get Friendly Name or Description
                    string friendlyName = GetDevicePropertyString(hDevInfo, ref devInfo, SPDRP_FRIENDLYNAME);
                    if (string.IsNullOrWhiteSpace(friendlyName))
                    {
                        friendlyName = GetDevicePropertyString(hDevInfo, ref devInfo, SPDRP_DEVICEDESC);
                    }
                    if (string.IsNullOrWhiteSpace(friendlyName))
                    {
                        friendlyName = instanceId;
                    }

                    // 3. Check Enabled Status
                    bool enabled = true;
                    if (CM_Get_DevNode_Status(out uint _, out uint probNum, devInfo.DevInst, 0) == CR_SUCCESS)
                    {
                        enabled = (probNum != CM_PROB_DISABLED);
                    }

                    list.Add(new MonitorInfo
                    {
                        InstanceId = instanceId,
                        FriendlyName = friendlyName,
                        Enabled = enabled,
                        DevInst = devInfo.DevInst
                    });

                    index++;
                    devInfo = new SP_DEVINFO_DATA();
                    devInfo.cbSize = (uint)Marshal.SizeOf<SP_DEVINFO_DATA>();
                }
            }
            finally
            {
                SetupDiDestroyDeviceInfoList(hDevInfo);
            }

            // Determine primary monitor (device IDs under the primary adapter)
            DisplayTopology.GetPrimaryMonitorIds(out var primaryMonitorInterfaceIds, out var primaryMonitorPnpIds);

            if (list.Count == 1)
            {
                list[0].IsPrimary = true;
            }
            else if (list.Count > 1)
            {
                bool foundPrimary = false;

                // Priority 1: Exact unique matching using interface path (perfectly resolves dual identical monitors)
                foreach (var m in list)
                {
                    string normalizedInstance = m.InstanceId.Replace('\\', '#');
                    foreach (var ifaceId in primaryMonitorInterfaceIds)
                    {
                        if (ifaceId.IndexOf(normalizedInstance, StringComparison.OrdinalIgnoreCase) >= 0)
                        {
                            m.IsPrimary = true;
                            foundPrimary = true;
                            break;
                        }
                    }
                    if (foundPrimary) break;
                }

                // Priority 2: Fallback to PNP model ID matching
                if (!foundPrimary)
                {
                    foreach (var m in list)
                    {
                        foreach (var pId in primaryMonitorPnpIds)
                        {
                            var parts = pId.Split('\\');
                            if (parts.Length > 1 && !string.IsNullOrEmpty(parts[1]))
                            {
                                if (m.InstanceId.IndexOf(parts[1], StringComparison.OrdinalIgnoreCase) >= 0)
                                {
                                    m.IsPrimary = true;
                                    foundPrimary = true;
                                    break;
                                }
                            }
                        }
                        if (foundPrimary) break;
                    }
                }

                if (!foundPrimary && list.Count > 0)
                {
                    list[0].IsPrimary = true;
                }
            }

            MarkActiveMonitors(list);
            return list;
        }

        // A monitor shows the desktop when an active DisplayConfig path's device path ("\\?\DISPLAY#DEL4123#5&...#{guid}")
        // contains its instance ID ("DISPLAY\DEL4123\5&...") with '\' as '#'. Without any device path (DisplayConfig
        // failed) there is nothing to compare, so every enabled monitor counts as active.
        private static void MarkActiveMonitors(List<MonitorInfo> list)
        {
            var displays = DisplayTopology.GetDisplays();
            bool pathsKnown = false;
            foreach (var d in displays)
            {
                if (!string.IsNullOrEmpty(d.DevicePath))
                {
                    pathsKnown = true;
                    break;
                }
            }

            foreach (var m in list)
            {
                if (!m.Enabled || string.IsNullOrEmpty(m.InstanceId))
                {
                    m.IsActive = false;
                    continue;
                }
                if (!pathsKnown)
                {
                    m.IsActive = true;
                    continue;
                }

                string normalizedInstance = m.InstanceId.Replace('\\', '#');
                m.IsActive = false;
                foreach (var d in displays)
                {
                    if (d.DevicePath.IndexOf(normalizedInstance, StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        m.IsActive = true;
                        break;
                    }
                }
            }
        }

        private static bool _cachedHasDisabled = false;
        private static long _lastDisabledCheckTick = 0;
        private static readonly object _monitorCheckLock = new object();

        public static void InvalidateMonitorCache()
        {
            lock (_monitorCheckLock)
            {
                _lastDisabledCheckTick = 0;
                _disabledCacheGeneration++;
            }
            DisplayTopology.Invalidate();
        }

        public static bool HasAnyDisabledMonitors()
        {
            lock (_monitorCheckLock)
            {
                // Always the real devnode state: a monitor this app disabled may have been re-enabled elsewhere
                // (Device Manager, replug, another tool), and trusting _disabledInSession would pin gamma to the
                // desktop-wide DWM path for the rest of the session.
                long now = Environment.TickCount64;
                if (_lastDisabledCheckTick != 0)
                {
                    // Only the 60s TTL expired (no topology change): answer from cache and rescan in the background.
                    // The SetupAPI scan takes several ms and this runs inside GammaEngine.ApplyCurve, so a synchronous
                    // rescan would land on the first black flash after a quiet minute.
                    if (now - _lastDisabledCheckTick >= 60000 && !_disabledRefreshPending)
                    {
                        _disabledRefreshPending = true;
                        int generation = _disabledCacheGeneration;
                        ThreadPool.QueueUserWorkItem(_ => RefreshDisabledCache(generation));
                    }
                    return _cachedHasDisabled;
                }

                // Never scanned, or invalidated by a display change / monitor enable-disable: must be accurate now
                _lastDisabledCheckTick = now;
                _cachedHasDisabled = ScanHasDisabled(out var enabledIds);
                ForgetReenabled(enabledIds);
                return _cachedHasDisabled;
            }
        }

        // Drops monitors this app disabled that are present and enabled again, so RestoreOnExit does not re-enable
        // (or ask UAC for) something already on. Caller holds _monitorCheckLock.
        private static void ForgetReenabled(HashSet<string> enabledIds)
        {
            if (_disabledInSession.Count == 0) return;
            int removed = _disabledInSession.RemoveWhere(id => enabledIds.Contains(id));
            if (removed > 0)
            {
                Program.Log($"MonitorEngine: {removed} monitor(s) disabled by this app were re-enabled elsewhere.");
            }
        }

        private static bool _disabledRefreshPending = false;
        private static int _disabledCacheGeneration = 0;

        private static void RefreshDisabledCache(int generation)
        {
            bool hasDisabled = ScanHasDisabled(out var enabledIds);
            lock (_monitorCheckLock)
            {
                _disabledRefreshPending = false;
                // An invalidation during the scan wins: its synchronous rescan is newer than this result
                if (generation != _disabledCacheGeneration) return;
                _cachedHasDisabled = hasDisabled;
                _lastDisabledCheckTick = Environment.TickCount64;
                ForgetReenabled(enabledIds);
            }
        }

        private static bool ScanHasDisabled(out HashSet<string> enabledIds)
        {
            enabledIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            bool hasDisabled = false;
            try
            {
                foreach (var m in EnumerateMonitors())
                {
                    if (!m.Enabled) hasDisabled = true;
                    else if (!string.IsNullOrEmpty(m.InstanceId)) enabledIds.Add(m.InstanceId);
                }
            }
            catch { }
            return hasDisabled;
        }

        public static bool IsPrimaryMonitorDisabled()
        {
            try
            {
                var list = EnumerateMonitors();
                foreach (var m in list)
                {
                    if (m.IsPrimary && !m.Enabled) return true;
                }
            }
            catch { }
            return false;
        }

        private static string GetDevicePropertyString(IntPtr hDevInfo, ref SP_DEVINFO_DATA devInfo, uint property)
        {
            SetupDiGetDeviceRegistryPropertyW(hDevInfo, ref devInfo, property, out _, null, 0, out uint reqSize);
            if (reqSize > 0)
            {
                byte[] buffer = new byte[reqSize];
                if (SetupDiGetDeviceRegistryPropertyW(hDevInfo, ref devInfo, property, out _, buffer, reqSize, out _))
                {
                    return Encoding.Unicode.GetString(buffer).TrimEnd('\0');
                }
            }
            return "";
        }

        private static readonly HashSet<string> _disabledInSession = new(StringComparer.OrdinalIgnoreCase);

        public static (bool Success, string Message) SetMonitorEnabled(string instanceId, bool enable)
        {
            if (string.IsNullOrWhiteSpace(instanceId))
            {
                return (false, "Invalid instance ID.");
            }

            (bool Success, string Message) result;
            if (IsAdministrator())
            {
                result = SetDevNodeEnabledDirect(instanceId, enable);
            }
            else
            {
                // Launch self with elevation
                try
                {
                    string? exePath = Environment.ProcessPath;
                    if (string.IsNullOrEmpty(exePath))
                    {
                        return (false, "Could not determine application path for elevation.");
                    }

                    var psi = new ProcessStartInfo
                    {
                        FileName = exePath,
                        Arguments = $"--monitor-op {(enable ? "enable" : "disable")} \"{instanceId}\"",
                        Verb = "runas",
                        UseShellExecute = true,
                        CreateNoWindow = true,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };

                    using var proc = Process.Start(psi);
                    if (proc == null) return (false, "UAC prompt was cancelled.");

                    if (!proc.WaitForExit(30000))
                    {
                        return (false, "Operation timed out waiting for elevated helper.");
                    }

                    if (proc.ExitCode == 0)
                    {
                        result = (true, $"Monitor {(enable ? "enabled" : "disabled")} successfully.");
                    }
                    else
                    {
                        result = (false, $"Operation failed with exit code {proc.ExitCode}.");
                    }
                }
                catch (System.ComponentModel.Win32Exception wEx) when (wEx.NativeErrorCode == 1223)
                {
                    result = (false, "UAC prompt was cancelled.");
                }
                catch (Exception ex)
                {
                    result = (false, $"Elevation request failed: {ex.Message}");
                }
            }

            if (result.Success)
            {
                lock (_monitorCheckLock)
                {
                    InvalidateMonitorCache();
                    if (!enable)
                    {
                        _disabledInSession.Add(instanceId);
                    }
                    else
                    {
                        _disabledInSession.Remove(instanceId);
                    }
                }
            }

            return result;
        }

        public static void RestoreOnExit()
        {
            try
            {
                List<string> toEnable;
                lock (_monitorCheckLock)
                {
                    if (_disabledInSession.Count == 0) return;

                    // If not running as admin, do NOT trigger a UAC prompt during app exit or OS shutdown
                    if (!IsAdministrator())
                    {
                        Program.Log("MonitorEngine.RestoreOnExit: Skipping restore because process is not elevated (avoid UAC prompt on exit).");
                        return;
                    }

                    toEnable = new List<string>(_disabledInSession);
                    _disabledInSession.Clear();
                    InvalidateMonitorCache();
                }

                foreach (var id in toEnable)
                {
                    SetDevNodeEnabledDirect(id, true);
                }
            }
            catch (Exception ex)
            {
                Program.Log($"MonitorEngine.RestoreOnExit error: {ex.Message}");
            }
        }

        public static (bool Success, string Message) SetDevNodeEnabledDirect(string instanceId, bool enable)
        {
            uint cr = CM_Locate_DevNodeW(out uint devInst, instanceId, CM_LOCATE_DEVNODE_NORMAL);
            if (cr != CR_SUCCESS)
            {
                return (false, $"Could not locate device node (CR code {cr}).");
            }

            cr = enable ? CM_Enable_DevNode(devInst, 0) : CM_Disable_DevNode(devInst, 0);
            if (cr == CR_SUCCESS)
            {
                return (true, $"Monitor {(enable ? "enabled" : "disabled")} successfully.");
            }
            return (false, $"Failed to {(enable ? "enable" : "disable")} device (CR code {cr}).");
        }
    }
}
