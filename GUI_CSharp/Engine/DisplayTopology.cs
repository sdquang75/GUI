using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace GUI.Engine
{
    // One monitor currently showing the desktop
    public sealed class DisplayInfo
    {
        public string GdiName { get; init; } = "";        // "\\.\DISPLAY2", what ramps / NVAPI names refer to
        public string AdapterName { get; init; } = "";    // GPU driving it (iGPU vs NVIDIA on laptops)
        public bool IsPrimary { get; init; }
    }

    // Which monitors are attached and which one is primary. Everything that writes a LUT or vibrance asks this class.
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

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool EnumDisplayDevicesW(
            string? lpDevice,
            uint iDevNum,
            ref DISPLAY_DEVICEW lpDisplayDevice,
            uint dwFlags);

        [StructLayout(LayoutKind.Sequential)]
        private struct RECT
        {
            public int Left, Top, Right, Bottom;
        }

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

        private const uint MONITOR_DEFAULTTONULL = 0x00000000;

        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        private static extern bool GetMonitorInfoW(IntPtr hMonitor, ref MONITORINFOEXW lpmi);

        private sealed class Snapshot
        {
            public DisplayInfo[] Displays = Array.Empty<DisplayInfo>();
            public long Tick;
        }

        // Ramp writes and GSI packets ask for this constantly, so it is cached. Invalidated on WM_DISPLAYCHANGE: the
        // next call re-reads synchronously. When only the TTL safety net expires, the old answer is returned and
        // refreshed in the background, so a black flash packet never pays for the rescan.
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

        // GDI device name of the monitor `hwnd` is mostly on (what "Affect game monitor only" targets). null when the
        // window is gone or its monitor is not in the attached list, so the caller picks its own fallback.
        public static string? GetMonitorOfWindow(IntPtr hwnd)
        {
            if (hwnd == IntPtr.Zero) return null;
            try
            {
                IntPtr hMonitor = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONULL);
                if (hMonitor == IntPtr.Zero) return null;

                var info = new MONITORINFOEXW { cbSize = Marshal.SizeOf<MONITORINFOEXW>() };
                if (!GetMonitorInfoW(hMonitor, ref info) || string.IsNullOrEmpty(info.szDevice)) return null;

                foreach (var d in GetDisplays())
                {
                    if (string.Equals(d.GdiName, info.szDevice, StringComparison.OrdinalIgnoreCase)) return d.GdiName;
                }
            }
            catch { }
            return null;
        }

        // Every display attached to the desktop (these names are what SetDeviceGammaRamp / NVAPI use)
        private static DisplayInfo[] Enumerate()
        {
            var result = new List<DisplayInfo>();
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
                        result.Add(new DisplayInfo
                        {
                            GdiName = dev.DeviceName,
                            AdapterName = dev.DeviceString ?? "",
                            IsPrimary = (dev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) != 0
                        });
                    }
                }
            }
            catch { }

            result.Sort((a, b) =>
            {
                if (a.IsPrimary != b.IsPrimary) return a.IsPrimary ? -1 : 1;
                return string.Compare(a.GdiName, b.GdiName, StringComparison.OrdinalIgnoreCase);
            });
            return result.ToArray();
        }

        // Logs which GPU drives each monitor whenever the layout changes (HDMI plug/unplug, MUX switch...)
        private static void LogIfChanged(DisplayInfo[] displays)
        {
            if (!Program.EnableLogging) return;
            var parts = new List<string>(displays.Length);
            foreach (var d in displays)
            {
                parts.Add($"{d.GdiName}{(d.IsPrimary ? " (primary)" : "")} on {d.AdapterName}");
            }
            string topology = string.Join("; ", parts);
            if (topology == _lastLoggedTopology) return;
            _lastLoggedTopology = topology;
            Program.Log($"Display topology: {(topology.Length > 0 ? topology : "none")}");
        }
    }
}
