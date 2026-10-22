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
                for (uint i = 0; ; i++)
                {
                    var dev = new DISPLAY_DEVICEW();
                    dev.cb = Marshal.SizeOf<DISPLAY_DEVICEW>();
                    if (!EnumDisplayDevicesW(null, i, ref dev, 0)) break;

                    bool attached = (dev.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP) != 0;
                    bool mirror = (dev.StateFlags & DISPLAY_DEVICE_MIRRORING_DRIVER) != 0;
                    bool isPrimary = (dev.StateFlags & DISPLAY_DEVICE_PRIMARY_DEVICE) != 0;

                    // Skip primary display if already successfully updated via primaryHdc
                    if (isPrimary && primarySuccess) continue;

                    if (attached && !mirror && !string.IsNullOrEmpty(dev.DeviceName))
                    {
                        IntPtr hdc = CreateDC(null, dev.DeviceName, null, IntPtr.Zero);
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
            }
            catch { }

            return anySuccess;
        }

        public bool ApplyCurve(double gamma = 1.80, double blackBoost = 0.0, double contrast = 1.0)
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

                // Normal path: Attempt hardware LUT on all active displays
                bool success = SetRampToAllDisplays(&newRamp);
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
                    }
                }

                if (success)
                {
                    _switchedToDwmDueToDisabledMonitor = false;
                    ActiveMode = GammaPipelineMode.HardwareLut;
                    DwmShaderColorEngine.ResetGammaAndBoost();
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
            return ApplyCurve(_lastGamma, _lastBlackBoost, contrast);
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
