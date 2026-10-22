using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace GUI.Utils
{
    public static class ProcessHelper
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint processAccess, bool bInheritHandle, uint processId);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool QueryFullProcessImageName(IntPtr hProcess, int flags, StringBuilder lpExeName, ref uint lpdwSize);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        // Full exe path, or "" when it cannot be read with PROCESS_QUERY_LIMITED_INFORMATION.
        // Never falls back to Process.MainModule: that opens the process with PROCESS_VM_READ, the handle
        // anti-cheats watch for, and it would fail anyway wherever the limited query is refused.
        public static string GetProcessPath(int pid)
        {
            if (pid <= 0) return "";

            IntPtr hProcess = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, (uint)pid);
            if (hProcess != IntPtr.Zero)
            {
                try
                {
                    var sb = new StringBuilder(1024);
                    uint size = (uint)sb.Capacity;
                    if (QueryFullProcessImageName(hProcess, 0, sb, ref size))
                    {
                        return sb.ToString();
                    }
                }
                finally
                {
                    CloseHandle(hProcess);
                }
            }

            return "";
        }

        public static string GetProcessExeName(int pid)
        {
            if (pid <= 0) return "";
            string path = GetProcessPath(pid);
            if (!string.IsNullOrEmpty(path))
            {
                return Path.GetFileName(path).ToLowerInvariant();
            }

            try
            {
                using var proc = Process.GetProcessById(pid);
                if (string.IsNullOrWhiteSpace(proc.ProcessName)) return "";
                return (proc.ProcessName + ".exe").ToLowerInvariant();
            }
            catch
            {
                return "";
            }
        }
    }
}
