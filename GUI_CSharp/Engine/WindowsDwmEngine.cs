using System;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    public class WindowsDwmEngine : IVibranceEngine
    {
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

        private bool _isDisposed = false;
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
                if (DwmShaderColorEngine.EnsureInitialized())
                {
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
            if (_isDisposed) return false;

            if (!DwmShaderColorEngine.EnsureInitialized())
            {
                IsAvailable = false;
                return false;
            }
            IsAvailable = true;

            level = Math.Clamp(level, 0, 100);
            _currentLevel = level;

            return DwmShaderColorEngine.SetVibrance(level);
        }

        public void Restore()
        {
            if (!_isDisposed)
            {
                SetLevel(DesktopLevel);
            }
        }

        public void ResetToDefault()
        {
            try
            {
                DwmShaderColorEngine.ResetVibrance();
                _currentLevel = 50;
            }
            catch { }
        }

        public void Dispose()
        {
            if (_isDisposed) return;
            try
            {
                ResetToDefault();
            }
            catch { }
            finally
            {
                _isDisposed = true;
            }
        }
    }
}
