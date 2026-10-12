using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace GUI.Utils
{
    public class ActiveWindowObserver : IDisposable
    {
        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll", SetLastError = true)]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint processAccess, bool bInheritHandle, uint processId);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool QueryFullProcessImageName(IntPtr hProcess, int flags, StringBuilder lpExeName, ref uint lpdwSize);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        private CancellationTokenSource? _cts;
        private Task? _monitorTask;
        private string? _lastProcessExe;

        public event Action<string?>? ForegroundAppChanged;
        public event Action? PollTick;

        public bool IsRunning => _monitorTask != null && !_cts!.IsCancellationRequested;

        public void Start(int intervalMs = 200)
        {
            if (IsRunning) return;

            _cts = new CancellationTokenSource();
            _monitorTask = Task.Run(async () =>
            {
                var token = _cts.Token;
                while (!token.IsCancellationRequested)
                {
                    try
                    {
                        PollTick?.Invoke();

                        string? currentExe = GetForegroundProcessExe();
                        if (!string.Equals(currentExe, _lastProcessExe, StringComparison.OrdinalIgnoreCase))
                        {
                            _lastProcessExe = currentExe;
                            ForegroundAppChanged?.Invoke(currentExe);
                        }
                    }
                    catch { }

                    await Task.Delay(intervalMs, token).ConfigureAwait(false);
                }
            }, _cts.Token);
        }

        public void Stop()
        {
            _cts?.Cancel();
            try { _monitorTask?.Wait(500); } catch { }
            _monitorTask = null;
        }

        private string? GetForegroundProcessExe()
        {
            IntPtr hwnd = GetForegroundWindow();
            if (hwnd == IntPtr.Zero) return null;

            uint pid;
            GetWindowThreadProcessId(hwnd, out pid);
            if (pid == 0) return null;

            // Method 1: QueryFullProcessImageName with PROCESS_QUERY_LIMITED_INFORMATION (100% Anti-cheat safe)
            IntPtr hProcess = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
            if (hProcess != IntPtr.Zero)
            {
                try
                {
                    StringBuilder sb = new StringBuilder(1024);
                    uint size = (uint)sb.Capacity;
                    if (QueryFullProcessImageName(hProcess, 0, sb, ref size))
                    {
                        return Path.GetFileName(sb.ToString()).ToLowerInvariant();
                    }
                }
                finally
                {
                    CloseHandle(hProcess);
                }
            }

            // Method 2: Process.GetProcessById fallback
            try
            {
                using var proc = Process.GetProcessById((int)pid);
                return (proc.ProcessName + ".exe").ToLowerInvariant();
            }
            catch
            {
                return null;
            }
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
