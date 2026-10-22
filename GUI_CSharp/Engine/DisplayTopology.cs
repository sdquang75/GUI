using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace GUI.Engine
{
    // One monitor currently showing the desktop
    public sealed class DisplayInfo
    {
        public string GdiName { get; init; } = "";        // "\\.\DISPLAY2" (can change across reboots / replugs)
        public string DevicePath { get; init; } = "";     // stable per monitor + connector, saved in the profile
        public string FriendlyName { get; init; } = "";   // EDID name, e.g. "DELL S2721DGF"
        public string Connection { get; init; } = "";     // HDMI / DisplayPort / Laptop panel ...
        public string AdapterName { get; init; } = "";    // GPU driving it (iGPU vs NVIDIA on laptops)
        public bool IsPrimary { get; init; }
    }

    // Single source of truth for "which monitors exist": GDI names (what ramps are written to), DisplayConfig
    // details (EDID name, connector, stable device path) and the primary monitor. Everything else asks this class.
    public static class DisplayTopology
    {
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

        private const int DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001;
        private const int DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004;
        private const int DISPLAY_DEVICE_MIRRORING_DRIVER = 0x00000008;
        private const uint EDD_GET_DEVICE_INTERFACE_NAME = 0x00000001;

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool EnumDisplayDevicesW(
            string? lpDevice,
            uint iDevNum,
            ref DISPLAY_DEVICEW lpDisplayDevice,
            uint dwFlags);

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

        private sealed class Snapshot
        {
            public DisplayInfo[] Displays = Array.Empty<DisplayInfo>();
            public long Tick;
        }

        // Enumeration costs a few ms (DisplayConfig); ramp writes and GSI packets ask for it constantly, so cache it.
        // Invalidated on WM_DISPLAYCHANGE / monitor enable-disable (MonitorEngine.InvalidateMonitorCache): the next call
        // re-reads synchronously. When only the TTL safety net expires, the old answer is returned and refreshed in the
        // background, so a black flash packet never pays for the rescan.
        private const long CacheTtlMs = 30000;
        private static volatile Snapshot? _snapshot;
        private static int _generation;
        private static int _refreshPending;
        private static string _lastLoggedTopology = "";

        public static void Invalidate()
        {
            Interlocked.Increment(ref _generation);
            _snapshot = null;
        }

        // Monitors currently showing the desktop: primary first, then by GDI index
        public static IReadOnlyList<DisplayInfo> GetDisplays()
        {
            var snap = _snapshot;
            if (snap != null)
            {
                if (Environment.TickCount64 - snap.Tick >= CacheTtlMs && Interlocked.CompareExchange(ref _refreshPending, 1, 0) == 0)
                {
                    ThreadPool.QueueUserWorkItem(_ => RefreshInBackground());
                }
                return snap.Displays;
            }

            return Refresh(Volatile.Read(ref _generation));
        }

        private static DisplayInfo[] Refresh(int generation)
        {
            var displays = Enumerate();
            // An Invalidate() during the scan wins: the data may predate the change it announced
            if (generation == Volatile.Read(ref _generation))
            {
                _snapshot = new Snapshot { Displays = displays, Tick = Environment.TickCount64 };
            }
            LogIfChanged(displays);
            return displays;
        }

        private static void RefreshInBackground()
        {
            try
            {
                Refresh(Volatile.Read(ref _generation));
            }
            catch { }
            finally
            {
                Volatile.Write(ref _refreshPending, 0);
            }
        }

        // GDI device name (e.g. "\\.\DISPLAY1") of the primary monitor, null when nothing is attached
        public static string? GetPrimaryName()
        {
            var displays = GetDisplays();
            foreach (var d in displays)
            {
                if (d.IsPrimary) return d.GdiName;
            }
            return displays.Count > 0 ? displays[0].GdiName : null;
        }

        // GDI device name of the monitor saved in the profile.
        // Matches the stable device path, then the friendly name (monitor moved to another port), else the primary monitor.
        public static string? ResolveProfileDisplay(GUI.Config.GameProfile prof)
        {
            try
            {
                if (!string.IsNullOrEmpty(prof.DisplayDevicePath))
                {
                    var displays = GetDisplays();
                    foreach (var d in displays)
                    {
                        if (string.Equals(d.DevicePath, prof.DisplayDevicePath, StringComparison.OrdinalIgnoreCase)) return d.GdiName;
                    }

                    DisplayInfo? byName = null;
                    int nameMatches = 0;
                    foreach (var d in displays)
                    {
                        if (!string.IsNullOrEmpty(prof.DisplayName) && string.Equals(d.FriendlyName, prof.DisplayName, StringComparison.OrdinalIgnoreCase))
                        {
                            byName = d;
                            nameMatches++;
                        }
                    }
                    if (nameMatches == 1) return byName!.GdiName;

                    Program.Log($"Profile monitor '{prof.DisplayName}' is not connected -> using primary monitor.");
                }
            }
            catch { }

            return GetPrimaryName();
        }

        // Device IDs of the monitors under the primary adapter, used by MonitorEngine to tell which devnode is the primary one:
        // interface paths (unique per monitor, resolve two identical models) and plain PnP IDs (model fallback).
        public static void GetPrimaryMonitorIds(out List<string> interfaceIds, out List<string> pnpIds)
        {
            interfaceIds = new List<string>();
            pnpIds = new List<string>();

            try
            {
                for (uint i = 0; ; i++)
                {
                    var adapterDev = new DISPLAY_DEVICEW();
                    adapterDev.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                    if (!EnumDisplayDevicesW(null, i, ref adapterDev, 0)) break;

                    if ((adapterDev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) == 0) continue;

                    for (uint j = 0; ; j++)
                    {
                        // 1. Query with EDD_GET_DEVICE_INTERFACE_NAME to get unique hardware UID interface path
                        var monDevIface = new DISPLAY_DEVICEW();
                        monDevIface.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                        if (EnumDisplayDevicesW(adapterDev.DeviceName, j, ref monDevIface, EDD_GET_DEVICE_INTERFACE_NAME)
                            && !string.IsNullOrEmpty(monDevIface.DeviceID))
                        {
                            interfaceIds.Add(monDevIface.DeviceID);
                        }

                        // 2. Query standard device ID as fallback
                        var monDevStd = new DISPLAY_DEVICEW();
                        monDevStd.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                        if (!EnumDisplayDevicesW(adapterDev.DeviceName, j, ref monDevStd, 0)) break;

                        if (!string.IsNullOrEmpty(monDevStd.DeviceID))
                        {
                            pnpIds.Add(monDevStd.DeviceID);
                        }
                    }
                }
            }
            catch { }
        }

        private static DisplayInfo[] Enumerate()
        {
            // 1. GDI: every display attached to the desktop (these names are what SetDeviceGammaRamp / NVAPI use)
            var gdi = new List<(string Name, string Adapter, bool Primary)>();
            try
            {
                for (uint i = 0; ; i++)
                {
                    var dev = new DISPLAY_DEVICEW();
                    dev.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                    if (!EnumDisplayDevicesW(null, i, ref dev, 0)) break;

                    bool attached = (dev.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP) != 0;
                    bool mirror = (dev.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER) != 0;
                    if (attached && !mirror && !string.IsNullOrEmpty(dev.DeviceName))
                    {
                        gdi.Add((dev.DeviceName, dev.DeviceString ?? "", (dev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) != 0));
                    }
                }
            }
            catch { }

            // 2. DisplayConfig: EDID name, connector and stable device path for each GDI source
            var details = new Dictionary<string, (string Path, string Friendly, uint Tech)>(StringComparer.OrdinalIgnoreCase);
            try
            {
                if (GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, out uint numPaths, out uint numModes) == 0)
                {
                    var paths = new DISPLAYCONFIG_PATH_INFO[numPaths];
                    var modes = new DISPLAYCONFIG_MODE_INFO[numModes];
                    if (QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ref numPaths, paths, ref numModes, modes, IntPtr.Zero) == 0)
                    {
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
                            if (details.ContainsKey(src.viewGdiDeviceName)) continue;

                            var tgt = new DISPLAYCONFIG_TARGET_DEVICE_NAME();
                            tgt.header.type = DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME;
                            tgt.header.size = (uint)Marshal.SizeOf<DISPLAYCONFIG_TARGET_DEVICE_NAME>();
                            tgt.header.adapterId = p.targetInfo.adapterId;
                            tgt.header.id = p.targetInfo.id;
                            bool hasTarget = DisplayConfigGetDeviceInfo(ref tgt) == 0;

                            details[src.viewGdiDeviceName] = (
                                hasTarget ? (tgt.monitorDevicePath ?? "") : "",
                                hasTarget ? (tgt.monitorFriendlyDeviceName ?? "") : "",
                                hasTarget ? tgt.outputTechnology : p.targetInfo.outputTechnology);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Program.Log($"DisplayTopology: DisplayConfig query failed: {ex.Message}");
            }

            var result = new List<DisplayInfo>(gdi.Count);
            foreach (var g in gdi)
            {
                details.TryGetValue(g.Name, out var det);
                string friendly = det.Friendly;
                if (string.IsNullOrWhiteSpace(friendly))
                {
                    friendly = IsInternalConnection(det.Tech) ? "Built-in display" : "Generic monitor";
                }

                result.Add(new DisplayInfo
                {
                    GdiName = g.Name,
                    DevicePath = det.Path ?? "",
                    FriendlyName = friendly,
                    Connection = DescribeConnection(det.Tech),
                    AdapterName = g.Adapter,
                    IsPrimary = g.Primary
                });
            }

            result.Sort((a, b) =>
            {
                if (a.IsPrimary != b.IsPrimary) return a.IsPrimary ? -1 : 1;
                return string.Compare(a.GdiName, b.GdiName, StringComparison.OrdinalIgnoreCase);
            });
            return result.ToArray();
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

        // Logs which GPU drives each monitor whenever the layout changes (HDMI plug/unplug, MUX switch...)
        private static void LogIfChanged(DisplayInfo[] displays)
        {
            var parts = new List<string>(displays.Length);
            foreach (var d in displays)
            {
                parts.Add($"{d.GdiName}{(d.IsPrimary ? " (primary)" : "")} {d.FriendlyName} · {d.Connection} on {d.AdapterName}");
            }
            string topology = string.Join("; ", parts);
            if (topology == _lastLoggedTopology) return;
            _lastLoggedTopology = topology;
            Program.Log($"Display topology: {(topology.Length > 0 ? topology : "none")}");
        }
    }
}
