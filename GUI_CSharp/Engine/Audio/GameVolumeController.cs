using System;
using System.Collections.Generic;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// "Game volume": while the game is in front the output device's master volume goes to the game level (e.g. 70%),
    /// back on the desktop it returns to what it was (e.g. 40%). One endpoint volume write each way, nothing per frame.
    ///
    /// "Keep other apps at desktop loudness": every other app on that device (Discord, browser, system sounds) gets
    /// its session volume multiplied by the dB difference, so it sounds exactly as loud as on the desktop.
    /// - Apps that start playing after the game was focused are picked up by <see cref="Rescan"/>.
    /// - A volume the user changes during the game is kept (restored relative to the change, not overwritten).
    /// - The game's own session and this app's DSP session are never touched.
    /// UI thread only (Apply / Rescan / Restore).
    /// </summary>
    public sealed class GameVolumeController
    {
        private sealed class ScaledSession
        {
            public float Original;   // desktop volume of this app
            public float Applied;    // what we last wrote
            public float Factor;     // factor Applied was computed with
        }

        private string? _deviceId;
        private float _originalScalar;
        private float _originalDb;
        private float _appliedScalar = -1f;
        private bool _active;

        private bool _keepOthers;
        private int _gamePid;
        private float _factor = 1f;
        private readonly Dictionary<string, ScaledSession> _sessions = new(StringComparer.OrdinalIgnoreCase);

        public bool IsActive => _active;

        /// <param name="deviceId">Output whose volume the game uses; null = default output.</param>
        public void Apply(string? deviceId, float gameVolume, bool keepOtherAppsLevel, int gamePid)
        {
            gameVolume = Math.Clamp(gameVolume, 0.01f, 1.0f);
            try
            {
                var dev = GetDevice(deviceId, out string resolvedId);
                if (dev == null) return;

                if (_active && !string.Equals(_deviceId, resolvedId, StringComparison.OrdinalIgnoreCase))
                {
                    Restore();
                }

                var epVol = Activate<IAudioEndpointVolume>(dev);
                if (epVol == null) return;

                if (!_active)
                {
                    epVol.GetMasterVolumeLevelScalar(out _originalScalar);
                    epVol.GetMasterVolumeLevel(out _originalDb);
                    _deviceId = resolvedId;
                    _active = true;
                    Program.Log($"[GameVolume] Desktop volume {_originalScalar:P0} saved, game volume {gameVolume:P0}");
                }

                // Re-applied on every focus / profile change: only write when the target actually changed, so a
                // volume the user picked during the game is not reset by an alt-tab
                Guid ctx = Guid.Empty;
                if (Math.Abs(_appliedScalar - gameVolume) > 0.001f)
                {
                    epVol.SetMasterVolumeLevelScalar(gameVolume, ref ctx);
                    _appliedScalar = gameVolume;
                }

                _keepOthers = keepOtherAppsLevel;
                _gamePid = gamePid;
                _factor = 1f;
                if (keepOtherAppsLevel && epVol.GetMasterVolumeLevel(out float gameDb) == 0)
                {
                    // dB read from the endpoint (the scalar is audio-tapered). > 1 when the game level is below the
                    // desktop level: others are raised back as far as their own slider allows.
                    _factor = (float)Math.Pow(10.0, (_originalDb - gameDb) / 20.0);
                }
                ScaleSessions(dev);
            }
            catch (Exception ex)
            {
                Program.Log($"[GameVolume] Apply failed: {ex.Message}");
            }
        }

        /// <summary>Scales apps that opened an audio session since the last call (call periodically while active).</summary>
        public void Rescan()
        {
            if (!_active || !_keepOthers) return;
            try
            {
                var dev = GetDevice(_deviceId, out _);
                if (dev != null) ScaleSessions(dev);
            }
            catch (Exception ex)
            {
                Program.Log($"[GameVolume] Rescan failed: {ex.Message}");
            }
        }

        public void Restore()
        {
            if (!_active) return;
            try
            {
                var dev = GetDevice(_deviceId, out _);
                if (dev != null)
                {
                    // Master down first, then the apps back up: the other order would blast them for a moment
                    var epVol = Activate<IAudioEndpointVolume>(dev);
                    if (epVol != null)
                    {
                        Guid ctx = Guid.Empty;
                        epVol.SetMasterVolumeLevelScalar(_originalScalar, ref ctx);
                        Program.Log($"[GameVolume] Desktop volume {_originalScalar:P0} restored");
                    }
                    _factor = 1f;
                    ScaleSessions(dev);
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[GameVolume] Restore failed: {ex.Message}");
            }
            finally
            {
                _active = false;
                _appliedScalar = -1f;
                _deviceId = null;
                _factor = 1f;
                _sessions.Clear();
            }
        }

        // Brings every other session on the device to Original * _factor (factor 1 = restore)
        private void ScaleSessions(IMMDevice dev)
        {
            if (_factor == 1f && _sessions.Count == 0) return; // nothing scaled, nothing to do

            var mgr = Activate<IAudioSessionManager2>(dev);
            if (mgr == null || mgr.GetSessionEnumerator(out var sessions) != 0 || sessions == null) return;

            uint self = (uint)Environment.ProcessId;
            Guid ctx = Guid.Empty;
            int changed = 0;
            sessions.GetCount(out int count);
            for (int i = 0; i < count; i++)
            {
                if (sessions.GetSession(i, out var ctl) != 0 || ctl is not IAudioSessionControl2 ctl2) continue;
                if (ctl is not ISimpleAudioVolume vol) continue;
                ctl2.GetProcessId(out uint pid);
                if (pid == self || (_gamePid > 0 && pid == (uint)_gamePid)) continue;
                if (ctl2.GetSessionInstanceIdentifier(out string key) != 0 || string.IsNullOrEmpty(key)) continue;
                if (vol.GetMasterVolume(out float current) != 0) continue;

                if (!_sessions.TryGetValue(key, out var s))
                {
                    if (_factor == 1f) continue; // never scaled
                    s = new ScaledSession { Original = current, Applied = current, Factor = 1f };
                    _sessions[key] = s;
                }
                else if (Math.Abs(current - s.Applied) > 0.002f && s.Factor > 0f)
                {
                    // The user moved this app's slider during the game: keep that choice relative to the scaling
                    s.Original = Math.Clamp(current / s.Factor, 0f, 1f);
                }

                float target = Math.Clamp(s.Original * _factor, 0f, 1f);
                if (Math.Abs(target - current) > 0.001f)
                {
                    vol.SetMasterVolume(target, ref ctx);
                    changed++;
                }
                s.Applied = target;
                s.Factor = _factor;
            }

            if (changed > 0)
            {
                Program.Log($"[GameVolume] {changed} other app session(s) set to {20 * Math.Log10(_factor):+0.0;-0.0;0} dB");
            }
        }

        private static IMMDevice? GetDevice(string? deviceId, out string resolvedId)
        {
            resolvedId = "";
            var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
            IMMDevice? dev = null;
            if (!string.IsNullOrEmpty(deviceId)) enumerator.GetDevice(deviceId, out dev);
            if (dev == null) enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out dev);
            if (dev == null) return null;
            dev.GetId(out string id);
            resolvedId = id ?? "";
            return dev;
        }

        private static T? Activate<T>(IMMDevice dev) where T : class
        {
            Guid iid = typeof(T).GUID;
            return dev.Activate(ref iid, 1, IntPtr.Zero, out var obj) == 0 ? obj as T : null;
        }
    }
}
