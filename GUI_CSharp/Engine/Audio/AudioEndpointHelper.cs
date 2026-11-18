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
        public bool IsG6 { get; set; }
        public bool IsVirtualCable { get; set; }

        public override string ToString() => Name;
    }

    public static class AudioEndpointHelper
    {
        private static readonly PROPERTYKEY PkeyDeviceFriendlyName = new(new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"), 14);

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

                            bool isG6 = name.Contains("Sound BlasterX G6", StringComparison.OrdinalIgnoreCase) ||
                                        id.Contains("35862ba1-5b8c-4d8f-827b-97bbbf2d6d9f", StringComparison.OrdinalIgnoreCase);

                            bool isCable = name.Contains("CABLE Input", StringComparison.OrdinalIgnoreCase) ||
                                           name.Contains("VB-Audio", StringComparison.OrdinalIgnoreCase) ||
                                           name.Contains("Virtual Cable", StringComparison.OrdinalIgnoreCase);

                            list.Add(new AudioEndpointInfo
                            {
                                Id = id,
                                Name = name,
                                IsDefault = string.Equals(id, defaultId, StringComparison.OrdinalIgnoreCase),
                                IsG6 = isG6,
                                IsVirtualCable = isCable
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

        public static (string? renderId, string? captureId) GetVirtualCableEndpoints()
        {
            string? renderId = null;
            string? captureId = null;

            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();

                // 1. Find Playback endpoint: CABLE Input
                if (enumerator.EnumAudioEndpoints(EDataFlow.eRender, 1, out var renderCol) == 0 && renderCol != null)
                {
                    renderCol.GetCount(out int count);
                    for (int i = 0; i < count; i++)
                    {
                        if (renderCol.Item(i, out var dev) == 0 && dev != null)
                        {
                            string name = GetDeviceFriendlyName(dev);
                            if (name.Contains("CABLE Input", StringComparison.OrdinalIgnoreCase) ||
                                name.Contains("VB-Audio Virtual Cable", StringComparison.OrdinalIgnoreCase))
                            {
                                dev.GetId(out renderId);
                                break;
                            }
                        }
                    }
                }

                // 2. Find Recording endpoint: CABLE Output
                if (enumerator.EnumAudioEndpoints(EDataFlow.eCapture, 1, out var capCol) == 0 && capCol != null)
                {
                    capCol.GetCount(out int count);
                    for (int i = 0; i < count; i++)
                    {
                        if (capCol.Item(i, out var dev) == 0 && dev != null)
                        {
                            string name = GetDeviceFriendlyName(dev);
                            if (name.Contains("CABLE Output", StringComparison.OrdinalIgnoreCase) ||
                                name.Contains("VB-Audio Virtual Cable", StringComparison.OrdinalIgnoreCase) ||
                                name.Contains("CABLE", StringComparison.OrdinalIgnoreCase))
                            {
                                dev.GetId(out captureId);
                                break;
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioEndpointHelper] GetVirtualCableEndpoints error: {ex.Message}");
            }

            return (renderId, captureId);
        }

        public static string GetDeviceFriendlyName(IMMDevice dev)
        {
            try
            {
                if (dev.OpenPropertyStore(0, out var store) == 0 && store != null)
                {
                    var key = PkeyDeviceFriendlyName;
                    if (store.GetValue(ref key, out var pv) == 0 && pv.vt == 31 && pv.pwszVal != IntPtr.Zero)
                    {
                        string? name = Marshal.PtrToStringUni(pv.pwszVal);
                        if (!string.IsNullOrEmpty(name)) return name;
                    }
                }
            }
            catch { }

            dev.GetId(out string id);
            return id;
        }
    }
}
