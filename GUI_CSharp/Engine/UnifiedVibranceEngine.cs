using System;

namespace GUI.Engine
{
    public class UnifiedVibranceEngine : IVibranceEngine
    {
        private readonly NvApiEngine _nvApi;
        private readonly WindowsDwmEngine _dwm;
        private readonly IVibranceEngine _activeEngine;

        public bool IsAvailable => _activeEngine.IsAvailable;
        public string EngineName => _activeEngine.EngineName;
        public int DesktopLevel
        {
            get => _activeEngine.DesktopLevel;
            set
            {
                _nvApi.DesktopLevel = value;
                _dwm.DesktopLevel = value;
            }
        }

        public IVibranceEngine ActiveEngine => _activeEngine;
        public bool IsNvidia => _activeEngine is NvApiEngine;

        public UnifiedVibranceEngine()
        {
            _nvApi = new NvApiEngine();
            _dwm = new WindowsDwmEngine();

            if (_nvApi.IsAvailable && _nvApi.DisplayCount > 0)
            {
                _activeEngine = _nvApi;
                Program.Log($"UnifiedVibranceEngine: Active engine -> NVIDIA DVC ({_nvApi.DisplayCount} displays)");
            }
            else if (_dwm.IsAvailable)
            {
                _activeEngine = _dwm;
                Program.Log("UnifiedVibranceEngine: Active engine -> Windows DWM (Intel / AMD / Generic)");
            }
            else
            {
                _activeEngine = _nvApi;
                Program.Log("UnifiedVibranceEngine: Fallback to NvApi (No engine available)");
            }
        }

        public int GetLevel() => _activeEngine.GetLevel();

        public bool SetLevel(int level) => _activeEngine.SetLevel(level);

        public void Restore() => _activeEngine.Restore();

        public void ResetToDefault()
        {
            try
            {
                _activeEngine.ResetToDefault();
            }
            catch { }
        }

        public void Dispose()
        {
            try
            {
                _activeEngine.ResetToDefault();
                _nvApi.Dispose();
                _dwm.Dispose();
            }
            catch { }
        }
    }
}
