using System;

namespace GUI.Engine
{
    // Vibrance through NVIDIA DVC when the NVIDIA driver controls the chosen monitor, otherwise through the DWM shader
    // (desktop-wide: Intel / AMD, or NVAPI missing). Same engine choice as 1.1.3, applied per monitor.
    public class UnifiedVibranceEngine : IVibranceEngine
    {
        private const int NeutralLevel = 50;

        private readonly NvApiEngine _nvApi;
        private WindowsDwmEngine? _dwm;
        private readonly object _lock = new();
        private bool _isDisposed = false;
        private bool _usingDwm;
        private int _desktopLevel = NeutralLevel;
        private int _lastLevel = NeutralLevel;

        public bool IsAvailable => _nvApi.IsAvailable || (GetDwm()?.IsAvailable ?? false);
        public string EngineName => _usingDwm ? (_dwm?.EngineName ?? "Windows DWM") : _nvApi.EngineName;
        public int DesktopLevel
        {
            get => _desktopLevel;
            set
            {
                _desktopLevel = value;
                _nvApi.DesktopLevel = value;
                if (_dwm != null) _dwm.DesktopLevel = value;
            }
        }

        public bool IsNvidia => _nvApi.IsAvailable;

        public UnifiedVibranceEngine()
        {
            _nvApi = new NvApiEngine();
            _usingDwm = !_nvApi.IsAvailable;
            if (_usingDwm) GetDwm();
            Program.Log($"UnifiedVibranceEngine: Active engine -> {EngineName} (NVIDIA outputs: {_nvApi.DisplayCount})");
        }

        // Created lazily so NVIDIA-only machines never initialize the Magnification API
        private WindowsDwmEngine? GetDwm()
        {
            if (_dwm == null && !_isDisposed)
            {
                _dwm = new WindowsDwmEngine { DesktopLevel = _desktopLevel };
            }
            return _dwm;
        }

        // Re-enumerate NVIDIA outputs after a display topology change (call on WM_DISPLAYCHANGE, then re-apply)
        public void RefreshDisplays()
        {
            lock (_lock)
            {
                if (!_isDisposed) _nvApi.RefreshDisplays();
            }
        }

        public int GetLevel() => _lastLevel;

        public bool SetLevel(int level) => Apply(level, null);

        public void Restore() => Apply(_desktopLevel, null);

        // Applies `level` to targetDisplay (GDI name, null = every monitor). With NVIDIA DVC the other NVIDIA monitors
        // keep the desktop level; the DWM shader cannot target one monitor, so it covers the whole desktop.
        public bool Apply(int level, string? targetDisplay)
        {
            lock (_lock)
            {
                if (_isDisposed) return false;
                level = Math.Clamp(level, 0, 100);
                _lastLevel = level;

                string? target = FindAttached(targetDisplay);
                bool useDvc = _nvApi.IsAvailable && (target == null || IsDvcOutput(target));

                bool ok;
                if (useDvc)
                {
                    if (target != null && _nvApi.HasDisplayNames)
                    {
                        if (_nvApi.DisplayCount > 1) _nvApi.SetLevelExcept(target, _desktopLevel);
                        ok = _nvApi.SetLevelFor(target, level);
                    }
                    else
                    {
                        ok = _nvApi.SetLevel(level);
                    }
                    SetDwm(NeutralLevel);
                }
                else
                {
                    if (_nvApi.IsAvailable) _nvApi.SetLevel(_desktopLevel);
                    ok = SetDwm(level);
                }

                _usingDwm = !useDvc;
                return ok;
            }
        }

        // The requested monitor's GDI name if it is still attached, else null (= every monitor)
        private static string? FindAttached(string? gdiName)
        {
            if (gdiName == null) return null;
            foreach (var d in DisplayTopology.GetDisplays())
            {
                if (string.Equals(d.GdiName, gdiName, StringComparison.OrdinalIgnoreCase)) return d.GdiName;
            }
            return null;
        }

        private bool IsDvcOutput(string gdiName)
        {
            if (!_nvApi.IsAvailable) return false;
            if (_nvApi.HasDisplayNames) return _nvApi.OwnsDisplay(gdiName);

            // NVAPI could not name its outputs: go by the GPU that drives the monitor
            foreach (var d in DisplayTopology.GetDisplays())
            {
                if (string.Equals(d.GdiName, gdiName, StringComparison.OrdinalIgnoreCase))
                {
                    return d.AdapterName.Contains("NVIDIA", StringComparison.OrdinalIgnoreCase);
                }
            }
            return false;
        }

        // Short label of how colors are applied on that monitor, shown next to it in the profile's monitor list
        public string DescribeTechnology(DisplayInfo display)
        {
            lock (_lock)
            {
                return IsDvcOutput(display.GdiName) ? "NVIDIA DVC" : DescribeNonNvidia(display.AdapterName);
            }
        }

        public static string DescribeNonNvidia(string adapterName)
        {
            if (adapterName.Contains("Intel", StringComparison.OrdinalIgnoreCase)) return "Intel · DWM";
            if (adapterName.Contains("AMD", StringComparison.OrdinalIgnoreCase) ||
                adapterName.Contains("Radeon", StringComparison.OrdinalIgnoreCase)) return "AMD · DWM";
            if (adapterName.Contains("NVIDIA", StringComparison.OrdinalIgnoreCase)) return "NVIDIA · DWM";
            return "DWM";
        }

        private bool SetDwm(int level)
        {
            // Neutral and no DWM vibrance applied: nothing to undo, don't touch the Magnification API
            if (level == NeutralLevel && !DwmShaderColorEngine.IsVibranceActive) return true;
            var dwm = GetDwm();
            return dwm != null && dwm.SetLevel(level);
        }

        public void ResetToDefault()
        {
            try
            {
                _nvApi.ResetToDefault();
                _dwm?.ResetToDefault();
            }
            catch { }
        }

        public void Dispose()
        {
            if (_isDisposed) return;
            try
            {
                ResetToDefault();
                _nvApi.Dispose();
                _dwm?.Dispose();
            }
            catch { }
            _isDisposed = true;
        }
    }
}
