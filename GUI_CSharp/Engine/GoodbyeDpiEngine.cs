using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

namespace GUI.Engine
{
    public static class GoodbyeDpiEngine
    {
        private const string ProcessName = "goodbyedpi";
        private const string DefaultArgs = "-5";
        private static EventWaitHandle? _stopEvent;

        private static string GetStopEventName(int parentPid) => $"Local\\GUI_GoodbyeDPI_Stop_{parentPid}";

        public static bool IsRunning()
        {
            try
            {
                var procs = Process.GetProcessesByName(ProcessName);
                bool running = procs.Length > 0;
                foreach (var p in procs) p.Dispose();
                return running;
            }
            catch
            {
                return false;
            }
        }

        public static string? ResolveExecutablePath()
        {
            // 1. Extract embedded x86_64 resources to %LocalAppData%\GUI\goodbyedpi\x86_64
            try
            {
                string extractDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "GUI", "goodbyedpi", "x86_64");
                Directory.CreateDirectory(extractDir);

                string exePath = Path.Combine(extractDir, "goodbyedpi.exe");
                ExtractResourceIfMissing("GUI.Goodbyedpi.goodbyedpi.exe", exePath);
                ExtractResourceIfMissing("GUI.Goodbyedpi.WinDivert.dll", Path.Combine(extractDir, "WinDivert.dll"));
                ExtractResourceIfMissing("GUI.Goodbyedpi.WinDivert64.sys", Path.Combine(extractDir, "WinDivert64.sys"));

                if (File.Exists(exePath))
                {
                    return exePath;
                }
            }
            catch (Exception ex)
            {
                Program.Log($"GoodbyeDpiEngine extract fallback failed: {ex.Message}");
            }

            // 2. Fallback: check local directory
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string localCandidate = Path.Combine(baseDir, "goodbyedpi.exe");
            if (File.Exists(localCandidate))
            {
                return Path.GetFullPath(localCandidate);
            }

            return null;
        }

        private static void ExtractResourceIfMissing(string resourceName, string targetPath)
        {
            if (File.Exists(targetPath)) return;
            using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(resourceName);
            if (stream == null) return;
            using var fs = new FileStream(targetPath, FileMode.Create, FileAccess.Write, FileShare.Read);
            stream.CopyTo(fs);
        }

        public static (bool Success, string Message) SetEnabled(bool enable)
        {
            int currentPid = Environment.ProcessId;

            if (enable)
            {
                EnsureStopEventInitialized(currentPid);
                if (IsRunning())
                {
                    return (true, "GoodbyeDPI is already running.");
                }
            }
            else
            {
                if (!IsRunning())
                {
                    return (true, "GoodbyeDPI is already stopped.");
                }

                // First try signaling the elevated watchdog owned by this GUI session (no UAC needed)
                if (TrySignalWatchdogStop())
                {
                    return (true, "GoodbyeDPI stopped successfully.");
                }
            }

            if (MonitorEngine.IsAdministrator())
            {
                return SetEnabledDirect(enable, currentPid);
            }

            // Elevate via self helper (--goodbyedpi-op start|stop <parentPid>) so no console window flashes
            try
            {
                string? selfExe = Environment.ProcessPath;
                if (string.IsNullOrEmpty(selfExe))
                {
                    return (false, "Could not determine application path for elevation.");
                }

                var psi = new ProcessStartInfo
                {
                    FileName = selfExe,
                    Arguments = $"--goodbyedpi-op {(enable ? "start" : "stop")} {currentPid}",
                    Verb = "runas",
                    UseShellExecute = true,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };

                using var proc = Process.Start(psi);
                if (proc == null)
                {
                    return (false, "UAC prompt was cancelled.");
                }

                proc.WaitForExit(10000);
                if (proc.ExitCode == 0)
                {
                    return (true, enable
                        ? "GoodbyeDPI started in background (Mode -5)."
                        : "GoodbyeDPI stopped successfully.");
                }
                return (false, $"Operation failed (exit code {proc.ExitCode}).");
            }
            catch (Exception ex)
            {
                return (false, $"Administrator permission required: {ex.Message}");
            }
        }

        private static void EnsureStopEventInitialized(int pid)
        {
            try
            {
                if (_stopEvent == null)
                {
                    _stopEvent = new EventWaitHandle(false, EventResetMode.ManualReset, GetStopEventName(pid));
                }
                else
                {
                    _stopEvent.Reset();
                }
            }
            catch (Exception ex)
            {
                Program.Log($"EnsureStopEventInitialized failed: {ex.Message}");
            }
        }

        private static bool TrySignalWatchdogStop()
        {
            try
            {
                if (_stopEvent != null)
                {
                    _stopEvent.Set();
                    var sw = Stopwatch.StartNew();
                    while (sw.ElapsedMilliseconds < 2500)
                    {
                        if (!IsRunning()) return true;
                        Thread.Sleep(50);
                    }
                }
            }
            catch { }
            return !IsRunning();
        }

