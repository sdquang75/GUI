using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace GUI.Engine.Audio
{
    public class AudioEndpointInfo
    {
        public string Id { get; set; } = string.Empty;
        public string Name { get; set; } = string.Empty;
        public bool IsDefault { get; set; }
        public bool IsVirtualCable { get; set; }

        public override string ToString() => Name;
    }

    public static class AudioEndpointHelper
    {
        // Name of the driver interface shared by "CABLE Input" and "CABLE Output". Unlike the device name it does not
        // change when the user renames the device, and it is not shared with VB-Audio's other cables (A/B, Hi-Fi).
        public const string CableInterfaceName = "VB-Audio Virtual Cable";

        private static readonly PROPERTYKEY PkeyDeviceFriendlyName = new(new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), 14);
        private static readonly PROPERTYKEY PkeyDeviceInterfaceName = new(new Guid("b3f8fa53-0004-438e-9003-51a46e139bfc"), 6);
        private static readonly PROPERTYKEY PkeyDeviceInterfaceFriendlyName = new(new Guid("026e516e-b814-414b-83cd-856d6fef4822"), 2);

        public static List<AudioEndpointInfo> GetRenderEndpoints()
        {
            var list = new List<AudioEndpointInfo>();
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                string? defaultId = null;
                if (enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out var defDev) == 0 && defDev != null)
                {
                    defDev.GetId(out defaultId);
                }

                if (enumerator.EnumAudioEndpoints(EDataFlow.eRender, 1, out var col) == 0 && col != null)
                {
                    col.GetCount(out int count);
                    for (int i = 0; i < count; i++)
                    {
                        if (col.Item(i, out var dev) == 0 && dev != null)
                        {
                            dev.GetId(out string id);
                            string name = GetDeviceFriendlyName(dev);
                            list.Add(new AudioEndpointInfo
                            {
                                Id = id,
                                Name = name,
                                IsDefault = string.Equals(id, defaultId, StringComparison.OrdinalIgnoreCase),
                                IsVirtualCable = IsVirtualCable(dev, name)
                            });
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioEndpointHelper] GetRenderEndpoints error: {ex.Message}");
            }
            return list;
        }

        // Any virtual cable (ours, VB-Audio's others, similar drivers): never a valid DSP output, since anything played
        // into a cable has to be read back by something
        private static bool IsVirtualCable(IMMDevice dev, string name)
        {
            string iface = GetDeviceInterfaceName(dev);
            return iface.Contains("VB-Audio", StringComparison.OrdinalIgnoreCase) ||
                   iface.Contains("Virtual Cable", StringComparison.OrdinalIgnoreCase) ||
                   name.Contains("CABLE Input", StringComparison.OrdinalIgnoreCase) ||
                   name.Contains("Virtual Cable", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// Physical output the DSP renders to: the chosen device if it is active and not a cable; otherwise whatever
        /// Windows uses as default output (so it follows headset / speaker switches), otherwise any active output.
        /// Null if only cables exist.
        /// </summary>
        public static string? ResolveDspOutputDeviceId(string? preferredId, string? cableRenderId)
        {
            var endpoints = GetRenderEndpoints();
            bool Usable(AudioEndpointInfo e) =>
                !e.IsVirtualCable && !string.Equals(e.Id, cableRenderId, StringComparison.OrdinalIgnoreCase);

            if (!string.IsNullOrEmpty(preferredId))
            {
                var preferred = endpoints.Find(e => string.Equals(e.Id, preferredId, StringComparison.OrdinalIgnoreCase));
                if (preferred != null && Usable(preferred)) return preferred.Id;
                Program.Log(preferred == null
                    ? $"[AudioEndpointHelper] Chosen output {preferredId} is not connected -> Windows default output"
                    : $"[AudioEndpointHelper] Chosen output '{preferred.Name}' is a virtual cable (feedback loop) -> Windows default output");
            }

            return (endpoints.Find(e => e.IsDefault && Usable(e)) ?? endpoints.Find(Usable))?.Id;
        }

        /// <summary>
        /// Anything on the cable that colors or levels the game before the limiter sees it: Windows Loudness
        /// Equalization, a cable volume below 100% or a muted cable. Null when clean. Setup fixes all of these.
        /// </summary>
        public static string? GetCableInputIssue(string? cableRenderId, string? cableCaptureId = null)
        {
            if (string.IsNullOrEmpty(cableRenderId)) return null;
            try
            {
                int brace = cableRenderId.LastIndexOf('{');
                if (brace >= 0)
                {
                    string guid = cableRenderId.Substring(brace);
                    using var fx = Microsoft.Win32.Registry.LocalMachine.OpenSubKey(
                        $@"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\{guid}\FxProperties");
                    // PKEY_AudioEndpoint_LoudnessEqualization enable flag (VT_BOOL, value at byte 8)
                    if (fx?.GetValue("{fc52a749-4be9-4510-896e-966ba6525980},3") is byte[] le && le.Length >= 10 &&
                        (le[8] != 0 || le[9] != 0))
                    {
                        return "Loudness EQ is ON on the cable";
                    }
                    // Effect processors (APOs) attached to the cable: {d04e05a6-...},1..6 hold their CLSIDs
                    if (fx != null)
                    {
                        foreach (string n in fx.GetValueNames())
                        {
                            if (n.StartsWith("{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},", StringComparison.OrdinalIgnoreCase) &&
                                !n.EndsWith(",0") && fx.GetValue(n) is string clsid &&
                                !string.IsNullOrEmpty(clsid) && clsid != "{00000000-0000-0000-0000-000000000000}")
                            {
                                return "Windows enhancements are attached to the cable";
                            }
                        }
                    }
                }

                string? Level(string? id, string side)
                {
                    if (string.IsNullOrEmpty(id)) return null;
                    var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                    if (enumerator.GetDevice(id, out var dev) != 0 || dev == null) return null;
                    Guid iid = typeof(IAudioEndpointVolume).GUID;
                    if (dev.Activate(ref iid, 1, IntPtr.Zero, out var o) != 0 || o is not IAudioEndpointVolume vol) return null;
                    if (vol.GetMute(out bool muted) == 0 && muted) return $"CABLE {side} is muted";
                    if (vol.GetMasterVolumeLevelScalar(out float level) == 0 && level < 0.99f) return $"CABLE {side} at {level:P0}";
                    return null;
                }
                return Level(cableRenderId, "Input") ?? Level(cableCaptureId, "Output");
            }
            catch { }
            return null;
        }

        /// <summary>Render devices on which this process has a running (active) audio stream right now.</summary>
        public static List<string> GetDevicesPlayingFor(int pid)
        {
            var result = new List<string>();
            if (pid <= 0) return result;
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                if (enumerator.EnumAudioEndpoints(EDataFlow.eRender, 1, out var col) != 0 || col == null) return result;
                col.GetCount(out int count);
                Guid iidMgr = typeof(IAudioSessionManager2).GUID;
                for (int i = 0; i < count; i++)
                {
                    if (col.Item(i, out var dev) != 0 || dev == null) continue;
                    if (dev.Activate(ref iidMgr, 1, IntPtr.Zero, out var o) != 0 || o is not IAudioSessionManager2 mgr) continue;
                    if (mgr.GetSessionEnumerator(out var sessions) != 0 || sessions == null) continue;
                    sessions.GetCount(out int n);
                    for (int k = 0; k < n; k++)
                    {
                        if (sessions.GetSession(k, out var ctl) != 0 || ctl is not IAudioSessionControl2 ctl2) continue;
                        ctl2.GetProcessId(out uint p);
                        // AudioSessionStateActive = 1
                        if (p == (uint)pid && ctl2.GetState(out int state) == 0 && state == 1)
                        {
                            dev.GetId(out string id);
                            result.Add(id);
                            break;
                        }
                    }
                }
            }
            catch { }
            return result;
        }

        /// <summary>
        /// "CABLE Input" (playback) and "CABLE Output" (recording) of VB-Audio Virtual Cable, found by the driver's
        /// interface name so renamed devices still match. Active devices only.
        /// </summary>
        public static (string? renderId, string? captureId) GetVirtualCableEndpoints()
        {
            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                return (FindMainCable(enumerator, EDataFlow.eRender, 1), FindMainCable(enumerator, EDataFlow.eCapture, 1));
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioEndpointHelper] GetVirtualCableEndpoints error: {ex.Message}");
                return (null, null);
            }
        }

        /// <summary>
        /// The cable's main endpoint for a direction among devices in <paramref name="stateMask"/>. The driver also
        /// creates "CABLE In 16ch" (a 16-channel playback side, disabled by default): it is never picked, and a device
        /// named "CABLE Input" / "CABLE Output" wins over a renamed one.
        /// </summary>
        public static string? FindMainCable(IMMDeviceEnumerator enumerator, EDataFlow flow, int stateMask)
        {
            if (enumerator.EnumAudioEndpoints(flow, stateMask, out var col) != 0 || col == null) return null;
            col.GetCount(out int count);
            string? fallback = null;
            string wantedName = flow == EDataFlow.eRender ? "CABLE Input" : "CABLE Output";
            for (int i = 0; i < count; i++)
            {
                if (col.Item(i, out var dev) != 0 || dev == null) continue;
                if (!string.Equals(GetDeviceInterfaceName(dev), CableInterfaceName, StringComparison.OrdinalIgnoreCase)) continue;
                string name = GetDeviceFriendlyName(dev);
                if (name.Contains("16ch", StringComparison.OrdinalIgnoreCase)) continue;
                dev.GetId(out string id);
                if (name.Contains(wantedName, StringComparison.OrdinalIgnoreCase)) return id;
                fallback ??= id;
            }
            return fallback;
        }

        public static string GetDeviceFriendlyName(IMMDevice dev)
        {
            string? name = ReadString(dev, PkeyDeviceFriendlyName);
            if (!string.IsNullOrEmpty(name)) return name;
            dev.GetId(out string id);
            return id;
        }

        // Driver interface name ("VB-Audio Virtual Cable", "Sound BlasterX G6", "Realtek(R) Audio"...)
        public static string GetDeviceInterfaceName(IMMDevice dev) =>
            ReadString(dev, PkeyDeviceInterfaceName) ?? ReadString(dev, PkeyDeviceInterfaceFriendlyName) ?? "";

        private static string? ReadString(IMMDevice dev, PROPERTYKEY key)
        {
            try
            {
                if (dev.OpenPropertyStore(0, out var store) == 0 && store != null &&
                    store.GetValue(ref key, out var pv) == 0 && pv.vt == 31 && pv.pwszVal != IntPtr.Zero)
                {
                    string? s = Marshal.PtrToStringUni(pv.pwszVal);
                    if (!string.IsNullOrEmpty(s)) return s;
                }
            }
            catch { }
            return null;
        }
    }
}
