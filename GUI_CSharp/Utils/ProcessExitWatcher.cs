using System;
using System.Runtime.InteropServices;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace GUI.Utils
{
    // Calls back once when a process exits, through a wait on its handle instead of polling the process list.
    // The handle is opened with SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION only (no VM access, nothing an
    // anti-cheat objects to), and while it is held the PID cannot be reused by another process.
    public sealed class ProcessExitWatcher : IDisposable
    {
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint processAccess, bool bInheritHandle, uint processId);

        private const uint SYNCHRONIZE = 0x00100000;
        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        private sealed class ProcessWaitHandle : WaitHandle
        {
            public ProcessWaitHandle(IntPtr handle)
            {
                SafeWaitHandle = new SafeWaitHandle(handle, ownsHandle: true);
            }
        }

        private readonly ProcessWaitHandle _handle;
        private RegisteredWaitHandle? _registration;

        public int Pid { get; }

        private ProcessExitWatcher(int pid, IntPtr handle)
        {
            Pid = pid;
            _handle = new ProcessWaitHandle(handle);
        }

        // null when the process cannot be opened, or when `pid` is no longer `expectedExe` (exited and reused)
        public static ProcessExitWatcher? TryStart(int pid, string expectedExe, Action onExit)
        {
            if (pid <= 0) return null;
            IntPtr h = OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, false, (uint)pid);
            if (h == IntPtr.Zero) return null;

            var watcher = new ProcessExitWatcher(pid, h);
            // Checked with the handle held, so the PID is pinned to the process that was checked
            if (!string.Equals(ProcessHelper.GetProcessExeName(pid), expectedExe, StringComparison.OrdinalIgnoreCase))
            {
                watcher.Dispose();
                return null;
            }

            watcher._registration = ThreadPool.RegisterWaitForSingleObject(
                watcher._handle, (_, _) => onExit(), null, Timeout.Infinite, executeOnlyOnce: true);
            return watcher;
        }

        public void Dispose()
        {
            try { _registration?.Unregister(null); } catch { }
            _registration = null;
            try { _handle.Dispose(); } catch { }
        }
    }
}
