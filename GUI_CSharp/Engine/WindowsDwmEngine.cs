using System;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    public class WindowsDwmEngine : IVibranceEngine
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct MAGCOLOREFFECT
        {
            [MarshalAs(UnmanagedType.ByValArray, SizeConst = 25)]
            public float[] transform;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
        private struct DISPLAY_DEVICE
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

        [DllImport("user32.dll", CharSet = CharSet.Ansi)]
        private static extern bool EnumDisplayDevices(string? lpDevice, uint iDevNum, ref DISPLAY_DEVICE lpDisplayDevice, uint dwFlags);

        [DllImport("Magnification.dll", CallingConvention = CallingConvention.Winapi)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MagInitialize();

        [DllImport("Magnification.dll", CallingConvention = CallingConvention.Winapi)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MagUninitialize();

        [DllImport("Magnification.dll", CallingConvention = CallingConvention.Winapi)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MagSetFullscreenColorEffect(ref MAGCOLOREFFECT pEffect);

        private bool _isInitialized = false;
        private int _currentLevel = 50;

        public bool IsAvailable { get; private set; } = false;
        public string EngineName { get; private set; } = "Intel / DWM";
        public int DesktopLevel { get; set; } = 50;

        public WindowsDwmEngine()
        {
            DetectGpuName();
            Initialize();
        }

        private void DetectGpuName()
        {
            try
            {
                var dd = new DISPLAY_DEVICE();
                dd.cb = Marshal.SizeOf(dd);
                for (uint i = 0; EnumDisplayDevices(null, i, ref dd, 0); i++)
                {
                    if ((dd.StateFlags & 0x4) != 0 || i == 0) // Primary or first display
                    {
                        string str = dd.DeviceString ?? "";
                        if (str.Contains("Intel", StringComparison.OrdinalIgnoreCase))
                        {
                            EngineName = "Intel (DWM)";
                            return;
                        }
                        if (str.Contains("AMD", StringComparison.OrdinalIgnoreCase) || str.Contains("Radeon", StringComparison.OrdinalIgnoreCase))
                        {
                            EngineName = "AMD (DWM)";
                            return;
                        }
                    }
                }
            }
            catch { }
            EngineName = "Windows DWM";
        }

        private void Initialize()
        {
            try
            {
                if (MagInitialize())
                {
                    _isInitialized = true;
                    IsAvailable = true;
                }
            }
            catch (Exception ex)
            {
                Program.Log($"WindowsDwmEngine.Initialize failed: {ex.Message}");
                IsAvailable = false;
            }
        }

        public int GetLevel()
        {
            return _currentLevel;
        }

        public bool SetLevel(int level)
        {
            if (!_isInitialized && !MagInitialize())
            {
                IsAvailable = false;
                return false;
            }
            _isInitialized = true;

            level = Math.Clamp(level, 0, 100);
            _currentLevel = level;

            try
            {
                MAGCOLOREFFECT effect = new MAGCOLOREFFECT();

                if (level == 50)
                {
                    // Identity matrix
                    effect.transform = new float[25]
                    {
                        1f, 0f, 0f, 0f, 0f,
                        0f, 1f, 0f, 0f, 0f,
                        0f, 0f, 1f, 0f, 0f,
                        0f, 0f, 0f, 1f, 0f,
                        0f, 0f, 0f, 0f, 1f
                    };
                }
                else
                {
                    // Saturation factor: 50% = 1.0, 100% = 2.0, 0% = 0.0
                    float s = level / 50.0f;
                    float invS = 1.0f - s;

                    // ITU-R BT.709 standard luminance weights
                    const float lr = 0.2126f;
                    const float lg = 0.7152f;
                    const float lb = 0.0722f;

                    float rL = invS * lr;
                    float gL = invS * lg;
                    float bL = invS * lb;

                    effect.transform = new float[25]
                    {
                        rL + s, rL,     rL,     0f, 0f,
                        gL,     gL + s, gL,     0f, 0f,
                        bL,     bL,     bL + s, 0f, 0f,
                        0f,     0f,     0f,     1f, 0f,
                        0f,     0f,     0f,     0f, 1f
                    };
                }

                return MagSetFullscreenColorEffect(ref effect);
            }
            catch (Exception ex)
            {
                Program.Log($"WindowsDwmEngine.SetLevel failed: {ex.Message}");
                return false;
            }
        }

        public void Restore()
        {
            SetLevel(DesktopLevel);
        }

        public void Dispose()
        {
            try
            {
                if (_isInitialized)
                {
                    // Reset to identity
                    MAGCOLOREFFECT id = new MAGCOLOREFFECT
                    {
                        transform = new float[25]
                        {
                            1f, 0f, 0f, 0f, 0f,
                            0f, 1f, 0f, 0f, 0f,
                            0f, 0f, 1f, 0f, 0f,
                            0f, 0f, 0f, 1f, 0f,
                            0f, 0f, 0f, 0f, 1f
                        }
                    };
                    MagSetFullscreenColorEffect(ref id);
                    MagUninitialize();
                    _isInitialized = false;
                }
            }
            catch { }
        }
    }
}
