using System;
using System.Collections.Generic;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// Default playback / recording devices around the cable install. Installing VB-CABLE often makes "CABLE Input"
    /// the default speakers and "CABLE Output" the default microphone: every app would go silent and Discord would
    /// lose the mic. Setup takes a snapshot before and puts back anything that moved onto the cable.
    /// </summary>
    public static class AudioDefaults
    {
        private static readonly ERole[] Roles = { ERole.eConsole, ERole.eMultimedia, ERole.eCommunications };

        public sealed class Snapshot
        {
            public readonly Dictionary<(EDataFlow flow, ERole role), string> Ids = new();
        }

        public static Snapshot Take()
        {
            var snap = new Snapshot();
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                foreach (var flow in new[] { EDataFlow.eRender, EDataFlow.eCapture })
                {
                    foreach (var role in Roles)
                    {
                        if (enumerator.GetDefaultAudioEndpoint(flow, role, out var dev) == 0 && dev != null)
                        {
                            dev.GetId(out string id);
                            if (!string.IsNullOrEmpty(id)) snap.Ids[(flow, role)] = id;
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioDefaults] Snapshot failed: {ex.Message}");
            }
            return snap;
        }

        /// <summary>
        /// No default device may sit on the cable: as default speakers every app would only be heard while the game
        /// runs, and as default microphone Discord & co. would send the game's sound instead of the user's voice.
        /// Each default on the cable goes back to the device it was before Setup; if that was the cable too (an older
        /// install), to the device another role uses, else to the first connected real device. Returns how many moved.
        /// </summary>
        public static int MoveDefaultsOffCable(Snapshot before, string? cableRenderId, string? cableCaptureId)
        {
            int moved = 0;
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                var policy = (IPolicyConfig)new PolicyConfigClientComObject();
                bool IsCable(string? id) =>
                    !string.IsNullOrEmpty(id) &&
                    (string.Equals(id, cableRenderId, StringComparison.OrdinalIgnoreCase) ||
                     string.Equals(id, cableCaptureId, StringComparison.OrdinalIgnoreCase));
                bool IsActive(string? id) =>
                    !string.IsNullOrEmpty(id) && enumerator.GetDevice(id, out var d) == 0 && d != null &&
                    d.GetState(out int state) == 0 && state == 1;
                string? Current(EDataFlow flow, ERole role)
                {
                    if (enumerator.GetDefaultAudioEndpoint(flow, role, out var d) != 0 || d == null) return null;
                    d.GetId(out string id);
                    return id;
                }
                string? FirstRealDevice(EDataFlow flow)
                {
                    if (enumerator.EnumAudioEndpoints(flow, 1, out var col) != 0 || col == null) return null;
                    col.GetCount(out int n);
                    for (int i = 0; i < n; i++)
                    {
                        if (col.Item(i, out var d) != 0 || d == null) continue;
                        d.GetId(out string id);
                        string iface = AudioEndpointHelper.GetDeviceInterfaceName(d);
                        if (!IsCable(id) && !iface.Contains("VB-Audio", StringComparison.OrdinalIgnoreCase)) return id;
                    }
                    return null;
                }

                foreach (var flow in new[] { EDataFlow.eRender, EDataFlow.eCapture })
                {
                    foreach (var role in Roles)
                    {
                        if (!IsCable(Current(flow, role))) continue;

                        string? target = null;
                        if (before.Ids.TryGetValue((flow, role), out var was) && !IsCable(was) && IsActive(was))
                        {
                            target = was;
                        }
                        foreach (var other in Roles)
                        {
                            if (target != null) break;
                            string? cand = Current(flow, other);
                            if (!IsCable(cand) && IsActive(cand)) target = cand;
                            else if (before.Ids.TryGetValue((flow, other), out var prev) && !IsCable(prev) && IsActive(prev)) target = prev;
                        }
                        target ??= FirstRealDevice(flow);
                        if (target == null) continue; // nothing else is connected: leave it

                        int hr = policy.SetDefaultEndpoint(target, role);
                        Program.Log($"[AudioDefaults] Default {flow}/{role} was the cable -> {target} (0x{hr:X8})");
                        if (hr == 0) moved++;
                    }
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioDefaults] Restore failed: {ex.Message}");
            }
            return moved;
        }

        /// <summary>
        /// Switches back on a VB-Audio Virtual Cable device that was disabled in the Sound control panel (Windows then
        /// hides it from the normal device list, so it looks "not installed"). Returns how many were enabled.
        /// </summary>
        public static int EnableDisabledCable()
        {
            int enabled = 0;
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                var policy = (IPolicyConfig)new PolicyConfigClientComObject();
                const int DEVICE_STATE_ACTIVE = 0x1, DEVICE_STATE_DISABLED = 0x2;
                foreach (var flow in new[] { EDataFlow.eRender, EDataFlow.eCapture })
                {
                    // Only when this side has no working cable, and only its main endpoint (never "CABLE In 16ch",
                    // which the driver ships disabled on purpose)
                    if (AudioEndpointHelper.FindMainCable(enumerator, flow, DEVICE_STATE_ACTIVE) != null) continue;
                    string? id = AudioEndpointHelper.FindMainCable(enumerator, flow, DEVICE_STATE_DISABLED);
                    if (id == null) continue;
                    int hr = policy.SetEndpointVisibility(id, 1);
                    Program.Log($"[AudioDefaults] Disabled cable {flow} {id} -> enabled (0x{hr:X8})");
                    if (hr == 0) enabled++;
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioDefaults] Enable cable failed: {ex.Message}");
            }
            return enabled;
        }

        /// <summary>Cable Input and Output at 100% and unmuted, so the limiter sees the game at its real level.</summary>
        public static void SetCableLevels(string? cableRenderId, string? cableCaptureId)
        {
            foreach (var id in new[] { cableRenderId, cableCaptureId })
            {
                if (string.IsNullOrEmpty(id)) continue;
                try
                {
                    var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                    if (enumerator.GetDevice(id, out var dev) != 0 || dev == null) continue;
                    Guid iid = typeof(IAudioEndpointVolume).GUID;
                    if (dev.Activate(ref iid, 1, IntPtr.Zero, out var o) != 0 || o is not IAudioEndpointVolume vol) continue;
                    Guid ctx = Guid.Empty;
                    vol.SetMute(false, ref ctx);
                    vol.SetMasterVolumeLevelScalar(1.0f, ref ctx);
                }
                catch (Exception ex)
                {
                    Program.Log($"[AudioDefaults] Cable level {id}: {ex.Message}");
                }
            }
        }
    }
}
