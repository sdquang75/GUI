using System;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    public class WindowsDwmEngine : IVibranceEngine
    {
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

        // Names the engine after the GPU driving the primary monitor (Intel / AMD); anything else is plain "Windows DWM"
        private void DetectGpuName()
        {
            string adapter = "";
            foreach (var d in DisplayTopology.GetDisplays())
            {
                if (d.IsPrimary)
                {
                    adapter = d.AdapterName;
                    break;
                }
            }

            if (adapter.Contains("Intel", StringComparison.OrdinalIgnoreCase))
            {
                EngineName = "Intel (DWM)";
            }
            else if (adapter.Contains("AMD", StringComparison.OrdinalIgnoreCase) || adapter.Contains("Radeon", StringComparison.OrdinalIgnoreCase))
            {
                EngineName = "AMD (DWM)";
            }
            else
            {
                EngineName = "Windows DWM";
            }
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
