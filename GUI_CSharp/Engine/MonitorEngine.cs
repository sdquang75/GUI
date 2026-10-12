using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;

namespace GUI.Engine
{
    public class MonitorInfo
    {
        public string InstanceId { get; set; } = "";
        public string FriendlyName { get; set; } = "";
        public bool Enabled { get; set; } = true;
        public uint DevInst { get; set; } = 0;
    }

    public static class MonitorEngine
    {
        private static readonly Guid GUID_DEVCLASS_MONITOR = new("4d36e96e-e325-11ce-bfc1-08002be10318");

        private const uint DIGCF_PRESENT = 0x00000002;
        private const uint SPDRP_DEVICEDESC = 0x00000000;
        private const uint SPDRP_FRIENDLYNAME = 0x0000000C;
        private const uint CR_SUCCESS = 0;
        private const uint CM_PROB_DISABLED = 22;
        private const uint CM_LOCATE_DEVNODE_NORMAL = 0;

        [StructLayout(LayoutKind.Sequential)]
        private struct SP_DEVINFO_DATA
        {
            public uint cbSize;
            public Guid ClassGuid;
            public uint DevInst;
            public IntPtr Reserved;
        }

        [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr SetupDiGetClassDevsW(
            ref Guid ClassGuid,
            string? Enumerator,
            IntPtr hwndParent,
            uint Flags);

        [DllImport("setupapi.dll", SetLastError = true)]
        private static extern bool SetupDiEnumDeviceInfo(
            IntPtr DeviceInfoSet,
            uint MemberIndex,
            ref SP_DEVINFO_DATA DeviceInfoData);

        [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool SetupDiGetDeviceInstanceIdW(
            IntPtr DeviceInfoSet,
            ref SP_DEVINFO_DATA DeviceInfoData,
            StringBuilder? DeviceInstanceId,
            uint DeviceInstanceIdSize,
            out uint RequiredSize);

        [DllImport("setupapi.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool SetupDiGetDeviceRegistryPropertyW(
            IntPtr DeviceInfoSet,
            ref SP_DEVINFO_DATA DeviceInfoData,
            uint Property,
            out uint PropertyRegDataType,
            byte[]? PropertyBuffer,
            uint PropertyBufferSize,
            out uint RequiredSize);

        [DllImport("setupapi.dll", SetLastError = true)]
        private static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        private static extern uint CM_Get_DevNode_Status(
            out uint pulStatus,
            out uint pulProblemNumber,
            uint dnDevInst,
            uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern uint CM_Locate_DevNodeW(
            out uint pdnDevInst,
            string pDeviceID,
            uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        private static extern uint CM_Enable_DevNode(uint dnDevInst, uint ulFlags);

        [DllImport("cfgmgr32.dll", SetLastError = true)]
        private static extern uint CM_Disable_DevNode(uint dnDevInst, uint ulFlags);

        public static bool IsAdministrator()
        {
            try
            {
                using var identity = WindowsIdentity.GetCurrent();
                var principal = new WindowsPrincipal(identity);
                return principal.IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch
            {
                return false;
            }
        }

        public static List<MonitorInfo> EnumerateMonitors()
        {
            var list = new List<MonitorInfo>();
            Guid guid = GUID_DEVCLASS_MONITOR;
            IntPtr hDevInfo = SetupDiGetClassDevsW(ref guid, null, IntPtr.Zero, DIGCF_PRESENT);
            if (hDevInfo == IntPtr.Zero || hDevInfo == new IntPtr(-1))
            {
                return list;
            }

            try
            {
                uint index = 0;
                var devInfo = new SP_DEVINFO_DATA();
                devInfo.cbSize = (uint)Marshal.SizeOf<SP_DEVINFO_DATA>();

                while (SetupDiEnumDeviceInfo(hDevInfo, index, ref devInfo))
                {
                    // 1. Get Instance ID
                    SetupDiGetDeviceInstanceIdW(hDevInfo, ref devInfo, null, 0, out uint reqSize);
                    string instanceId = "";
                    if (reqSize > 0)
                    {
                        var sb = new StringBuilder((int)reqSize);
                        if (SetupDiGetDeviceInstanceIdW(hDevInfo, ref devInfo, sb, reqSize, out _))
                        {
                            instanceId = sb.ToString();
                        }
                    }

                    // 2. Get Friendly Name or Description
                    string friendlyName = GetDevicePropertyString(hDevInfo, ref devInfo, SPDRP_FRIENDLYNAME);
                    if (string.IsNullOrWhiteSpace(friendlyName))
                    {
                        friendlyName = GetDevicePropertyString(hDevInfo, ref devInfo, SPDRP_DEVICEDESC);
                    }
                    if (string.IsNullOrWhiteSpace(friendlyName))
                    {
                        friendlyName = instanceId;
                    }

                    // 3. Check Enabled Status
                    bool enabled = true;
                    if (CM_Get_DevNode_Status(out uint _, out uint probNum, devInfo.DevInst, 0) == CR_SUCCESS)
                    {
                        enabled = (probNum != CM_PROB_DISABLED);
                    }

                    list.Add(new MonitorInfo
                    {
                        InstanceId = instanceId,
                        FriendlyName = friendlyName,
                        Enabled = enabled,
                        DevInst = devInfo.DevInst
                    });

                    index++;
                    devInfo = new SP_DEVINFO_DATA();
                    devInfo.cbSize = (uint)Marshal.SizeOf<SP_DEVINFO_DATA>();
                }
            }
            finally
            {
                SetupDiDestroyDeviceInfoList(hDevInfo);
            }

            return list;
        }

        private static string GetDevicePropertyString(IntPtr hDevInfo, ref SP_DEVINFO_DATA devInfo, uint property)
        {
            SetupDiGetDeviceRegistryPropertyW(hDevInfo, ref devInfo, property, out _, null, 0, out uint reqSize);
            if (reqSize > 0)
            {
                byte[] buffer = new byte[reqSize];
                if (SetupDiGetDeviceRegistryPropertyW(hDevInfo, ref devInfo, property, out _, buffer, reqSize, out _))
                {
                    return Encoding.Unicode.GetString(buffer).TrimEnd('\0');
                }
            }
            return "";
        }

        public static (bool Success, string Message) SetMonitorEnabled(string instanceId, bool enable)
        {
            if (string.IsNullOrWhiteSpace(instanceId))
            {
                return (false, "Invalid instance ID.");
            }

            if (IsAdministrator())
            {
                return SetDevNodeEnabledDirect(instanceId, enable);
            }

            // Launch self with elevation
            try
            {
                string? exePath = Environment.ProcessPath;
                if (string.IsNullOrEmpty(exePath))
                {
                    return (false, "Could not determine application path for elevation.");
                }

                var psi = new ProcessStartInfo
                {
                    FileName = exePath,
                    Arguments = $"--monitor-op {(enable ? "enable" : "disable")} \"{instanceId}\"",
                    Verb = "runas",
                    UseShellExecute = true,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };

                using var proc = Process.Start(psi);
                if (proc == null) return (false, "UAC prompt was cancelled.");

                proc.WaitForExit(10000);
                if (proc.ExitCode == 0)
                {
                    return (true, $"Monitor {(enable ? "enabled" : "disabled")} successfully.");
                }
                return (false, $"Operation failed with exit code {proc.ExitCode}.");
            }
            catch (Exception ex)
            {
                return (false, $"Elevation request failed: {ex.Message}");
            }
        }

        public static (bool Success, string Message) SetDevNodeEnabledDirect(string instanceId, bool enable)
        {
            uint cr = CM_Locate_DevNodeW(out uint devInst, instanceId, CM_LOCATE_DEVNODE_NORMAL);
            if (cr != CR_SUCCESS)
            {
                return (false, $"Could not locate device node (CR code {cr}).");
            }

            cr = enable ? CM_Enable_DevNode(devInst, 0) : CM_Disable_DevNode(devInst, 0);
            if (cr == CR_SUCCESS)
            {
                return (true, $"Monitor {(enable ? "enabled" : "disabled")} successfully.");
            }
            return (false, $"Failed to {(enable ? "enable" : "disable")} device (CR code {cr}).");
        }
    }
}
