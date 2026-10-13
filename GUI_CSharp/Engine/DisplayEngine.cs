using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    public static class DisplayEngine
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
        public struct DEVMODE
        {
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
            public string dmDeviceName;
            public ushort dmSpecVersion;
            public ushort dmDriverVersion;
            public ushort dmSize;
            public ushort dmDriverExtra;
            public uint dmFields;
            public int dmPositionX;
            public int dmPositionY;
            public uint dmDisplayOrientation;
            public uint dmDisplayFixedOutput;
            public short dmColor;
            public short dmDuplex;
            public short dmYResolution;
            public short dmTTOption;
            public short dmCollate;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)]
            public string dmFormName;
            public ushort dmLogPixels;
            public uint dmBitsPerPel;
            public uint dmPelsWidth;
            public uint dmPelsHeight;
            public uint dmDisplayFlags;
            public uint dmDisplayFrequency;
            public uint dmICMMethod;
            public uint dmICMIntent;
            public uint dmMediaType;
            public uint dmDitherType;
            public uint dmReserved1;
            public uint dmReserved2;
            public uint dmPanningWidth;
            public uint dmPanningHeight;
        }

        private const int ENUM_CURRENT_SETTINGS = -1;
        private const uint DM_PELSWIDTH = 0x00080000;
        private const uint DM_PELSHEIGHT = 0x00100000;
        private const uint DM_DISPLAYFREQUENCY = 0x00400000;

        private const int CDS_UPDATEREGISTRY = 0x00000001;
        private const int CDS_NORESET = 0x10000000;
        private const int CDS_RESET = 0x40000000;
        private const int DISP_CHANGE_SUCCESSFUL = 0;

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        private static extern bool EnumDisplaySettings(string? lpszDeviceName, int iModeNum, ref DEVMODE lpDevMode);

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        private static extern int ChangeDisplaySettings(ref DEVMODE lpDevMode, int dwFlags);

        [DllImport("user32.dll", CharSet = CharSet.Auto)]
        private static extern int ChangeDisplaySettings(IntPtr lpDevMode, int dwFlags);

        private static (int Width, int Height, int Frequency)? _initialResolution;
        private static bool _hasModifiedResolution = false;

        public static void CaptureInitialResolution()
        {
            if (_initialResolution == null)
            {
                _initialResolution = GetCurrentResolution();
                if (_initialResolution.HasValue)
                {
                    Program.Log($"DisplayEngine: Captured initial resolution {_initialResolution.Value.Width}x{_initialResolution.Value.Height} @ {_initialResolution.Value.Frequency}Hz");
                }
            }
        }

        public static void RestoreDefaultResolution()
        {
            try
            {
                if (!_hasModifiedResolution || !_initialResolution.HasValue)
                {
                    return;
                }

                var init = _initialResolution.Value;
                var cur = GetCurrentResolution();
                if (!cur.HasValue || cur.Value.Width != init.Width || cur.Value.Height != init.Height || cur.Value.Frequency != init.Frequency)
                {
                    Program.Log($"DisplayEngine: Restoring default resolution {init.Width}x{init.Height} @ {init.Frequency}Hz on exit");
                    Rollback(init.Width, init.Height, init.Frequency);
                }
            }
            catch (Exception ex)
            {
                Program.Log($"DisplayEngine.RestoreDefaultResolution error: {ex.Message}");
            }
            finally
            {
                _hasModifiedResolution = false;
            }
        }

        public static (int Width, int Height, int Frequency)? GetCurrentResolution()
        {
            var devmode = new DEVMODE();
            devmode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
            if (EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref devmode))
            {
                return ((int)devmode.dmPelsWidth, (int)devmode.dmPelsHeight, (int)devmode.dmDisplayFrequency);
            }
            return null;
        }

        public static int GetMaxRefreshRate(int width, int height)
        {
            int best = 0;
            var devmode = new DEVMODE();
            devmode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
            int i = 0;
            while (EnumDisplaySettings(null, i, ref devmode))
            {
                if (devmode.dmPelsWidth == width && devmode.dmPelsHeight == height)
                {
                    if (devmode.dmDisplayFrequency > best)
                    {
                        best = (int)devmode.dmDisplayFrequency;
                    }
                }
                i++;
            }
            return best > 1 ? best : 0;
        }

        public static List<(int Width, int Height)> GetSupportedResolutions()
        {
            var set = new HashSet<(int Width, int Height)>();
            var devmode = new DEVMODE();
            devmode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
            int i = 0;
            while (EnumDisplaySettings(null, i, ref devmode))
            {
                int w = (int)devmode.dmPelsWidth;
                int h = (int)devmode.dmPelsHeight;
                if (w >= 800 && h >= 600)
                {
                    set.Add((w, h));
                }
                i++;
            }

            var list = new List<(int Width, int Height)>(set);
            list.Sort((a, b) =>
            {
                int cmpW = b.Width.CompareTo(a.Width);
                return cmpW != 0 ? cmpW : b.Height.CompareTo(a.Height);
            });
            return list;
        }

        public static (bool Success, string Message) SetResolution(int width, int height, int refreshRate = 0)
        {
            CaptureInitialResolution();

            var curMode = new DEVMODE();
            curMode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
            if (!EnumDisplaySettings(null, ENUM_CURRENT_SETTINGS, ref curMode))
            {
                return (false, "Could not read current display settings.");
            }

            int origWidth = (int)curMode.dmPelsWidth;
            int origHeight = (int)curMode.dmPelsHeight;
            int origFreq = (int)curMode.dmDisplayFrequency;

            if (refreshRate <= 0)
            {
                refreshRate = GetMaxRefreshRate(width, height);
            }

            var devmode = new DEVMODE();
            devmode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
            devmode.dmPelsWidth = (uint)width;
            devmode.dmPelsHeight = (uint)height;
            devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;

            if (refreshRate > 0)
            {
                devmode.dmDisplayFrequency = (uint)refreshRate;
                devmode.dmFields |= DM_DISPLAYFREQUENCY;
            }

            int res = ChangeDisplaySettings(ref devmode, 0);
            if (res != DISP_CHANGE_SUCCESSFUL && refreshRate > 0)
            {
                // Retry without forcing refresh rate
                devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
                res = ChangeDisplaySettings(ref devmode, 0);
            }

            if (res != DISP_CHANGE_SUCCESSFUL)
            {
                return (false, $"Resolution change failed (Error code {res}). Resolution might not be registered in GPU driver.");
            }

            _hasModifiedResolution = true;
            var curModeApplied = GetCurrentResolution();
            int actualHz = curModeApplied?.Frequency ?? (refreshRate > 0 ? refreshRate : origFreq);
            return (true, $"Applied {width} x {height} @ {actualHz}Hz");
        }

        private static void Rollback(int width, int height, int freq)
        {
            // Restore default from registry first (cleanest dynamic rollback)
            int res = ChangeDisplaySettings(IntPtr.Zero, 0);
            if (res != DISP_CHANGE_SUCCESSFUL)
            {
                var devmode = new DEVMODE();
                devmode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
                devmode.dmPelsWidth = (uint)width;
                devmode.dmPelsHeight = (uint)height;
                devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
                if (freq > 1)
                {
                    devmode.dmDisplayFrequency = (uint)freq;
                    devmode.dmFields |= DM_DISPLAYFREQUENCY;
                }
                ChangeDisplaySettings(ref devmode, 0);
            }
        }

        public static string GetAspectRatioLabel(int width, int height)
        {
            if (height <= 0 || width <= 0) return "Unknown";
            int gcd = Gcd(width, height);
            int w = width / gcd;
            int h = height / gcd;

            // Handle approximate aspect ratios
            double ratio = (double)width / height;
            if (Math.Abs(ratio - 16.0 / 9.0) < 0.02) return "16:9";
            if (Math.Abs(ratio - 4.0 / 3.0) < 0.02) return "4:3";
            if (Math.Abs(ratio - 16.0 / 10.0) < 0.02) return "16:10";
            if (Math.Abs(ratio - 5.0 / 4.0) < 0.02) return "5:4";
            if (Math.Abs(ratio - 1.0) < 0.02) return "1:1";
            if (Math.Abs(ratio - 21.0 / 9.0) < 0.05) return "21:9";
            if (Math.Abs(ratio - 32.0 / 9.0) < 0.05) return "32:9";

            return $"{w}:{h}";
        }

        private static int Gcd(int a, int b)
        {
            while (b != 0)
            {
                int t = b;
                b = a % b;
                a = t;
            }
            return a;
        }
    }
}
