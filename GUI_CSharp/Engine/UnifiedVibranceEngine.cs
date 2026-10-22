using System;

namespace GUI.Engine
{
    // Routes vibrance per monitor between NVIDIA DVC (outputs wired to the NVIDIA GPU) and the DWM shader
    // (desktop-wide, for Intel / AMD outputs). On a hybrid laptop the internal panel usually runs on the iGPU
    // while HDMI is wired to the dGPU, so both engines may be needed and the topology can change at runtime.
    public class UnifiedVibranceEngine : IVibranceEngine
    {
        private const int NeutralLevel = 50;

        private readonly NvApiEngine _nvApi;
        private WindowsDwmEngine? _dwm;
        private readonly object _lock = new();
        private bool _isDisposed = false;
        private int _desktopLevel = NeutralLevel;
        private int _lastLevel = NeutralLevel;

        public bool IsAvailable => _nvApi.IsAvailable || (GetDwm()?.IsAvailable ?? false);
        public string EngineName { get; private set; } = "Windows DWM";
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
            UpdateEngineName();
            Program.Log($"UnifiedVibranceEngine: {EngineName} (NVIDIA outputs: {_nvApi.DisplayCount})");
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
                if (_isDisposed) return;
                _nvApi.RefreshDisplays();
                UpdateEngineName();
            }
        }

        public int GetLevel() => _lastLevel;

        public bool SetLevel(int level) => Apply(level, null);

        public void Restore() => Apply(_desktopLevel, null);

        // Applies `level` to targetDisplay (GDI name, null = every monitor); other monitors keep the desktop level.
        public bool Apply(int level, string? targetDisplay)
        {
            lock (_lock)
            {
                if (_isDisposed) return false;
                level = Math.Clamp(level, 0, 100);
                _lastLevel = level;

                var displays = GammaEngine.GetAttachedDisplays();
                bool nvAvailable = _nvApi.IsAvailable;
                bool hasNonNvidia = false;
                bool targetAttached = false;
                foreach (var d in displays)
                {
                    if (!IsNvidiaOutput(d)) hasNonNvidia = true;
                    if (targetDisplay != null && string.Equals(d.Name, targetDisplay, StringComparison.OrdinalIgnoreCase)) targetAttached = true;
                }
                if (!targetAttached) targetDisplay = null;

                bool ok;
                if (!nvAvailable)
                {
                    // Intel / AMD only (or HDMI wired to the iGPU): DWM is desktop-wide, no per-monitor routing possible
                    ok = SetDwm(level);
                }
                else if (targetDisplay == null)
                {
                    if (!hasNonNvidia)
                    {
                        ok = _nvApi.SetLevel(level);
                        SetDwm(NeutralLevel);
                    }
                    else
                    {
                        // Hybrid: DWM covers every screen, so keep DVC neutral to avoid double saturation on NVIDIA outputs
                        _nvApi.SetLevel(NeutralLevel);
                        ok = SetDwm(level);
                    }
                }
                else if (IsNvidiaOutput(targetDisplay))
                {
                    if (_nvApi.HasDisplayNames)
                    {
                        // Other NVIDIA outputs keep the desktop level; iGPU screens stay neutral while gaming
                        _nvApi.SetLevel(hasNonNvidia ? NeutralLevel : _desktopLevel);
                        ok = _nvApi.SetLevelFor(targetDisplay, level);
                    }
                    else
                    {
                        ok = _nvApi.SetLevel(level);
                    }
                    SetDwm(NeutralLevel);
                }
                else
                {
                    // Game on an iGPU screen of a hybrid laptop: DWM (desktop-wide), DVC neutral to avoid double saturation
                    _nvApi.SetLevel(NeutralLevel);
                    ok = SetDwm(level);
                }

                UpdateEngineName();
                return ok;
            }
        }

        private bool IsNvidiaOutput(GammaEngine.DisplayTarget d)
        {
            if (!_nvApi.IsAvailable) return false;
            if (_nvApi.HasDisplayNames) return _nvApi.OwnsDisplay(d.Name);
            return d.AdapterName.Contains("NVIDIA", StringComparison.OrdinalIgnoreCase);
        }

        private bool IsNvidiaOutput(string displayName)
        {
            foreach (var d in GammaEngine.GetAttachedDisplays())
            {
                if (string.Equals(d.Name, displayName, StringComparison.OrdinalIgnoreCase)) return IsNvidiaOutput(d);
            }
            return false;
        }

        // Short label of how colors are applied on that monitor, shown next to it in the profile's monitor list
        public string DescribeTechnology(string gdiName, string adapterName)
        {
            lock (_lock)
            {
                if (IsNvidiaOutput(gdiName)) return "NVIDIA DVC";
            }
            return DescribeNonNvidia(adapterName);
        }

        public static string DescribeNonNvidia(string adapterName)
        {
            if (adapterName.Contains("Intel", StringComparison.OrdinalIgnoreCase)) return "Intel · DWM";
            if (adapterName.Contains("AMD", StringComparison.OrdinalIgnoreCase) ||
                adapterName.Contains("Radeon", StringComparison.OrdinalIgnoreCase)) return "AMD · DWM";
            if (adapterName.Contains("NVIDIA", StringComparison.OrdinalIgnoreCase)) return "NVIDIA";
            return "DWM";
        }

        private bool SetDwm(int level)
        {
            // Neutral and no DWM vibrance applied: nothing to undo, don't touch the Magnification API
            if (level == NeutralLevel && !DwmShaderColorEngine.IsVibranceActive) return true;
            var dwm = GetDwm();
            return dwm != null && dwm.SetLevel(level);
        }

        private void UpdateEngineName()
        {
            bool dwmInUse = DwmShaderColorEngine.IsVibranceActive;
            if (_nvApi.IsAvailable)
            {
                EngineName = dwmInUse ? "Hybrid (NVIDIA + DWM)" : _nvApi.EngineName;
            }
            else
            {
                EngineName = GetDwm()?.EngineName ?? "Windows DWM";
            }
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
