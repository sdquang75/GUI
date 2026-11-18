using System;
using System.Runtime.InteropServices;

namespace GUI.Engine.Audio
{
    // Per-app output device ("App volume and device preferences") through the undocumented
    // Windows.Media.Internal.AudioPolicyConfig factory, same layout and calling convention as EarTrumpet.
    // The choice is PERSISTED by Windows per executable: it outlives the process, so whoever routes must also reset.
    public static class AudioPolicyConfig
    {
        // Plain HRESULT returns: with PreserveSig = false an `int` return would be read from a phantom [retval]
        // argument and failures would throw instead of reaching the fallback below.
        [DllImport("combase.dll", CharSet = CharSet.Unicode)]
        private static extern int WindowsCreateString(string sourceString, uint length, out IntPtr hstring);

        [DllImport("combase.dll")]
        private static extern int WindowsDeleteString(IntPtr hstring);

        [DllImport("combase.dll")]
        private static extern int RoGetActivationFactory(IntPtr activatableClassId, [In] ref Guid iid, out IntPtr factory);

        // HRESULT SetPersistedDefaultAudioEndpoint(UINT32 processId, EDataFlow flow, ERole role, HSTRING deviceId)
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate int SetPersistedDefaultAudioEndpointFunc(IntPtr thisPtr, uint pid, int flow, int role, IntPtr deviceIdHString);

        private static readonly Guid IidWin11 = new Guid("ab3d4648-e242-459f-b02f-541c70306324");
        private static readonly Guid IidWin10 = new Guid("2a59116d-6c4f-45e0-a74f-707e3fef9258");

        private const string ActivatableClassId = "Windows.Media.Internal.AudioPolicyConfig";

        // 6 IInspectable slots + 19 methods before it (identical for both IIDs).
        // Slot 27 is ClearAllPersistedApplicationDefaultEndpoints, which wipes the choice of EVERY app.
        private const int VTableSlotSetPersistedDefaultAudioEndpoint = 25;

        // The policy store wants the device interface path, not the bare MMDevice id
        private const string MmDevApiToken = @"\\?\SWD#MMDEVAPI#";
        private const string DevInterfaceAudioRender = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}";

        /// <summary>
        /// Routes a running process's audio output to a specific render endpoint (MMDevice id).
        /// </summary>
        public static bool SetAppOutputEndpoint(uint processId, string deviceId)
        {
            if (processId == 0 || string.IsNullOrEmpty(deviceId)) return false;
            return InvokeSetEndpoint(processId, deviceId);
        }

        /// <summary>
        /// Puts a running process's audio output back on the system default endpoint.
        /// Needs a live PID: Windows resolves the app from it, a PID that already exited is rejected.
        /// </summary>
        public static bool ResetAppOutputEndpoint(uint processId)
        {
            if (processId == 0) return false;
            return InvokeSetEndpoint(processId, null);
        }

        private static bool InvokeSetEndpoint(uint processId, string? deviceId)
        {
            IntPtr hClassId = IntPtr.Zero;
            IntPtr pFactory = IntPtr.Zero;
            IntPtr hDeviceId = IntPtr.Zero; // null HSTRING = "Default"

            try
            {
                int hr = WindowsCreateString(ActivatableClassId, (uint)ActivatableClassId.Length, out hClassId);
                if (hr != 0) return LogFail("WindowsCreateString(class)", hr);

                Guid targetIid = Environment.OSVersion.Version.Build >= 21390 ? IidWin11 : IidWin10;
                hr = RoGetActivationFactory(hClassId, ref targetIid, out pFactory);
                if ((hr != 0 || pFactory == IntPtr.Zero) && targetIid != IidWin10)
                {
                    targetIid = IidWin10;
                    hr = RoGetActivationFactory(hClassId, ref targetIid, out pFactory);
                }
                if (hr != 0 || pFactory == IntPtr.Zero) return LogFail("RoGetActivationFactory", hr);

                IntPtr vtable = Marshal.ReadIntPtr(pFactory);
                IntPtr pMethod = Marshal.ReadIntPtr(vtable, VTableSlotSetPersistedDefaultAudioEndpoint * IntPtr.Size);
                if (pMethod == IntPtr.Zero) return false;
                var setFunc = Marshal.GetDelegateForFunctionPointer<SetPersistedDefaultAudioEndpointFunc>(pMethod);

                if (!string.IsNullOrEmpty(deviceId))
                {
                    string path = MmDevApiToken + deviceId + DevInterfaceAudioRender;
                    hr = WindowsCreateString(path, (uint)path.Length, out hDeviceId);
                    if (hr != 0) return LogFail("WindowsCreateString(device)", hr);
                }

                int hrMultimedia = setFunc(pFactory, processId, (int)EDataFlow.eRender, (int)ERole.eMultimedia, hDeviceId);
                int hrConsole = setFunc(pFactory, processId, (int)EDataFlow.eRender, (int)ERole.eConsole, hDeviceId);
                if (hrMultimedia != 0 || hrConsole != 0)
                {
                    Program.Log($"[AudioPolicyConfig] SetPersistedDefaultAudioEndpoint(pid {processId}, {(deviceId == null ? "default" : deviceId)}) " +
                                $"-> multimedia 0x{hrMultimedia:X8}, console 0x{hrConsole:X8}");
                    return false;
                }
                return true;
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioPolicyConfig] InvokeSetEndpoint error: {ex.Message}");
                return false;
            }
            finally
            {
                if (hDeviceId != IntPtr.Zero) WindowsDeleteString(hDeviceId);
                if (pFactory != IntPtr.Zero) Marshal.Release(pFactory);
                if (hClassId != IntPtr.Zero) WindowsDeleteString(hClassId);
            }
        }

        private static bool LogFail(string step, int hr)
        {
            Program.Log($"[AudioPolicyConfig] {step} failed: 0x{hr:X8}");
            return false;
        }
    }
}
