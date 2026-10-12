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

        public record ResolutionPreset(string Label, int Width, int Height, string AspectRatio);

        public static readonly List<ResolutionPreset> PopularPresets = new()
        {
            new("1920 x 1080 (Native 16:9)", 1920, 1080, "16:9"),
            new("2560 x 1440 (2K 16:9)", 2560, 1440, "16:9"),
            new("1440 x 1080 (Valorant/CS Stretched 4:3)", 1440, 1080, "4:3"),
            new("1280 x 960 (CS2 Classic 4:3)", 1280, 960, "4:3"),
            new("1024 x 768 (Low-res 4:3)", 1024, 768, "4:3"),
            new("1280 x 1024 (5:4 Stretched)", 1280, 1024, "5:4"),
            new("1080 x 1080 (1:1 Stretched)", 1080, 1080, "1:1"),
            new("1600 x 1080 (Stretch-lite)", 1600, 1080, "40:27"),
            new("1620 x 1080 (3:2 Stretched)", 1620, 1080, "3:2"),
            new("1920 x 1440 (High 4:3)", 1920, 1440, "4:3"),
            new("1280 x 1080 (Narrow)", 1280, 1080, "32:27"),
            new("1350 x 1080 (Custom 5:4ish)", 1350, 1080, "5:4"),
            new("800 x 1080 (Ultra Stretched)", 800, 1080, "20:27")
        };

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

        public static HashSet<(int Width, int Height)> GetSupportedResolutions()
        {
            var result = new HashSet<(int Width, int Height)>();
            var devmode = new DEVMODE();
            devmode.dmSize = (ushort)Marshal.SizeOf<DEVMODE>();
            int i = 0;
            while (EnumDisplaySettings(null, i, ref devmode))
            {
                result.Add(((int)devmode.dmPelsWidth, (int)devmode.dmPelsHeight));
                i++;
            }
            return result;
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

            int res = ChangeDisplaySettings(ref devmode, CDS_UPDATEREGISTRY | CDS_NORESET);
            if (res != DISP_CHANGE_SUCCESSFUL && refreshRate > 0)
            {
                // Retry without forcing refresh rate
                devmode.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT;
                res = ChangeDisplaySettings(ref devmode, CDS_UPDATEREGISTRY | CDS_NORESET);
            }

            if (res != DISP_CHANGE_SUCCESSFUL)
            {
                return (false, $"Resolution change failed (Error code {res}). Resolution might not be registered in GPU driver.");
            }

            res = ChangeDisplaySettings(IntPtr.Zero, CDS_RESET);
            if (res == DISP_CHANGE_SUCCESSFUL)
            {
                _hasModifiedResolution = true;
                int actualHz = refreshRate > 0 ? refreshRate : origFreq;
                return (true, $"Applied {width} x {height} @ {actualHz}Hz");
            }

            // Rollback
            Rollback(origWidth, origHeight, origFreq);
            return (false, $"Apply failed (Error code {res}). Rolled back to {origWidth} x {origHeight}.");
        }

        private static void Rollback(int width, int height, int freq)
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
            int res = ChangeDisplaySettings(ref devmode, CDS_UPDATEREGISTRY | CDS_NORESET);
            if (res == DISP_CHANGE_SUCCESSFUL)
            {
                ChangeDisplaySettings(IntPtr.Zero, CDS_RESET);
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

        public static void OpenGpuControlPanel()
        {
            // 1. NVIDIA Control Panel
            string nvidiaPath = @"C:\Program Files\NVIDIA Corporation\Control Panel Client\nvcplui.exe";
            if (File.Exists(nvidiaPath))
            {
                Process.Start(new ProcessStartInfo { FileName = nvidiaPath, UseShellExecute = true });
                return;
            }

            // 2. AMD Software
            string[] amdPaths = {
                @"C:\Program Files\AMD\CNext\CNext\RadeonSoftware.exe",
                @"C:\Program Files (x86)\AMD\CNext\CNext\RadeonSoftware.exe"
            };
            foreach (var p in amdPaths)
            {
                if (File.Exists(p))
                {
                    Process.Start(new ProcessStartInfo { FileName = p, UseShellExecute = true });
                    return;
                }
            }

            // 3. Fallback: launch windows display settings
            try
            {
                Process.Start(new ProcessStartInfo { FileName = "ms-settings:display", UseShellExecute = true });
            }
            catch { }
        }
    }
}