        public static void StopOnExit()
        {
            try
            {
                if (!IsRunning())
                {
                    return;
                }

                // 1. Signal watchdog event (stops elevated goodbyedpi without UAC prompt)
                if (TrySignalWatchdogStop())
                {
                    Program.Log("GoodbyeDPI stopped via watchdog signal on exit.");
                    return;
                }

                // 2. Try killing directly (works when running as admin or same privilege)
                try
                {
                    KillAllProcessesDirect();
                }
                catch { }
            }
            catch (Exception ex)
            {
                Program.Log($"StopOnExit error: {ex.Message}");
            }
            finally
            {
                _stopEvent?.Dispose();
                _stopEvent = null;
            }
        }

        public static (bool Success, string Message) SetEnabledDirect(bool enable, int parentPid = 0)
        {
            if (enable)
            {
                if (IsRunning())
                {
                    if (parentPid > 0) SpawnWatchdog(parentPid);
                    return (true, "GoodbyeDPI is already running.");
                }

                string? exePath = ResolveExecutablePath();
                if (string.IsNullOrEmpty(exePath) || !File.Exists(exePath))
                {
                    return (false, "goodbyedpi.exe not found.");
                }

                try
                {
                    var psi = new ProcessStartInfo
                    {
                        FileName = exePath,
                        Arguments = DefaultArgs,
                        WorkingDirectory = Path.GetDirectoryName(exePath) ?? AppDomain.CurrentDomain.BaseDirectory,
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        WindowStyle = ProcessWindowStyle.Hidden
                    };

                    var proc = Process.Start(psi);
                    if (proc == null)
                    {
                        return (false, "Failed to launch goodbyedpi.exe.");
                    }

                    Thread.Sleep(250);
                    if (proc.HasExited)
                    {
                        return (false, $"goodbyedpi.exe exited immediately (code {proc.ExitCode}).");
                    }

                    if (parentPid > 0)
                    {
                        SpawnWatchdog(parentPid);
                    }

                    return (true, "GoodbyeDPI started in background (Mode -5).");
                }
                catch (Exception ex)
                {
                    return (false, $"Failed to start GoodbyeDPI: {ex.Message}");
                }
            }
            else
            {
                try
                {
                    KillAllProcessesDirect();
                    return (true, "GoodbyeDPI stopped successfully.");
                }
                catch (Exception ex)
                {
                    return (false, $"Failed to stop GoodbyeDPI: {ex.Message}");
                }
            }
        }

        private static void SpawnWatchdog(int parentPid)
        {
            try
            {
                string? selfExe = Environment.ProcessPath;
                if (string.IsNullOrEmpty(selfExe)) return;

                var psi = new ProcessStartInfo
                {
                    FileName = selfExe,
                    Arguments = $"--goodbyedpi-watch {parentPid}",
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                Process.Start(psi)?.Dispose();
            }
            catch (Exception ex)
            {
                Program.Log($"SpawnWatchdog failed: {ex.Message}");
            }
        }

        public static void RunWatchdog(int parentPid)
        {
            Program.Log($"GoodbyeDPI watchdog started for parent PID {parentPid}");
            EventWaitHandle? stopEvt = null;
            Process? parentProc = null;
            Process? dpiProc = null;

            try
            {
                try
                {
                    parentProc = Process.GetProcessById(parentPid);
                }
                catch
                {
                    // Parent already exited
                    KillAllProcessesDirect();
                    return;
                }

                while (true)
                {
                    if (stopEvt == null)
                    {
                        try
                        {
                            stopEvt = EventWaitHandle.OpenExisting(GetStopEventName(parentPid));
                        }
                        catch { }
                    }

                    if (stopEvt != null && stopEvt.WaitOne(200))
                    {
                        Program.Log("Watchdog received stop signal.");
                        break;
                    }

                    if (parentProc.WaitForExit(stopEvt == null ? 200 : 0))
                    {
                        Program.Log($"Parent GUI (PID {parentPid}) exited. Stopping GoodbyeDPI.");
                        break;
                    }

                    if (dpiProc == null || dpiProc.WaitForExit(0))
                    {
                        dpiProc?.Dispose();
                        dpiProc = null;

                        var procs = Process.GetProcessesByName(ProcessName);
                        if (procs.Length == 0)
                        {
                            Program.Log("GoodbyeDPI is no longer running. Watchdog exiting.");
                            return;
                        }

                        dpiProc = procs[0];
                        for (int i = 1; i < procs.Length; i++) procs[i].Dispose();
                    }
                }

                KillAllProcessesDirect();
            }
            catch (Exception ex)
            {
                Program.Log($"RunWatchdog exception: {ex.Message}");
                try { KillAllProcessesDirect(); } catch { }
            }
            finally
            {
                dpiProc?.Dispose();
                stopEvt?.Dispose();
                parentProc?.Dispose();
            }
        }

        private static void KillAllProcessesDirect()
        {
            var procs = Process.GetProcessesByName(ProcessName);
            foreach (var p in procs)
            {
                try
                {
                    p.Kill();
                    p.WaitForExit(3000);
                }
                finally
                {
                    p.Dispose();
                }
            }

            foreach (var svc in new[] { "WinDivert", "WinDivert1.4" })
            {
                try
                {
                    using var sc = Process.Start(new ProcessStartInfo
                    {
                        FileName = "sc.exe",
                        Arguments = $"stop {svc}",
                        UseShellExecute = false,
                        CreateNoWindow = true,
                        WindowStyle = ProcessWindowStyle.Hidden
                    });
                    sc?.WaitForExit(1500);
                }
                catch { }
            }
        }
    }
}
