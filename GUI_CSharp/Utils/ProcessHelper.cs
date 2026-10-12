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

        public static string GetProcessPath(int pid)
        {
            if (pid <= 0) return "";

            // Method 1: Win32 QueryFullProcessImageName (100% Anti-cheat safe, no Access Denied)
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

            // Method 2: Process.MainModule fallback
            try
            {
                using var proc = Process.GetProcessById(pid);
                return proc.MainModule?.FileName ?? "";
            }
            catch
            {
                return "";
            }
        }
    }
}
