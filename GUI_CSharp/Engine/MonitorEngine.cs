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
    }

    public class ActiveMonitor
    {
        public string GdiName { get; set; } = "";        // "\\.\DISPLAY2" (can change across reboots / replugs)
        public string DevicePath { get; set; } = "";     // stable per monitor + connector, saved in the profile
        public string FriendlyName { get; set; } = "";   // EDID name, e.g. "DELL S2721DGF"
        public string Connection { get; set; } = "";     // HDMI / DisplayPort / Laptop panel ...
        public string AdapterName { get; set; } = "";    // GPU driving it (iGPU vs NVIDIA on hybrid laptops)
        public bool IsPrimary { get; set; }
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

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct DISPLAY_DEVICEW
        {
            public int cb;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
            public string DeviceName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string DeviceString;
            public int StateFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string DeviceID;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string DeviceKey;
        }

        private const int DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004;

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool EnumDisplayDevicesW(
            string? lpDevice,
            uint iDevNum,
            ref DISPLAY_DEVICEW lpDisplayDevice,
            uint dwFlags);

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

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct MONITORINFOEXW
        {
            public int cbSize;
            public RECT rcMonitor;
            public RECT rcWork;
            public uint dwFlags;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
            public string szDevice;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left, Top, Right, Bottom;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct POINT
        {
            public int X, Y;
        }

        private const uint MONITOR_DEFAULTTOPRIMARY = 0x00000001;

        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromPoint(POINT pt, uint dwFlags);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool GetMonitorInfoW(IntPtr hMonitor, ref MONITORINFOEXW lpmi);

        // --- DisplayConfig API: real monitor names, connector type and a stable device path per monitor ---
        private const uint QDC_ONLY_ACTIVE_PATHS = 0x00000002;
        private const uint DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1;
        private const uint DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2;

        [StructLayout(LayoutKind.Sequential)]
        private struct LUID
        {
            public uint LowPart;
            public int HighPart;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct DISPLAYCONFIG_PATH_SOURCE_INFO
        {
            public LUID adapterId;
            public uint id;
            public uint modeInfoIdx;
            public uint statusFlags;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct DISPLAYCONFIG_PATH_TARGET_INFO
        {
            public LUID adapterId;
            public uint id;
            public uint modeInfoIdx;
            public uint outputTechnology;
            public uint rotation;
            public uint scaling;
            public uint refreshRateNumerator;
            public uint refreshRateDenominator;
            public uint scanLineOrdering;
            public int targetAvailable;
            public uint statusFlags;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct DISPLAYCONFIG_PATH_INFO
        {
            public DISPLAYCONFIG_PATH_SOURCE_INFO sourceInfo;
            public DISPLAYCONFIG_PATH_TARGET_INFO targetInfo;
            public uint flags;
        }

        // 64 bytes: header (16) + 48-byte union (target / source / desktop-image mode); contents are not used
        [StructLayout(LayoutKind.Sequential)]
        private struct DISPLAYCONFIG_MODE_INFO
        {
            public uint infoType;
            public uint id;
            public LUID adapterId;
            public ulong u0, u1, u2, u3, u4, u5;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct DISPLAYCONFIG_DEVICE_INFO_HEADER
        {
            public uint type;
            public uint size;
            public LUID adapterId;
            public uint id;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct DISPLAYCONFIG_SOURCE_DEVICE_NAME
        {
            public DISPLAYCONFIG_DEVICE_INFO_HEADER header;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
            public string viewGdiDeviceName;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct DISPLAYCONFIG_TARGET_DEVICE_NAME
        {
            public DISPLAYCONFIG_DEVICE_INFO_HEADER header;
            public uint flags;
            public uint outputTechnology;
            public ushort edidManufactureId;
            public ushort edidProductCodeId;
            public uint connectorInstance;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
            public string monitorFriendlyDeviceName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
            public string monitorDevicePath;
        }

        [DllImport("user32.dll")]
        private static extern int GetDisplayConfigBufferSizes(uint flags, out uint numPathArrayElements, out uint numModeInfoArrayElements);

        [DllImport("user32.dll")]
        private static extern int QueryDisplayConfig(
            uint flags,
            ref uint numPathArrayElements,
            [Out] DISPLAYCONFIG_PATH_INFO[] pathArray,
            ref uint numModeInfoArrayElements,
            [Out] DISPLAYCONFIG_MODE_INFO[] modeInfoArray,
            IntPtr currentTopologyId);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int DisplayConfigGetDeviceInfo(ref DISPLAYCONFIG_SOURCE_DEVICE_NAME requestPacket);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern int DisplayConfigGetDeviceInfo(ref DISPLAYCONFIG_TARGET_DEVICE_NAME requestPacket);

        // Monitors currently showing the desktop, with what is needed to pick one in a game profile
        public static List<ActiveMonitor> GetActiveMonitors()
        {
            var result = new List<ActiveMonitor>();
            try
            {
                if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, out uint numPaths, out uint numModes) != 0) return result;
                var paths = new DISPLAYCONFIG_PATH_INFO[numPaths];
                var modes = new DISPLAYCONFIG_MODE_INFO[numModes];
                if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ref numPaths, paths, ref numModes, modes, IntPtr.Zero) != 0) return result;

                var attached = GammaEngine.GetAttachedDisplays();
                var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

                for (int i = 0; i < numPaths; i++)
                {
                    var p = paths[i];

                    var src = new DISPLAYCONFIG_SOURCE_DEVICE_NAME();
                    src.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME;
                    src.header.size = (uint)Marshal.SizeOf<DISPLAYCONFIG_SOURCE_DEVICE_NAME>();
                    src.header.adapterId = p.sourceInfo.adapterId;
                    src.header.id = p.sourceInfo.id;
                    if (DisplayConfigGetDeviceInfo(ref src) != 0 || string.IsNullOrEmpty(src.viewGdiDeviceName)) continue;
                    // Clone / duplicate mode: one GDI source drives several monitors; keep the first
                    if (!seen.Add(src.viewGdiDeviceName)) continue;

                    var tgt = new DISPLAYCONFIG_TARGET_DEVICE_NAME();
                    tgt.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME;
                    tgt.header.size = (uint)Marshal.SizeOf<DISPLAYCONFIG_TARGET_DEVICE_NAME>();
                    tgt.header.adapterId = p.targetInfo.adapterId;
                    tgt.header.id = p.targetInfo.id;
                    bool hasTarget = DisplayConfigGetDeviceInfo(ref tgt) == 0;

                    bool isPrimary = false;
                    string adapter = "";
                    foreach (var d in attached)
                    {
                        if (string.Equals(d.Name, src.viewGdiDeviceName, StringComparison.OrdinalIgnoreCase))
                        {
                            isPrimary = d.IsPrimary;
                            adapter = d.AdapterName;
                            break;
                        }
                    }

                    uint tech = hasTarget ? tgt.outputTechnology : p.targetInfo.outputTechnology;
                    string friendly = hasTarget ? (tgt.monitorFriendlyDeviceName ?? "") : "";
                    if (string.IsNullOrWhiteSpace(friendly))
                    {
                        friendly = IsInternalConnection(tech) ? "Built-in display" : "Generic monitor";
                    }

                    result.Add(new ActiveMonitor
                    {
                        GdiName = src.viewGdiDeviceName,
                        DevicePath = hasTarget ? (tgt.monitorDevicePath ?? "") : "",
                        FriendlyName = friendly,
                        Connection = DescribeConnection(tech),
                        AdapterName = adapter,
                        IsPrimary = isPrimary
                    });
                }
            }
            catch (Exception ex)
            {
                Program.Log($"MonitorEngine.GetActiveMonitors failed: {ex.Message}");
            }

            // Primary first, then by GDI index, so the list order is stable
            result.Sort((a, b) =>
            {
                if (a.IsPrimary != b.IsPrimary) return a.IsPrimary ? -1 : 1;
                return string.Compare(a.GdiName, b.GdiName, StringComparison.OrdinalIgnoreCase);
            });
            return result;
        }

        private static bool IsInternalConnection(uint tech) =>
            tech == 0x80000000 || tech == 6 || tech == 11 || tech == 13; // INTERNAL, LVDS, eDP, UDI embedded

        private static string DescribeConnection(uint tech) => tech switch
        {
            0 => "VGA",
            4 => "DVI",
            5 => "HDMI",
            10 => "DisplayPort",
            12 => "UDI",
            15 => "Miracast",
            16 or 17 => "Virtual",
            _ when IsInternalConnection(tech) => "Laptop panel",
            _ => "Other"
        };

        // GDI device name (e.g. "\\.\DISPLAY2") of the monitor saved in the profile.
        // Matches the stable device path, then the friendly name (monitor moved to another port), else the primary monitor.
        public static string? ResolveProfileDisplay(GUI.Config.GameProfile prof)
        {
            try
            {
                var monitors = GetActiveMonitors();
                if (!string.IsNullOrEmpty(prof.DisplayDevicePath))
                {
                    foreach (var m in monitors)
                    {
                        if (string.Equals(m.DevicePath, prof.DisplayDevicePath, StringComparison.OrdinalIgnoreCase)) return m.GdiName;
                    }

                    ActiveMonitor? byName = null;
                    int nameMatches = 0;
                    foreach (var m in monitors)
                    {
                        if (!string.IsNullOrEmpty(prof.DisplayName) && string.Equals(m.FriendlyName, prof.DisplayName, StringComparison.OrdinalIgnoreCase))
                        {
                            byName = m;
                            nameMatches++;
                        }
                    }
                    if (nameMatches == 1) return byName!.GdiName;

                    Program.Log($"Profile monitor '{prof.DisplayName}' is not connected -> using primary monitor.");
                }

                return GetPrimaryDisplayName();
            }
            catch
            {
                return GetPrimaryDisplayName();
            }
        }

        public static string? GetPrimaryDisplayName()
        {
            return GetDisplayName(MonitorFromPoint(new POINT { X = 0, Y = 0 }, MONITOR_DEFAULTTOPRIMARY));
        }

        private static string? GetDisplayName(IntPtr hMonitor)
        {
            if (hMonitor == IntPtr.Zero) return null;
            var mi = new MONITORINFOEXW { cbSize = Marshal.SizeOf<MONITORINFOEXW>() };
            return GetMonitorInfoW(hMonitor, ref mi) && !string.IsNullOrEmpty(mi.szDevice) ? mi.szDevice : null;
        }

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

            // Determine primary monitor
            const uint EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001;
            var primaryMonitorInterfaceIds = new List<string>();
            var primaryMonitorPnpIds = new List<string>();

            try
            {
                for (uint i = 0; ; i++)
                {
                    var adapterDev = new DISPLAY_DEVICEW();
                    adapterDev.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                    if (!EnumDisplayDevicesW(null, i, ref adapterDev, 0)) break;

                    bool isPrimary = (adapterDev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) != 0;
                    if (isPrimary)
                    {
                        for (uint j = 0; ; j++)
                        {
                            // 1. Query with EDD_GET_DEVICE_INTERFACE_NAME to get unique hardware UID interface path
                            var monDevIface = new DISPLAY_DEVICEW();
                            monDevIface.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                            if (EnumDisplayDevicesW(adapterDev.DeviceName, j, ref monDevIface, EDD_GET_DEVICE_INTERFACE_NAME))
                            {
                                if (!string.IsNullOrEmpty(monDevIface.DeviceID))
                                {
                                    primaryMonitorInterfaceIds.Add(monDevIface.DeviceID);
                                }
                            }

                            // 2. Query standard device ID as fallback
                            var monDevStd = new DISPLAY_DEVICEW();
                            monDevStd.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                            if (!EnumDisplayDevicesW(adapterDev.DeviceName, j, ref monDevStd, 0)) break;

                            if (!string.IsNullOrEmpty(monDevStd.DeviceID))
                            {
                                primaryMonitorPnpIds.Add(monDevStd.DeviceID);
                            }
                        }
                    }
                }
            }
            catch { }

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

            return list;
        }

        private static bool _cachedHasDisabled = false;
        private static long _lastDisabledCheckTick = 0;
        private static readonly object _monitorCheckLock = new object();

        public static void InvalidateMonitorCache()
        {
            lock (_monitorCheckLock)
            {
                _lastDisabledCheckTick = 0;
            }
            GammaEngine.InvalidateDisplayCache();
        }

        public static bool HasAnyDisabledMonitors()
        {
            lock (_monitorCheckLock)
            {
                if (_disabledInSession.Count > 0)
                {
                    return true;
                }

                long now = Environment.TickCount64;
                if (_lastDisabledCheckTick != 0 && (now - _lastDisabledCheckTick < 60000))
                {
                    return _cachedHasDisabled;
                }
                _lastDisabledCheckTick = now;
                try
                {
                    var list = EnumerateMonitors();
                    bool hasDisabled = false;
                    foreach (var m in list)
                    {
                        if (!m.Enabled)
                        {
                            hasDisabled = true;
                            break;
                        }
                    }
                    _cachedHasDisabled = hasDisabled;
                    return _cachedHasDisabled;
                }
                catch
                {
                    return false;
                }
            }
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
