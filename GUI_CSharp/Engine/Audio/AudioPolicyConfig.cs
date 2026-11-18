using System;
using System.Runtime.InteropServices;

namespace GUI.Engine.Audio
{
    public static class AudioPolicyConfig
    {
        [DllImport("combase.dll", PreserveSig = false)]
        private static extern int WindowsCreateString([MarshalAs(UnmanagedType.LPWStr)] string sourceString, int length, out IntPtr hstring);

        [DllImport("combase.dll", PreserveSig = false)]
        private static extern int WindowsDeleteString(IntPtr hstring);

        [DllImport("combase.dll", PreserveSig = false)]
        private static extern int RoGetActivationFactory(IntPtr activatableClassId, [In] ref Guid iid, out IntPtr factory);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate int SetEndpointFunc(IntPtr thisPtr, uint pid, int flow, int role, IntPtr devId);

        private static readonly Guid IidWin11 = new Guid("ab3d4648-e242-459f-b02f-541c70306324");
        private static readonly Guid IidWin10 = new Guid("2a59116d-6c4f-45e0-a74f-707e3fef9258");

        private const string ActivatableClassId = "Windows.Media.Internal.AudioPolicyConfig";
        private const int VTableSlotSetEndpoint = 27;

        /// <summary>
        /// Routes a running process's audio output to a specific audio endpoint device.
        /// </summary>
        public static bool SetAppOutputEndpoint(uint processId, string deviceId)
        {
            if (processId == 0 || string.IsNullOrEmpty(deviceId)) return false;
            return InvokeSetEndpoint(processId, deviceId);
        }

        /// <summary>
        /// Clears/resets a running process's audio output back to system default endpoint.
        /// </summary>
        public static bool ResetAppOutputEndpoint(uint processId)
        {
            if (processId == 0) return false;
            return InvokeSetEndpoint(processId, string.Empty);
        }

        private static bool InvokeSetEndpoint(uint processId, string deviceId)
        {
            IntPtr hstr = IntPtr.Zero;
            IntPtr pFactory = IntPtr.Zero;
            IntPtr pDeviceId = IntPtr.Zero;

            try
            {
                WindowsCreateString(ActivatableClassId, ActivatableClassId.Length, out hstr);

                Guid targetIid = Environment.OSVersion.Version.Build >= 21390 ? IidWin11 : IidWin10;
                int hr = RoGetActivationFactory(hstr, ref targetIid, out pFactory);
                if (hr != 0 || pFactory == IntPtr.Zero)
                {
                    targetIid = IidWin10;
                    hr = RoGetActivationFactory(hstr, ref targetIid, out pFactory);
                    if (hr != 0 || pFactory == IntPtr.Zero) return false;
                }

                IntPtr vtable = Marshal.ReadIntPtr(pFactory);
                IntPtr pMethod = Marshal.ReadIntPtr(vtable, VTableSlotSetEndpoint * IntPtr.Size);
                if (pMethod == IntPtr.Zero) return false;

                var setFunc = Marshal.GetDelegateForFunctionPointer<SetEndpointFunc>(pMethod);
                pDeviceId = Marshal.StringToHGlobalUni(deviceId);

                // eRender = 0, eMultimedia = 1, eConsole = 0
                int resMultimedia = setFunc(pFactory, processId, (int)EDataFlow.eRender, (int)ERole.eMultimedia, pDeviceId);
                int resConsole = setFunc(pFactory, processId, (int)EDataFlow.eRender, (int)ERole.eConsole, pDeviceId);

                return resMultimedia == 0 || resConsole == 0;
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioPolicyConfig] InvokeSetEndpoint error: {ex.Message}");
                return false;
            }
            finally
            {
                if (pDeviceId != IntPtr.Zero) Marshal.FreeHGlobal(pDeviceId);
                if (pFactory != IntPtr.Zero) Marshal.Release(pFactory);
                if (hstr != IntPtr.Zero) WindowsDeleteString(hstr);
            }
        }
    }
}
