using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// Pure Software Audio Session Limiter & Balancer (Sound Lock 1:1 behavior, zero delay).
    /// Does NOT send rapid hardware USB volume commands to external DACs (e.g. Sound BlasterX G6),
    /// eliminating USB brownouts and disconnection issues.
    /// </summary>
    public class SessionSoundLockEngine : IDisposable
    {
        [DllImport("winmm.dll", EntryPoint = "timeBeginPeriod", SetLastError = true)]
        private static extern uint TimeBeginPeriod(uint uMilliseconds);

        [DllImport("winmm.dll", EntryPoint = "timeEndPeriod", SetLastError = true)]
        private static extern uint TimeEndPeriod(uint uMilliseconds);

        private readonly object _lock = new();
        private Thread? _workerThread;
        private volatile bool _isRunning = false;
        private bool _timerPeriodActive = false;

        private int _targetPid = 0;
        private float _targetGameMasterVol = 0.60f;
        private float _soundLockThreshold = 0.40f;
        private bool _balanceBackgroundApps = true;
        private bool _fastRecovery = true;

        private float _originalMasterVolume = 0.40f;
        private bool _hasModifiedMasterVolume = false;

        private readonly Dictionary<IntPtr, float> _scaledBackgroundVolumes = new();

        public bool IsRunning => _isRunning;

        public void Start(int targetPid, float targetGameMasterVol, float soundLockThreshold, bool balanceBackgroundApps, bool fastRecovery = true)
        {
            lock (_lock)
            {
                if (_isRunning)
                {
                    Stop();
                }

                _targetPid = targetPid;
                _targetGameMasterVol = Math.Clamp(targetGameMasterVol, 0.05f, 1.0f);
                _soundLockThreshold = Math.Clamp(soundLockThreshold, 0.05f, 1.0f);
                _balanceBackgroundApps = balanceBackgroundApps;
                _fastRecovery = fastRecovery;

                try
                {
                    ApplyMasterAndBackgroundScaling();
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"[SoundLock] Failed to apply scaling: {ex.Message}");
                }

                try
                {
                    if (!_timerPeriodActive && TimeBeginPeriod(1) == 0)
                    {
                        _timerPeriodActive = true;
                    }
                }
                catch { }

                _isRunning = true;
                _workerThread = new Thread(WorkerLoop)
                {
                    IsBackground = true,
                    Priority = ThreadPriority.AboveNormal,
                    Name = "SoundLockWorker"
                };
                _workerThread.Start();
            }
        }

        public void Stop()
        {
            lock (_lock)
            {
                if (!_isRunning) return;

                _isRunning = false;

                try
                {
                    _workerThread?.Join(400);
                }
                catch { }
                _workerThread = null;

                try
                {
                    RestoreMasterAndBackgroundScaling();
                }
                catch (Exception ex)
                {
                    Debug.WriteLine($"[SoundLock] Failed to restore scaling: {ex.Message}");
                }

                try
                {
                    if (_timerPeriodActive)
                    {
                        TimeEndPeriod(1);
                        _timerPeriodActive = false;
                    }
                }
                catch { }
            }
        }

        private void ApplyMasterAndBackgroundScaling()
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
            if (enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out var dev) != 0 || dev == null)
            {
                return;
            }

            // 1. Read & Set Master Endpoint Volume
            Guid iidEpVol = typeof(IAudioEndpointVolume).GUID;
            if (dev.Activate(ref iidEpVol, 1, IntPtr.Zero, out var epVolObj) == 0 && epVolObj is IAudioEndpointVolume epVol)
            {
                epVol.GetMasterVolumeLevelScalar(out _originalMasterVolume);

                if (_targetGameMasterVol > 0 && Math.Abs(_originalMasterVolume - _targetGameMasterVol) > 0.005f)
                {
                    Guid ctx = Guid.Empty;
                    epVol.SetMasterVolumeLevelScalar(_targetGameMasterVol, ref ctx);
                    _hasModifiedMasterVolume = true;
                }
            }

            // 2. Balance external background apps if requested
            if (_balanceBackgroundApps && _hasModifiedMasterVolume && _targetGameMasterVol > _originalMasterVolume)
            {
                float scaleFactor = Math.Clamp(_originalMasterVolume / _targetGameMasterVol, 0.05f, 1.0f);

                Guid iidMgr = typeof(IAudioSessionManager2).GUID;
                if (dev.Activate(ref iidMgr, 1, IntPtr.Zero, out var mgrObj) == 0 && mgrObj is IAudioSessionManager2 mgr)
                {
                    if (mgr.GetSessionEnumerator(out var sessionEnum) == 0 && sessionEnum != null)
                    {
                        sessionEnum.GetCount(out int count);
                        _scaledBackgroundVolumes.Clear();

                        for (int i = 0; i < count; i++)
                        {
                            if (sessionEnum.GetSession(i, out var ctl) == 0 && ctl != null)
                            {
                                if (ctl is IAudioSessionControl2 ctl2)
                                {
                                    ctl2.GetProcessId(out uint pid);
                                    bool isSystem = ctl2.IsSystemSoundsSession() == 0;
                                    if (!isSystem && pid != 0 && pid != (uint)_targetPid)
                                    {
                                        if (ctl is ISimpleAudioVolume sav)
                                        {
                                            sav.GetMasterVolume(out float curVol);
                                            IntPtr unk = Marshal.GetIUnknownForObject(sav);
                                            _scaledBackgroundVolumes[unk] = curVol;

                                            Guid ctx = Guid.Empty;
                                            sav.SetMasterVolume(curVol * scaleFactor, ref ctx);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        private void RestoreMasterAndBackgroundScaling()
        {
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
            if (enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out var dev) != 0 || dev == null)
            {
                return;
            }

            // 1. Restore background sessions
            if (_scaledBackgroundVolumes.Count > 0)
            {
                Guid iidMgr = typeof(IAudioSessionManager2).GUID;
                if (dev.Activate(ref iidMgr, 1, IntPtr.Zero, out var mgrObj) == 0 && mgrObj is IAudioSessionManager2 mgr)
                {
                    if (mgr.GetSessionEnumerator(out var sessionEnum) == 0 && sessionEnum != null)
                    {
                        sessionEnum.GetCount(out int count);
                        for (int i = 0; i < count; i++)
                        {
                            if (sessionEnum.GetSession(i, out var ctl) == 0 && ctl != null)
                            {
                                if (ctl is IAudioSessionControl2 ctl2)
                                {
                                    ctl2.GetProcessId(out uint pid);
                                    if (pid != 0 && pid != (uint)_targetPid)
                                    {
                                        if (ctl is ISimpleAudioVolume sav)
                                        {
                                            Guid ctx = Guid.Empty;
                                            sav.SetMasterVolume(1.0f, ref ctx);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                _scaledBackgroundVolumes.Clear();
            }

            // 2. Restore Master Volume
            if (_hasModifiedMasterVolume)
            {
                Guid iidEpVol = typeof(IAudioEndpointVolume).GUID;
                if (dev.Activate(ref iidEpVol, 1, IntPtr.Zero, out var epVolObj) == 0 && epVolObj is IAudioEndpointVolume epVol)
                {
                    Guid ctx = Guid.Empty;
                    epVol.SetMasterVolumeLevelScalar(_originalMasterVolume, ref ctx);
                }
                _hasModifiedMasterVolume = false;
            }
        }

        private void WorkerLoop()
        {
            var gameSessions = new List<(ISimpleAudioVolume volume, IAudioMeterInformation meter)>();
            long lastSessionScanTime = 0;
            var stopwatch = Stopwatch.StartNew();

            float currentSmoothedVolume = 1.0f;
            float lastAppliedVolume = -1.0f;
            float prevPeak = 0.0f;
            int consecutiveDecayCount = 0;
            Guid ctx = Guid.Empty;

            while (_isRunning)
            {
                long now = stopwatch.ElapsedMilliseconds;

                // Refresh game sessions every 600ms or if none found yet
                if (gameSessions.Count == 0 || (now - lastSessionScanTime > 600))
                {
                    lastSessionScanTime = now;
                    RefreshGameSessions(ref gameSessions);
                }

                if (gameSessions.Count > 0)
                {
                    float maxPeak = 0f;
                    for (int i = 0; i < gameSessions.Count; i++)
                    {
                        try
                        {
                            if (gameSessions[i].meter.GetPeakValue(out float p) == 0 && p > maxPeak)
                            {
                                maxPeak = p;
                            }
                        }
                        catch
                        {
                            // Session may have closed; will be refreshed
                        }
                    }

                    // 1. Predictive Transient Slope Detection & Fast Recovery Acceleration
                    float deltaPeak = maxPeak - prevPeak;
                    float effectivePeak = maxPeak;
                    if (deltaPeak > 0.12f && maxPeak > 0.15f)
                    {
                        // Sudden steep rising edge detected (gunshot transient) -> anticipate peak
                        effectivePeak = Math.Max(maxPeak, maxPeak + deltaPeak * 1.5f);
                        consecutiveDecayCount = 0;
                    }
                    else if (deltaPeak < -0.001f)
                    {
                        consecutiveDecayCount++;
                    }
                    else if (deltaPeak > 0.005f)
                    {
                        consecutiveDecayCount = 0;
                    }
                    prevPeak = maxPeak;

                    // Fast Recovery: Accelerate decay of Windows ballistic VU meter after shooting ends
                    if (_fastRecovery && consecutiveDecayCount > 25)
                    {
                        // After ~25ms of steady decay, accelerate the meter drop so footsteps return quickly (~40-50ms)
                        float acceleration = (consecutiveDecayCount - 25) * 0.025f;
                        effectivePeak = Math.Max(0f, effectivePeak - acceleration);
                    }

                    // 2. Target Volume Calculation
                    float targetVolume = 1.0f;
                    if (effectivePeak > _soundLockThreshold && effectivePeak > 0.001f)
                    {
                        targetVolume = Math.Clamp(_soundLockThreshold / effectivePeak, 0.05f, 1.0f);
                    }

                    // 3. Micro-Ramp (Slew-Rate Limiter) to eliminate waveform discontinuity
                    if (targetVolume < currentSmoothedVolume)
                    {
                        // Fast attack ramp: smooth drop over ~1.5 - 2ms (step 0.30/ms)
                        currentSmoothedVolume = Math.Max(targetVolume, currentSmoothedVolume - 0.30f);
                    }
                    else if (targetVolume > currentSmoothedVolume)
                    {
                        // Fast / Smooth release ramp: recovery over ~5-8ms
                        float releaseStep = _fastRecovery ? 0.20f : 0.12f;
                        currentSmoothedVolume = Math.Min(targetVolume, currentSmoothedVolume + releaseStep);
                    }

                    // 4. Apply volume only when changed meaningfully
                    if (Math.Abs(currentSmoothedVolume - lastAppliedVolume) > 0.005f)
                    {
                        lastAppliedVolume = currentSmoothedVolume;
                        for (int i = 0; i < gameSessions.Count; i++)
                        {
                            try
                            {
                                gameSessions[i].volume.SetMasterVolume(currentSmoothedVolume, ref ctx);
                            }
                            catch { }
                        }
                    }
                }

                Thread.Sleep(1); // 1ms high-precision multimedia timer loop
            }

            // Restore game session volume to 1.0f on exit
            Guid resetCtx = Guid.Empty;
            for (int i = 0; i < gameSessions.Count; i++)
            {
                try
                {
                    gameSessions[i].volume.SetMasterVolume(1.0f, ref resetCtx);
                }
                catch { }
            }
        }

        private void RefreshGameSessions(ref List<(ISimpleAudioVolume volume, IAudioMeterInformation meter)> list)
        {
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                if (enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out var dev) != 0 || dev == null)
                {
                    return;
                }

                Guid iidMgr = typeof(IAudioSessionManager2).GUID;
                if (dev.Activate(ref iidMgr, 1, IntPtr.Zero, out var mgrObj) != 0 || mgrObj is not IAudioSessionManager2 mgr)
                {
                    return;
                }

                if (mgr.GetSessionEnumerator(out var sessionEnum) != 0 || sessionEnum == null)
                {
                    return;
                }

                sessionEnum.GetCount(out int count);
                var newList = new List<(ISimpleAudioVolume, IAudioMeterInformation)>();

                for (int i = 0; i < count; i++)
                {
                    if (sessionEnum.GetSession(i, out var ctl) == 0 && ctl != null)
                    {
                        if (ctl is IAudioSessionControl2 ctl2)
                        {
                            ctl2.GetProcessId(out uint pid);
                            if (pid == (uint)_targetPid)
                            {
                                if (ctl is ISimpleAudioVolume sav && ctl is IAudioMeterInformation meter)
                                {
                                    newList.Add((sav, meter));
                                }
                            }
                        }
                    }
                }

                list = newList;
            }
            catch { }
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
