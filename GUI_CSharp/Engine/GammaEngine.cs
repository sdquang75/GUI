using System;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    [StructLayout(LayoutKind.Sequential)]
    public unsafe struct RAMP
    {
        public fixed ushort Red[256];
        public fixed ushort Green[256];
        public fixed ushort Blue[256];
    }

    public enum GammaPipelineMode
    {
        HardwareLut,
        DwmCompositor
    }

    public struct GammaState
    {
        public bool WasActive;
        public GammaPipelineMode Mode;
        public double Gamma;
        public double BlackBoost;
        public double Contrast;
        public RAMP Ramp;
    }

    public unsafe class GammaEngine : IDisposable
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

        [DllImport("user32.dll")]
        private static extern IntPtr GetDC(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

        [DllImport("gdi32.dll", CharSet = CharSet.Auto, SetLastError = true)]
        private static extern IntPtr CreateDC(
            string? lpszDriver,
            string? lpszDevice,
            string? lpszOutput,
            IntPtr lpInitData);

        [DllImport("gdi32.dll", SetLastError = true)]
        private static extern bool DeleteDC(IntPtr hdc);

        [DllImport("gdi32.dll", SetLastError = true)]
        private static extern bool GetDeviceGammaRamp(IntPtr hDC, void* lpRamp);

        [DllImport("gdi32.dll", SetLastError = true)]
        private static extern bool SetDeviceGammaRamp(IntPtr hDC, void* lpRamp);

        private RAMP _lastAppliedRamp;
        private bool _isDisposed = false;
        private double _lastGamma = 2.20;
        private double _lastBlackBoost = 0.0;
        private double _lastContrast = 1.0;
        private bool _switchedToDwmDueToDisabledMonitor = false;
        private readonly object _lock = new object();

        private readonly double[] _cachedBaseCurve = new double[256];
        private double _cachedCurveGamma = -1.0;
        private double _cachedCurveBoost = -1.0;

        // Smaller than one GSI flashed step (1/255), so every real change is still applied
        private const double ContrastEpsilon = 0.001;
        private bool _lastApplyOk = true;

        // Highest contrast GDI rejected for the current base curve (GdiIcmGammaRange not unlocked).
        // Lower values go straight to the 0.50 clamp instead of failing (with retries) on every GSI packet.
        private const long RejectedContrastMemoMs = 30000;
        private double _rejectedContrastCeiling = -1.0;
        private long _rejectedContrastTick = 0;

        public bool IsCustomActive { get; private set; }
        public GammaPipelineMode ActiveMode { get; private set; } = GammaPipelineMode.HardwareLut;
        public double CurrentGamma => _lastGamma;
        public double CurrentBlackBoost => _lastBlackBoost;
        public double CurrentContrast => _lastContrast;

        public GammaEngine()
        {
        }

        private void EnsureBaseCurve(double gamma, double blackBoost)
        {
            if (Math.Abs(_cachedCurveGamma - gamma) < 0.0001 && Math.Abs(_cachedCurveBoost - blackBoost) < 0.0001)
            {
                return;
            }

            double baseP = gamma / 2.20;
            for (int i = 0; i < 256; i++)
            {
                double x = i / 255.0;
                double yGamma = x > 0 ? Math.Pow(x, baseP) : 0.0;
                double lift = (x > 0 && blackBoost > 0)
                    ? Math.Pow(1.0 - x, 4) * x * 3.8 * blackBoost
                    : 0.0;
                _cachedBaseCurve[i] = yGamma + lift;
            }

            _cachedCurveGamma = gamma;
            _cachedCurveBoost = blackBoost;
            _rejectedContrastCeiling = -1.0;
        }

        private const int SM_CMONITORS = 80;

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        private unsafe bool SetRampToAllDisplays(RAMP* pRamp)
        {
            // FAST PATH: Direct hardware update via primary virtual desktop DC (0.01ms!)
            IntPtr primaryHdc = GetDC(IntPtr.Zero);
            bool primarySuccess = false;
            if (primaryHdc != IntPtr.Zero)
            {
                try
                {
                    for (int retry = 0; retry < 5; retry++)
                    {
                        if (SetDeviceGammaRamp(primaryHdc, pRamp))
                        {
                            primarySuccess = true;
                            break;
                        }
                    }
                }
                finally
                {
                    ReleaseDC(IntPtr.Zero, primaryHdc);
                }
            }

            // Single monitor: complete immediately with zero extra overhead
            if (GetSystemMetrics(SM_CMONITORS) <= 1)
            {
                return primarySuccess;
            }

            // Multi-monitor: ensure all secondary attached displays also receive the ramp
            bool anySuccess = primarySuccess;
            try
            {
                foreach (var target in GetDisplayTargets())
                {
                    // Skip primary display if already successfully updated via primaryHdc
                    if (target.IsPrimary && primarySuccess) continue;

                    IntPtr hdc = CreateDC(null, target.Name, null, IntPtr.Zero);
                    if (hdc != IntPtr.Zero)
                    {
                        try
                        {
                            if (SetDeviceGammaRamp(hdc, pRamp))
                            {
                                anySuccess = true;
                            }
                        }
                        finally
                        {
                            DeleteDC(hdc);
                        }
                    }
                }
            }
            catch { }

            return anySuccess;
        }

        private readonly struct DisplayTarget
        {
            public DisplayTarget(string name, bool isPrimary)
            {
                Name = name;
                IsPrimary = isPrimary;
            }

            public string Name { get; }
            public bool IsPrimary { get; }
        }

        private sealed class DisplayTargetCache
        {
            public DisplayTarget[] Targets = Array.Empty<DisplayTarget>();
            public long Tick;
        }

        // EnumDisplayDevicesW over all adapters costs ~0.3 ms; cache it instead of re-running it per ramp write.
        // Invalidated on WM_DISPLAYCHANGE / monitor enable-disable (MonitorEngine.InvalidateMonitorCache), with a TTL as a safety net.
        private const long DisplayCacheTtlMs = 10000;
        private static volatile DisplayTargetCache? _displayCache;

        public static void InvalidateDisplayCache()
        {
            _displayCache = null;
        }

        private static DisplayTarget[] GetDisplayTargets()
        {
            long now = Environment.TickCount64;
            var cache = _displayCache;
            if (cache != null && now - cache.Tick < DisplayCacheTtlMs)
            {
                return cache.Targets;
            }

            var list = new List<DisplayTarget>();
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
                        list.Add(new DisplayTarget(dev.DeviceName, (dev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) != 0));
                    }
                }
            }
            catch { }

            var targets = list.ToArray();
            _displayCache = new DisplayTargetCache { Targets = targets, Tick = now };
            return targets;
        }

        public bool ApplyCurve(double gamma = 1.80, double blackBoost = 0.0, double contrast = 1.0)
        {
            lock (_lock)
            {
                bool ok = ApplyCurveCore(gamma, blackBoost, contrast);
                _lastApplyOk = ok;
                return ok;
            }
        }

        private bool ApplyCurveCore(double gamma, double blackBoost, double contrast)
        {
            lock (_lock)
            {
                if (_isDisposed) return false;

                _lastGamma = Math.Clamp(gamma, 1.20, 3.00);
                _lastBlackBoost = Math.Clamp(blackBoost, 0.0, 1.0);
                _lastContrast = Math.Clamp(contrast, 0.0, 1.0);

                // If both gamma and boost are default identity and contrast is 1.0, reset
                if (Math.Abs(_lastGamma - 2.20) < 0.01 && _lastBlackBoost < 0.001 && Math.Abs(_lastContrast - 1.0) < 0.001)
                {
                    ResetToIdentity();
                    return true;
                }

                EnsureBaseCurve(_lastGamma, _lastBlackBoost);

                RAMP newRamp = new RAMP();
                if (_lastContrast < 0.001)
                {
                    // Instant 0ms Black Flash: all 256 entries are already 0, zero CPU math needed!
                }
                else
                {
                    double c = _lastContrast;
                    for (int i = 0; i < 256; i++)
                    {
                        double y = Math.Clamp(_cachedBaseCurve[i] * c, 0.0, 1.0);
                        ushort val = (ushort)Math.Clamp((int)(y * 65535.0 + 0.5), 0, 65535);

                        newRamp.Red[i] = val;
                        newRamp.Green[i] = val;
                        newRamp.Blue[i] = val;
                    }
                }

                _lastAppliedRamp = newRamp;
                IsCustomActive = true;

                // If any monitor is disabled, hardware LUT cannot apply properly.
                // In that case, directly route to DWM Compositor Shader Engine.
                if (MonitorEngine.HasAnyDisabledMonitors())
                {
                    if (!_switchedToDwmDueToDisabledMonitor || ActiveMode != GammaPipelineMode.DwmCompositor)
                    {
                        Program.Log($"GammaEngine.ApplyCurve: Disabled monitor detected -> applying via DwmShaderColorEngine (DWM Pixel Shader).");
                        ResetHardwareLutOnly();
                        _switchedToDwmDueToDisabledMonitor = true;
                        ActiveMode = GammaPipelineMode.DwmCompositor;
                    }
                    return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                }

                // Normal path: Attempt hardware LUT on all active displays.
                // Skip the direct attempt when GDI recently rejected this (or a brighter) contrast for the same curve.
                long nowTick = Environment.TickCount64;
                bool knownRejected = _lastContrast < 0.50
                    && _lastContrast <= _rejectedContrastCeiling
                    && nowTick - _rejectedContrastTick < RejectedContrastMemoMs;

                bool success = !knownRejected && SetRampToAllDisplays(&newRamp);
                if (!success && _lastContrast < 0.50)
                {
                    // Fallback: If 0-contrast was rejected by Windows GDI (GdiIcmGammaRange not yet set),
                    // clamp to 0.50 (the lowest contrast allowed without registry unlock)
                    RAMP fallbackRamp = new RAMP();
                    for (int i = 0; i < 256; i++)
                    {
                        double y = Math.Clamp(_cachedBaseCurve[i] * 0.50, 0.0, 1.0);
                        ushort val = (ushort)Math.Clamp((int)(y * 65535.0 + 0.5), 0, 65535);

                        fallbackRamp.Red[i] = val;
                        fallbackRamp.Green[i] = val;
                        fallbackRamp.Blue[i] = val;
                    }
                    success = SetRampToAllDisplays(&fallbackRamp);
                    if (success)
                    {
                        _lastAppliedRamp = fallbackRamp;
                        if (!knownRejected)
                        {
                            // HW LUT works, only this low contrast was out of GDI's allowed range
                            _rejectedContrastCeiling = Math.Max(_rejectedContrastCeiling, _lastContrast);
                            _rejectedContrastTick = nowTick;
                        }
                    }
                }

                if (success)
                {
                    _switchedToDwmDueToDisabledMonitor = false;
                    ActiveMode = GammaPipelineMode.HardwareLut;
                    ResetDwmGammaIfActive();
                    return true;
                }

                // Fallback: If hardware LUT fails on all displays (e.g., driver blocked, HDR, disabled PnP node),
                // seamlessly engage DWM Compositor Shader Engine!
                if (ActiveMode != GammaPipelineMode.DwmCompositor)
                {
                    Program.Log($"GammaEngine.ApplyCurve: Hardware LUT failed -> falling back to DwmShaderColorEngine.");
                }
                _switchedToDwmDueToDisabledMonitor = false;
                ActiveMode = GammaPipelineMode.DwmCompositor;
                return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
            }
        }

        public bool ApplyContrast(double contrast)
        {
            lock (_lock)
            {
                if (_isDisposed) return false;

                // CS2 GSI sends a packet on every player_state change (money, health...) plus a 1s heartbeat.
                // Re-writing an identical ramp each time only adds driver work, so skip unchanged contrast.
                // External LUT overrides are still corrected by EnforceIfNeeded.
                double c = Math.Clamp(contrast, 0.0, 1.0);
                if (_lastApplyOk && Math.Abs(c - _lastContrast) < ContrastEpsilon)
                {
                    return true;
                }

                return ApplyCurve(_lastGamma, _lastBlackBoost, c);
            }
        }

        public bool EnforceIfNeeded()
        {
            lock (_lock)
            {
                if (_isDisposed || !IsCustomActive) return false;
                if (_lastContrast < 0.98) return false; // During flashbang fade, don't interrupt

                bool monitorsDisabled = MonitorEngine.HasAnyDisabledMonitors();

                // Case 1: Monitor is disabled
                if (monitorsDisabled)
                {
                    if (ActiveMode != GammaPipelineMode.DwmCompositor)
                    {
                        Program.Log("GammaEngine.EnforceIfNeeded: Monitor disabled detected -> switching to DWM Shader.");
                        ActiveMode = GammaPipelineMode.DwmCompositor;
                        _switchedToDwmDueToDisabledMonitor = true;
                        ResetHardwareLutOnly();
                        return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                    }
                    // Already in DWM mode while monitor is disabled: nothing to enforce repeatedly
                    return false;
                }

                // Case 2: Monitor was previously disabled and is now re-enabled
                if (_switchedToDwmDueToDisabledMonitor && !monitorsDisabled)
                {
                    _switchedToDwmDueToDisabledMonitor = false;
                    Program.Log("GammaEngine.EnforceIfNeeded: Monitor re-enabled detected -> switching back to Hardware LUT.");
                    DwmShaderColorEngine.ResetGammaAndBoost();
                    fixed (RAMP* pRamp = &_lastAppliedRamp)
                    {
                        if (SetRampToAllDisplays(pRamp))
                        {
                            ActiveMode = GammaPipelineMode.HardwareLut;
                            return true;
                        }
                    }
                    // If hardware LUT fails even after re-enable, stay on DWM
                    ActiveMode = GammaPipelineMode.DwmCompositor;
                    DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                    return true;
                }

                // If in DWM mode (e.g. because HW LUT failed on this machine), stay stably without flickering
                if (ActiveMode == GammaPipelineMode.DwmCompositor)
                {
                    return false;
                }

                // Case 3: Standard Hardware LUT mode - check if ramp was overridden
                bool needsEnforce = false;
                RAMP currentRamp;

                IntPtr hdc = GetDC(IntPtr.Zero);
                if (hdc != IntPtr.Zero)
                {
                    if (GetDeviceGammaRamp(hdc, &currentRamp))
                    {
                        ushort currentMid = currentRamp.Red[128];
                        ushort targetMid = _lastAppliedRamp.Red[128];
                        if (Math.Abs(currentMid - targetMid) > 800)
                        {
                            needsEnforce = true;
                        }
                    }
                    ReleaseDC(IntPtr.Zero, hdc);
                }
                else
                {
                    IntPtr dispHdc = CreateDC("DISPLAY", null, null, IntPtr.Zero);
                    if (dispHdc != IntPtr.Zero)
                    {
                        if (GetDeviceGammaRamp(dispHdc, &currentRamp))
                        {
                            ushort currentMid = currentRamp.Red[128];
                            ushort targetMid = _lastAppliedRamp.Red[128];
                            if (Math.Abs(currentMid - targetMid) > 800)
                            {
                                needsEnforce = true;
                            }
                        }
                        DeleteDC(dispHdc);
                    }
                }

                if (needsEnforce)
                {
                    fixed (RAMP* pRamp = &_lastAppliedRamp)
                    {
                        bool success = SetRampToAllDisplays(pRamp);
                        if (!success)
                        {
                            Program.Log("GammaEngine.EnforceIfNeeded: Hardware LUT re-apply failed -> switching to DWM Shader.");
                            ActiveMode = GammaPipelineMode.DwmCompositor;
                            return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                        }
                        return success;
                    }
                }

                return false;
            }
        }

        public void ResetToIdentity()
        {
            lock (_lock)
            {
                IsCustomActive = false;
                _lastGamma = 2.20;
                _lastBlackBoost = 0.0;
                _lastContrast = 1.0;
                _switchedToDwmDueToDisabledMonitor = false;
                ActiveMode = GammaPipelineMode.HardwareLut;

                ResetHardwareLutOnly();
                ResetDwmGammaIfActive();
                _lastApplyOk = true;
            }
        }

        // Only touch the Magnification API when a DWM gamma effect is actually applied; otherwise this would
        // initialize it (even on NVIDIA / HW-LUT machines) and send an extra DWM call on every GSI packet.
        private static void ResetDwmGammaIfActive()
        {
            if (DwmShaderColorEngine.IsGammaBoostActive)
            {
                DwmShaderColorEngine.ResetGammaAndBoost();
            }
        }

        private void ResetHardwareLutOnly()
        {
            try
            {
                RAMP identity = new RAMP();
                for (int i = 0; i < 256; i++)
                {
                    ushort val = (ushort)Math.Clamp((int)((i / 255.0) * 65535.0 + 0.5), 0, 65535);
                    identity.Red[i] = val;
                    identity.Green[i] = val;
                    identity.Blue[i] = val;
                }
                _lastAppliedRamp = identity;
                SetRampToAllDisplays(&identity);
            }
            catch { }
        }

        public void SaveState(out GammaState state)
        {
            lock (_lock)
            {
                state = new GammaState
                {
                    WasActive = IsCustomActive,
                    Mode = ActiveMode,
                    Gamma = _lastGamma,
                    BlackBoost = _lastBlackBoost,
                    Contrast = _lastContrast,
                    Ramp = _lastAppliedRamp
                };
            }
        }

        public void RestoreState(in GammaState state)
        {
            lock (_lock)
            {
                if (_isDisposed) return;

                if (state.WasActive)
                {
                    _lastAppliedRamp = state.Ramp;
                    _lastGamma = state.Gamma;
                    _lastBlackBoost = state.BlackBoost;
                    _lastContrast = state.Contrast > 0 ? state.Contrast : 1.0;
                    ActiveMode = state.Mode;
                    IsCustomActive = true;
                    _lastApplyOk = true;
                    if (ActiveMode == GammaPipelineMode.DwmCompositor || MonitorEngine.HasAnyDisabledMonitors())
                    {
                        ActiveMode = GammaPipelineMode.DwmCompositor;
                        DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                    }
                    else
                    {
                        fixed (RAMP* pRamp = &_lastAppliedRamp)
                        {
                            SetRampToAllDisplays(pRamp);
                        }
                    }
                }
                else
                {
                    ResetToIdentity();
                }
            }
        }

        public void SaveCurrentState(out bool wasActive, out RAMP ramp)
        {
            lock (_lock)
            {
                wasActive = IsCustomActive;
                ramp = _lastAppliedRamp;
            }
        }

        public void RestoreState(bool wasActive, in RAMP ramp)
        {
            lock (_lock)
            {
                if (_isDisposed) return;

                if (wasActive)
                {
                    _lastAppliedRamp = ramp;
                    IsCustomActive = true;
                    _lastApplyOk = true;
                    if (ActiveMode == GammaPipelineMode.DwmCompositor || MonitorEngine.HasAnyDisabledMonitors())
                    {
                        ActiveMode = GammaPipelineMode.DwmCompositor;
                        DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                    }
                    else
                    {
                        fixed (RAMP* pRamp = &_lastAppliedRamp)
                        {
                            SetRampToAllDisplays(pRamp);
                        }
                    }
                }
                else
                {
                    ResetToIdentity();
                }
            }
        }

        public void Restore()
        {
            ResetToIdentity();
        }

        public void Dispose()
        {
            lock (_lock)
            {
                if (_isDisposed) return;
                ResetToIdentity();
                _isDisposed = true;
            }
        }
    }
}
