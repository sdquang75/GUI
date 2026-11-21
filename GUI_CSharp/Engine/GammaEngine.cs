using System;
using System.Collections.Generic;
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
        public bool EnhancedBoost;
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
        private readonly object _lock = new object();

        private readonly double[] _cachedBaseCurve = new double[256];
        private double _cachedCurveGamma = -1.0;
        private double _cachedCurveBoost = -1.0;
        private bool _cachedCurveEnhanced;

        // Black Boost shape (see BlackBoostCurve). Kept across ApplyContrast calls,
        // so a black flash dims the same curve the profile applied.
        private bool _enhancedBoost;
        public bool EnhancedBoost => _enhancedBoost;

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

        // CreateDC handles of non-primary monitors, kept between writes (a flash sends a packet per game tick) and
        // dropped when the topology snapshot changes. The primary uses the screen DC from GetDC(NULL), which is cheap.
        private readonly Dictionary<string, IntPtr> _dcCache = new(StringComparer.OrdinalIgnoreCase);
        private IReadOnlyList<DisplayInfo>? _dcCacheTopology;

        // Monitors (MonitorKey) whose LUT this app changed since it last reset them. A reset only rewrites
        // these, so the end of a flash touches the game monitor alone instead of every screen in turn.
        // Starts as "all": at startup a monitor may still hold the curve of a run that crashed.
        private readonly HashSet<string> _dirtyMonitors = new(StringComparer.OrdinalIgnoreCase);
        private bool _restoreAll = true;

        // Last ramp WriteCurveRamp got onto the target(s). Any other LUT write clears it.
        private RAMP _lastWrittenRamp;
        private bool _lastWriteValid = false;
        private string? _lastWriteTarget;
        private IReadOnlyList<DisplayInfo>? _lastWriteTopology;

        public GammaEngine()
        {
        }

        private static string MonitorKey(DisplayInfo d) => d.GdiName;

        // Puts the monitors this app changed back on a flat (identity) LUT, as 1.1.3 did. Untouched monitors
        // (the stream screen while only the game monitor flashed) are not rewritten.
        private void ResetChangedRamps()
        {
            _lastWriteValid = false;
            var displays = DisplayTopology.GetDisplays();
            RAMP identity = BuildIdentityRamp();
            if (displays.Count == 0)
            {
                WriteRamp("", usePrimaryDc: true, &identity, 3);
                return;
            }

            bool all = _restoreAll;
            foreach (var d in displays)
            {
                string key = MonitorKey(d);
                if (!all && !_dirtyMonitors.Contains(key)) continue;

                bool ok = WriteRamp(d.GdiName, d.IsPrimary, &identity, 3);

                // A monitor that could not be reset stays on the list for the next reset
                if (ok) _dirtyMonitors.Remove(key);
                else _dirtyMonitors.Add(key);
            }
            _restoreAll = false;
        }

        private void MarkDirty(DisplayInfo? d)
        {
            if (d == null) _restoreAll = true;
            else _dirtyMonitors.Add(MonitorKey(d));
        }

        // DC for one monitor's LUT. usePrimaryDc: the screen DC from GetDC(NULL), release = true (ReleaseDC after use).
        // Otherwise a CreateDC handle from _dcCache, release = false (owned by the cache).
        private IntPtr AcquireDc(string gdiName, bool usePrimaryDc, out bool release)
        {
            if (usePrimaryDc)
            {
                release = true;
                return GetDC(IntPtr.Zero);
            }

            release = false;
            var topology = DisplayTopology.GetDisplays();
            if (!ReferenceEquals(topology, _dcCacheTopology))
            {
                // GDI names can point to another monitor (or none) after a topology change
                FlushDcCache();
                _dcCacheTopology = topology;
            }

            if (_dcCache.TryGetValue(gdiName, out IntPtr hdc)) return hdc;
            hdc = CreateDC(null, gdiName, null, IntPtr.Zero);
            if (hdc != IntPtr.Zero) _dcCache[gdiName] = hdc;
            return hdc;
        }

        private void FlushDcCache()
        {
            foreach (var hdc in _dcCache.Values)
            {
                DeleteDC(hdc);
            }
            _dcCache.Clear();
        }

        // Writes one monitor's LUT. A cached DC that keeps failing may have gone stale (driver reset), so it is
        // replaced once and tried again.
        private bool WriteRamp(string gdiName, bool usePrimaryDc, RAMP* pRamp, int attempts)
        {
            for (int pass = 0; pass < 2; pass++)
            {
                IntPtr hdc = AcquireDc(gdiName, usePrimaryDc, out bool release);
                if (hdc == IntPtr.Zero) return false;
                try
                {
                    for (int retry = 0; retry < attempts; retry++)
                    {
                        if (SetDeviceGammaRamp(hdc, pRamp)) return true;
                    }
                }
                finally
                {
                    if (release) ReleaseDC(IntPtr.Zero, hdc);
                }

                if (release) return false;
                DeleteDC(hdc);
                _dcCache.Remove(gdiName);
                attempts = 1;
            }
            return false;
        }

        private void EnsureBaseCurve(double gamma, double blackBoost)
        {
            if (Math.Abs(_cachedCurveGamma - gamma) < 0.0001 && Math.Abs(_cachedCurveBoost - blackBoost) < 0.0001 &&
                _cachedCurveEnhanced == _enhancedBoost)
            {
                return;
            }

            for (int i = 0; i < 256; i++)
            {
                _cachedBaseCurve[i] = BlackBoostCurve.Evaluate(i / 255.0, gamma, blackBoost, _enhancedBoost);
            }

            _cachedCurveGamma = gamma;
            _cachedCurveBoost = blackBoost;
            _cachedCurveEnhanced = _enhancedBoost;
            _rejectedContrastCeiling = -1.0;
        }

        /// <summary>Picks the Black Boost shape for the next ApplyCurve (it does not rewrite the LUT by itself).</summary>
        public void SetEnhancedBoost(bool enhanced)
        {
            lock (_lock)
            {
                _enhancedBoost = enhanced;
            }
        }

        private const int SM_CMONITORS = 80;

        [DllImport("user32.dll")]
        private static extern int GetSystemMetrics(int nIndex);

        private unsafe bool SetRampToAllDisplays(RAMP* pRamp)
        {
            var displays = DisplayTopology.GetDisplays();
            DisplayInfo? primary = null;
            foreach (var d in displays)
            {
                if (d.IsPrimary)
                {
                    primary = d;
                    break;
                }
            }

            // FAST PATH: Direct hardware update via primary virtual desktop DC (0.01ms!)
            bool primarySuccess = WriteRamp(primary?.GdiName ?? "", usePrimaryDc: true, pRamp, 5);
            if (primarySuccess) MarkDirty(primary);

            // Single monitor: complete immediately with zero extra overhead
            if (GetSystemMetrics(SM_CMONITORS) <= 1)
            {
                return primarySuccess;
            }

            // Multi-monitor: ensure all secondary attached displays also receive the ramp
            bool anySuccess = primarySuccess;
            try
            {
                foreach (var target in displays)
                {
                    // Skip primary display if already successfully updated via primaryHdc
                    if (target.IsPrimary && primarySuccess) continue;

                    if (WriteRamp(target.GdiName, usePrimaryDc: false, pRamp, 1))
                    {
                        anySuccess = true;
                        MarkDirty(target);
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
            _lastWriteValid = false;
            string? target = _targetDisplay;
            if (target == null) return SetRampToAllDisplays(pRamp);

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

            bool ok = WriteRamp(match.GdiName, match.IsPrimary, pRamp, 5);
            if (ok) MarkDirty(match);
            return ok;
        }

        // SetRampToTargets for a curve, skipped when this exact ramp already went to the same target on the same
        // topology: below the 0.50 clamp every step of a flash fade builds the identical ramp.
        private bool WriteCurveRamp(RAMP* pRamp)
        {
            if (_lastWriteValid &&
                ReferenceEquals(DisplayTopology.GetDisplays(), _lastWriteTopology) &&
                string.Equals(_targetDisplay, _lastWriteTarget, StringComparison.OrdinalIgnoreCase))
            {
                fixed (RAMP* pLast = &_lastWrittenRamp)
                {
                    if (new ReadOnlySpan<byte>(pLast, sizeof(RAMP)).SequenceEqual(new ReadOnlySpan<byte>(pRamp, sizeof(RAMP))))
                    {
                        return true;
                    }
                }
            }

            if (!SetRampToTargets(pRamp)) return false;
            _lastWrittenRamp = *pRamp;
            _lastWriteTarget = _targetDisplay;
            _lastWriteTopology = DisplayTopology.GetDisplays();
            _lastWriteValid = true;
            return true;
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
                    // Reset every LUT this app changed so the previous target is not left tinted / dimmed,
                    // then re-apply on the new one
                    ResetChangedRamps();
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

                // Normal path: Attempt hardware LUT on all active displays.
                // Skip the direct attempt when GDI recently rejected this (or a brighter) contrast for the same curve.
                long nowTick = Environment.TickCount64;
                bool knownRejected = _lastContrast < 0.50
                    && _lastContrast <= _rejectedContrastCeiling
                    && nowTick - _rejectedContrastTick < RejectedContrastMemoMs;

                bool success = !knownRejected && WriteCurveRamp(&newRamp);
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
                    success = WriteCurveRamp(&fallbackRamp);
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
                    ActiveMode = GammaPipelineMode.HardwareLut;
                    ResetDwmGammaIfActive();
                    return true;
                }

                // Fallback: If hardware LUT fails on all displays (e.g., driver blocked, HDR),
                // seamlessly engage DWM Compositor Shader Engine!
                if (ActiveMode != GammaPipelineMode.DwmCompositor)
                {
                    Program.Log($"GammaEngine.ApplyCurve: Hardware LUT failed -> falling back to DwmShaderColorEngine.");
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

                // If in DWM mode (HW LUT failed on this machine), stay stably without flickering
                if (ActiveMode == GammaPipelineMode.DwmCompositor)
                {
                    return false;
                }

                // Standard Hardware LUT mode - check if ramp was overridden
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
                    }
                    if (!success)
                    {
                        Program.Log("GammaEngine.EnforceIfNeeded: Hardware LUT re-apply failed -> switching to DWM Shader.");
                        ActiveMode = GammaPipelineMode.DwmCompositor;
                        return DwmShaderColorEngine.SetGammaAndBoost(_lastGamma, _lastBlackBoost, _lastContrast);
                    }
                    return true;
                }

                return false;
            }
        }

        // Whether the LUT loaded on the target monitor (primary when none) matches `expected`, compared on the
        // red channel's mid-grey, the level an override or a dropped write moves the most. null = cannot be read.
        private bool? ReadBackMatches(RAMP* expected)
        {
            string? target = _targetDisplay;
            bool readTarget = target != null && !string.Equals(target, DisplayTopology.GetPrimaryName(), StringComparison.OrdinalIgnoreCase);
            IntPtr hdc = AcquireDc(target ?? "", usePrimaryDc: !readTarget, out bool release);
            bool ownsDc = false;
            if (hdc == IntPtr.Zero)
            {
                hdc = CreateDC("DISPLAY", null, null, IntPtr.Zero);
                ownsDc = true;
                release = false;
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
                else if (release) ReleaseDC(IntPtr.Zero, hdc);
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
                // Every monitor this app changed, so a previous target never stays tinted / dimmed.
                // _lastAppliedRamp is only compared while a custom curve is active, so identity is fine there.
                _lastAppliedRamp = BuildIdentityRamp();
                ResetChangedRamps();
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
                    EnhancedBoost = _enhancedBoost,
                    Ramp = _lastAppliedRamp
                };
            }
        }

        public void RestoreState(in GammaState state)
        {
            lock (_lock)
            {
                if (_isDisposed) return;
                _enhancedBoost = state.EnhancedBoost;

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

        // Puts _lastAppliedRamp back on screen through the current pipeline: the hardware LUT while it works
        // (see ApplyCurveCore), DWM only when it fails.
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
                FlushDcCache();
            }
        }
    }
}
