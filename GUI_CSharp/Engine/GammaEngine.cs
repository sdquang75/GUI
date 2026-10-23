using System;
using System.Collections.Generic;
using System.IO;
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

        // LUT each monitor had before this app wrote to it (Windows calibration / ICC vcgt), keyed by MonitorKey.
        // "Restore" writes these back instead of a flat identity ramp, so a calibrated monitor keeps its calibration.
        private readonly Dictionary<string, RAMP> _originalRamps = new(StringComparer.OrdinalIgnoreCase);
        private bool _captureEnabled = false;
        private bool _ownsSessionMarker = false;

        // Exists while a session that captured LUTs is running. Still there at the next start = that run did not
        // restore the LUTs (crash / kill), so what is loaded now may be its curve or a black flash, not the calibration.
        private static string SessionMarkerPath => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GUI", "gamma_session.lock");

        public GammaEngine()
        {
        }

        // Call once at startup, before the first ramp write. Monitors attached later are captured the first time
        // a ramp is written while they are attached (still untouched by this app at that point).
        public void CaptureOriginalRamps()
        {
            lock (_lock)
            {
                if (_isDisposed || _captureEnabled) return;
                _captureEnabled = true;

                bool previousRunLeftLut = false;
                try
                {
                    previousRunLeftLut = File.Exists(SessionMarkerPath);
                    Directory.CreateDirectory(Path.GetDirectoryName(SessionMarkerPath)!);
                    File.WriteAllText(SessionMarkerPath, Environment.ProcessId.ToString());
                    _ownsSessionMarker = true;
                }
                catch (Exception ex)
                {
                    Program.Log($"GammaEngine: session marker unavailable: {ex.Message}");
                }

                if (previousRunLeftLut)
                {
                    // Don't adopt a leftover curve as the "original": these monitors fall back to identity
                    Program.Log("GammaEngine: previous run did not restore the LUTs -> using identity as original for current monitors.");
                    RAMP identity = BuildIdentityRamp();
                    foreach (var d in DisplayTopology.GetDisplays())
                    {
                        _originalRamps[MonitorKey(d)] = identity;
                    }
                }

                CaptureUnknownDisplays();
            }
        }

        private static string MonitorKey(DisplayInfo d) => string.IsNullOrEmpty(d.DevicePath) ? d.GdiName : d.DevicePath;

        // Reads the LUT of every attached monitor not seen yet. Cheap when nothing is new (dictionary lookups on the
        // cached topology), so it runs before every write and a hot-plugged monitor is captured before we touch it.
        private void CaptureUnknownDisplays()
        {
            if (!_captureEnabled) return;
            foreach (var d in DisplayTopology.GetDisplays())
            {
                string key = MonitorKey(d);
                if (_originalRamps.ContainsKey(key)) continue;

                RAMP ramp;
                bool ok = false;
                IntPtr hdc = d.IsPrimary ? GetDC(IntPtr.Zero) : CreateDC(null, d.GdiName, null, IntPtr.Zero);
                if (hdc != IntPtr.Zero)
                {
                    try
                    {
                        ok = GetDeviceGammaRamp(hdc, &ramp) && IsPlausibleOriginal(&ramp);
                    }
                    finally
                    {
                        if (d.IsPrimary) ReleaseDC(IntPtr.Zero, hdc);
                        else DeleteDC(hdc);
                    }
                }

                if (!ok) ramp = BuildIdentityRamp();
                _originalRamps[key] = ramp;
                Program.Log($"GammaEngine: original LUT of {d.GdiName} ({d.FriendlyName}) -> {(ok ? "captured" : "identity")}");
            }
        }

        // A calibration curve rises steadily and still reaches a bright white; a dimmed or inverted ramp
        // (another tool's effect, a stuck flash) is not something to restore.
        private static bool IsPlausibleOriginal(RAMP* ramp)
        {
            ushort* channels0 = ramp->Red;
            for (int c = 0; c < 3; c++)
            {
                ushort* ch = channels0 + c * 256;
                if (ch[255] < 32768) return false;
                for (int i = 1; i < 256; i++)
                {
                    if (ch[i] < ch[i - 1]) return false;
                }
            }
            return true;
        }

        // Puts every attached monitor back on its original LUT (identity when unknown or when GDI refuses it)
        private bool RestoreOriginalRamps()
        {
            CaptureUnknownDisplays();
            var displays = DisplayTopology.GetDisplays();
            if (displays.Count == 0)
            {
                RAMP identity = BuildIdentityRamp();
                return SetRampToAllDisplays(&identity);
            }

            bool any = false;
            foreach (var d in displays)
            {
                RAMP ramp = _originalRamps.TryGetValue(MonitorKey(d), out var orig) ? orig : BuildIdentityRamp();
                bool ok = WriteRampTo(d, &ramp);
                if (!ok)
                {
                    RAMP identity = BuildIdentityRamp();
                    ok = WriteRampTo(d, &identity);
                }
                any |= ok;
            }
            return any;
        }

        private static bool WriteRampTo(DisplayInfo d, RAMP* pRamp)
        {
            IntPtr hdc = d.IsPrimary ? GetDC(IntPtr.Zero) : CreateDC(null, d.GdiName, null, IntPtr.Zero);
            if (hdc == IntPtr.Zero) return false;
            try
            {
                for (int retry = 0; retry < 3; retry++)
                {
                    if (SetDeviceGammaRamp(hdc, pRamp)) return true;
                }
                return false;
            }
            finally
            {
                if (d.IsPrimary) ReleaseDC(IntPtr.Zero, hdc);
                else DeleteDC(hdc);
            }
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
            CaptureUnknownDisplays();

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
                foreach (var target in DisplayTopology.GetDisplays())
                {
                    // Skip primary display if already successfully updated via primaryHdc
                    if (target.IsPrimary && primarySuccess) continue;

                    IntPtr hdc = CreateDC(null, target.GdiName, null, IntPtr.Zero);
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

        // Writes the ramp only to the profile's target monitor (null = every monitor).
        // A target that is no longer attached (e.g. HDMI unplugged) falls back to every monitor.
        private unsafe bool SetRampToTargets(RAMP* pRamp)
        {
            string? target = _targetDisplay;
            if (target == null) return SetRampToAllDisplays(pRamp);

            CaptureUnknownDisplays();
            DisplayInfo? match = null;
            foreach (var d in DisplayTopology.GetDisplays())
            {
                if (string.Equals(d.GdiName, target, StringComparison.OrdinalIgnoreCase))
                {
                    match = d;
                    break;
                }
            }
            if (match == null) return SetRampToAllDisplays(pRamp);

            bool isPrimary = match.IsPrimary;
            IntPtr hdc = isPrimary ? GetDC(IntPtr.Zero) : CreateDC(null, target, null, IntPtr.Zero);
            if (hdc == IntPtr.Zero) return false;
            try
            {
                for (int retry = 0; retry < 5; retry++)
                {
                    if (SetDeviceGammaRamp(hdc, pRamp)) return true;
                }
                return false;
            }
            finally
            {
                if (isPrimary) ReleaseDC(IntPtr.Zero, hdc);
                else DeleteDC(hdc);
            }
        }

        private string? _targetDisplay;

        public string? TargetDisplay => _targetDisplay;

        // Restricts gamma / black flash to one monitor (GDI name like "\\.\DISPLAY2"); null = all monitors.
        // The DWM fallback path stays desktop-wide (Magnification API has no per-monitor effect).
        public void SetTargetDisplay(string? displayName)
        {
            lock (_lock)
            {
                if (_isDisposed) return;
                if (string.Equals(_targetDisplay, displayName, StringComparison.OrdinalIgnoreCase)) return;

                Program.Log($"GammaEngine: target display -> {displayName ?? "all monitors"}");
                _targetDisplay = displayName;

                if (ActiveMode == GammaPipelineMode.HardwareLut)
                {
                    // Put every LUT back to its original so the previous target is not left tinted / dimmed,
                    // then re-apply on the new one
                    RestoreOriginalRamps();
                    if (IsCustomActive)
                    {
                        fixed (RAMP* pRamp = &_lastAppliedRamp)
                        {
                            SetRampToTargets(pRamp);
                        }
                    }
                }
                else if (displayName != null)
                {
                    Program.Log("GammaEngine: DWM compositor path is active -> effect stays desktop-wide (no per-monitor support).");
                }
            }
        }

        private static RAMP BuildIdentityRamp()
        {
            RAMP identity = new RAMP();
            for (int i = 0; i < 256; i++)
            {
                ushort val = (ushort)Math.Clamp((int)((i / 255.0) * 65535.0 + 0.5), 0, 65535);
                identity.Red[i] = val;
                identity.Green[i] = val;
                identity.Blue[i] = val;
            }
            return identity;
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

                // A disabled monitor (stretched-resolution setup) used to send everything to the DWM shader. That
                // shader is desktop-wide and may not reach an exclusive-fullscreen game, which is the case the
                // hardware LUT exists for. So the LUT is still tried first on the attached monitors and read back;
                // only when it did not take effect does this monitor setup stay on DWM until it changes.
                bool monitorsDisabled = MonitorEngine.HasAnyDisabledMonitors();
                if (monitorsDisabled && _switchedToDwmDueToDisabledMonitor)
                {
                    ActiveMode = GammaPipelineMode.DwmCompositor;
                    return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                }

                // Normal path: Attempt hardware LUT on all active displays.
                // Skip the direct attempt when GDI recently rejected this (or a brighter) contrast for the same curve.
                long nowTick = Environment.TickCount64;
                bool knownRejected = _lastContrast < 0.50
                    && _lastContrast <= _rejectedContrastCeiling
                    && nowTick - _rejectedContrastTick < RejectedContrastMemoMs;

                bool success = !knownRejected && SetRampToTargets(&newRamp);
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
                    success = SetRampToTargets(&fallbackRamp);
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

                if (success && monitorsDisabled)
                {
                    fixed (RAMP* pApplied = &_lastAppliedRamp)
                    {
                        if (ReadBackMatches(pApplied) == false)
                        {
                            Program.Log("GammaEngine.ApplyCurve: monitor disabled and the hardware LUT did not take effect -> DWM shader.");
                            success = false;
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
                // Remembered only while monitors are disabled: re-enabling them retries the hardware LUT
                _switchedToDwmDueToDisabledMonitor = monitorsDisabled;
                if (monitorsDisabled)
                {
                    // The write may have landed partly; don't stack it under the DWM effect.
                    // _lastAppliedRamp keeps the curve so the switch back to the LUT can re-apply it.
                    RestoreOriginalRamps();
                }
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

        // Re-applies gamma / boost but keeps the black flash contrast currently on screen. A profile re-apply in the
        // middle of a flash (focus or display change) would otherwise reset it to 1.0, and during the flash hold CS2
        // sends no new packet for up to 1s, so the screen would turn white. Read and written under the same lock as
        // ApplyContrast, so a flash ending on the GSI thread at that moment is never overwritten by a stale value.
        public bool ApplyCurveKeepingContrast(double gamma, double blackBoost)
        {
            lock (_lock)
            {
                if (_isDisposed) return false;
                return ApplyCurve(gamma, blackBoost, _lastContrast);
            }
        }

        public bool EnforceIfNeeded()
        {
            lock (_lock)
            {
                if (_isDisposed || !IsCustomActive) return false;
                if (_lastContrast < 0.98) return false; // During flashbang fade, don't interrupt

                bool monitorsDisabled = MonitorEngine.HasAnyDisabledMonitors();

                // Case 1: on DWM because the LUT did not take effect with a monitor disabled, and the monitor is
                // back: rebuild the curve (ApplyCurveCore tries the hardware LUT first again)
                if (_switchedToDwmDueToDisabledMonitor && !monitorsDisabled)
                {
                    _switchedToDwmDueToDisabledMonitor = false;
                    Program.Log("GammaEngine.EnforceIfNeeded: Monitor re-enabled detected -> retrying Hardware LUT.");
                    return ApplyCurveCore(_lastGamma, _lastBlackBoost, _lastContrast);
                }

                // If in DWM mode (HW LUT failed on this machine / monitor setup), stay stably without flickering
                if (ActiveMode == GammaPipelineMode.DwmCompositor)
                {
                    return false;
                }

                // Case 2: Standard Hardware LUT mode - check if ramp was overridden
                bool needsEnforce;
                fixed (RAMP* pApplied = &_lastAppliedRamp)
                {
                    needsEnforce = ReadBackMatches(pApplied) == false;
                }

                if (needsEnforce)
                {
                    bool success;
                    fixed (RAMP* pRamp = &_lastAppliedRamp)
                    {
                        success = SetRampToTargets(pRamp);
                        if (success && monitorsDisabled && ReadBackMatches(pRamp) == false) success = false;
                    }
                    if (!success)
                    {
                        Program.Log("GammaEngine.EnforceIfNeeded: Hardware LUT re-apply failed -> switching to DWM Shader.");
                        _switchedToDwmDueToDisabledMonitor = monitorsDisabled;
                        ActiveMode = GammaPipelineMode.DwmCompositor;
                        return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                    }
                    return true;
                }

                return false;
            }
        }

        // Whether the LUT loaded on the profile's monitor (primary when none) matches `expected`, compared on the
        // red channel's mid-grey, the level an override or a dropped write moves the most. null = cannot be read.
        private bool? ReadBackMatches(RAMP* expected)
        {
            string? target = _targetDisplay;
            bool readTarget = target != null && !string.Equals(target, DisplayTopology.GetPrimaryName(), StringComparison.OrdinalIgnoreCase);
            IntPtr hdc = readTarget ? CreateDC(null, target, null, IntPtr.Zero) : GetDC(IntPtr.Zero);
            bool ownsDc = readTarget;
            if (hdc == IntPtr.Zero)
            {
                hdc = CreateDC("DISPLAY", null, null, IntPtr.Zero);
                ownsDc = true;
            }
            if (hdc == IntPtr.Zero) return null;

            try
            {
                RAMP current;
                if (!GetDeviceGammaRamp(hdc, &current)) return null;
                return Math.Abs(current.Red[128] - expected->Red[128]) <= 800;
            }
            finally
            {
                if (ownsDc) DeleteDC(hdc);
                else ReleaseDC(IntPtr.Zero, hdc);
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
                // Always every monitor, so a previous target never stays tinted / dimmed.
                // _lastAppliedRamp is only compared while a custom curve is active, so identity is fine there.
                _lastAppliedRamp = BuildIdentityRamp();
                RestoreOriginalRamps();
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
                    ReapplyLastRamp();
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
                    ReapplyLastRamp();
                }
                else
                {
                    ResetToIdentity();
                }
            }
        }

        // Puts _lastAppliedRamp back on screen through the current pipeline. A disabled monitor no longer forces
        // DWM here: the hardware LUT is used while it works (see ApplyCurveCore), DWM only when it fails.
        private void ReapplyLastRamp()
        {
            if (ActiveMode != GammaPipelineMode.DwmCompositor)
            {
                fixed (RAMP* pRamp = &_lastAppliedRamp)
                {
                    if (SetRampToTargets(pRamp)) return;
                }
                ActiveMode = GammaPipelineMode.DwmCompositor;
            }
            DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
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

                // LUTs are back to their originals: the next start may trust what it reads
                if (_ownsSessionMarker)
                {
                    try { File.Delete(SessionMarkerPath); } catch { }
                    _ownsSessionMarker = false;
                }
            }
        }
    }
}
