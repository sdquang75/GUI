using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;

namespace GUI.Engine
{
    public sealed class GoodbyeDpiPreset
    {
        public string Id { get; init; } = "";
        public string DisplayName { get; init; } = "";
        public string ShortLabel { get; init; } = "";
        public string Arguments { get; init; } = "";
        public string Description { get; init; } = "";

        public override string ToString() => DisplayName;
    }

    public static class GoodbyeDpiEngine
    {
        private const string ProcessName = "goodbyedpi";
        public const string DefaultModeId = "git_safe_reverse";

        public static readonly IReadOnlyList<GoodbyeDpiPreset> Presets = new List<GoodbyeDpiPreset>
        {
            new GoodbyeDpiPreset
            {
                Id = "git_safe_reverse",
                DisplayName = "Git-Safe Reverse Frag (Recommended — No Fake TTL)",
                ShortLabel = "Git-Safe Reverse",
                Arguments = "-f 2 -e 2 --reverse-frag --max-payload",
                Description = "• Splits HTTP/HTTPS packets (2-byte reverse fragmentation) without sending Fake Request packets.\n• Bypasses ISP SNI blocks while keeping git push (HTTPS), SSH, and dev tools working."
            },
            new GoodbyeDpiPreset
            {
                Id = "git_safe_native",
                DisplayName = "Git-Safe Native Frag (Most Stable for Dev / Git)",
                ShortLabel = "Git-Safe Native",
                Arguments = "-f 2 -e 2 --native-frag --max-payload",
                Description = "• Uses standard in-order TCP fragmentation (--native-frag) with no Fake Request packets.\n• Maximum compatibility with strict TLS servers, GitHub git push, and API endpoints."
            },
            new GoodbyeDpiPreset
            {
                Id = "mode_5",
                DisplayName = "Mode -5 (2_any_country — Auto TTL Fake Packet)",
                ShortLabel = "Mode -5",
                Arguments = "-5",
                Description = "• Standard GoodbyeDPI default (-f 2 -e 2 --auto-ttl --reverse-frag --max-payload).\n• Strong DPI bypass, but --auto-ttl fake packets may reset git push (HTTPS) connections."
            },
            new GoodbyeDpiPreset
            {
                Id = "mode_6",
                DisplayName = "Mode -6 (Modern — Wrong SEQ Fake Packet)",
                ShortLabel = "Mode -6",
                Arguments = "-6",
                Description = "• Sends Fake Requests with past TCP SEQ/ACK (--wrong-seq) instead of --auto-ttl.\n• Useful when Mode -5 fails on certain routes or ISP equipment."
            },
            new GoodbyeDpiPreset
            {
                Id = "mode_1",
                DisplayName = "Mode -1 (Legacy — Most Compatible)",
                ShortLabel = "Mode -1",
                Arguments = "-1",
                Description = "• Classic mode (-p -r -s -f 2 -k 2 -n -e 2) without Fake Request packets.\n• Compatible with older routers and safe for git push."
            },
            new GoodbyeDpiPreset
            {
                Id = "mode_2",
                DisplayName = "Mode -2 (Legacy — Faster HTTPS)",
                ShortLabel = "Mode -2",
                Arguments = "-2",
                Description = "• Classic mode with 40-byte HTTPS fragmentation (-p -r -s -f 2 -k 2 -n -e 40).\n• Faster HTTPS handshake and safe for Git / Terminal."
            },
            new GoodbyeDpiPreset
            {
                Id = "git_safe_dns",
                DisplayName = "Git-Safe Reverse + DNS Redirect (Yandex DNS)",
                ShortLabel = "Git-Safe + DNS",
                Arguments = "-f 2 -e 2 --reverse-frag --max-payload --dns-addr 77.88.8.8 --dns-port 1253 --dnsv6-addr 2a02:6b8::feed:0ff --dnsv6-port 1253",
                Description = "• Combines Git-Safe Reverse Frag with non-standard port DNS redirection (77.88.8.8:1253).\n• Bypasses both ISP DNS poisoning and SNI inspection without breaking git push."
            },
            new GoodbyeDpiPreset
            {
                Id = "mode_5_dns",
                DisplayName = "Mode -5 + DNS Redirect (2_any_country_dnsredir)",
                ShortLabel = "Mode -5 + DNS",
                Arguments = "-5 --dns-addr 77.88.8.8 --dns-port 1253 --dnsv6-addr 2a02:6b8::feed:0ff --dnsv6-port 1253",
                Description = "• Full Mode -5 with Yandex DNS redirection on port 1253.\n• Bypasses strict DNS + DPI blocks (note: --auto-ttl may affect git push over HTTPS)."
            }
        };

        private static EventWaitHandle? _stopEvent;
        private static EventWaitHandle? _reloadEvent;

        private static string GetStopEventName(int parentPid) => $"Local\\GUI_GoodbyeDPI_Stop_{parentPid}";
        private static string GetReloadEventName(int parentPid) => $"Local\\GUI_GoodbyeDPI_Reload_{parentPid}";

        private static string GetActiveModeFilePath()
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "GUI", "goodbyedpi");
            Directory.CreateDirectory(dir);
            return Path.Combine(dir, "active_mode.txt");
        }

        public static GoodbyeDpiPreset GetPreset(string? modeId)
        {
            if (!string.IsNullOrWhiteSpace(modeId))
            {
                foreach (var p in Presets)
                {
                    if (string.Equals(p.Id, modeId.Trim(), StringComparison.OrdinalIgnoreCase))
                    {
                        return p;
                    }
                }
            }
            return Presets[0];
        }

        public static void SaveActiveModeId(string modeId)
        {
            try
            {
                File.WriteAllText(GetActiveModeFilePath(), GetPreset(modeId).Id);
            }
            catch { }
        }

        public static GoodbyeDpiPreset GetActivePreset()
        {
            try
            {
                string path = GetActiveModeFilePath();
                if (File.Exists(path))
                {
                    string id = File.ReadAllText(path).Trim();
                    return GetPreset(id);
                }
            }
            catch { }
            return Presets[0];
        }

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

        public static (bool Success, string Message) SetEnabled(bool enable, string? modeId = null)
        {
            int currentPid = Environment.ProcessId;
            var preset = GetPreset(modeId);

            if (enable)
            {
                SaveActiveModeId(preset.Id);
                EnsureEventsInitialized(currentPid);
                if (IsRunning())
                {
                    return (true, $"GoodbyeDPI is already running ({preset.ShortLabel}).");
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
                return SetEnabledDirect(enable, currentPid, preset.Id);
            }

            // Elevate via self helper (--goodbyedpi-op start|stop <parentPid> <modeId>) so no console window flashes
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
                    Arguments = $"--goodbyedpi-op {(enable ? "start" : "stop")} {currentPid} {preset.Id}",
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

                if (!proc.WaitForExit(30000))
                {
                    return (false, "Operation timed out waiting for elevated helper.");
                }

                if (proc.ExitCode == 0)
                {
                    return (true, enable
                        ? $"GoodbyeDPI started ({preset.ShortLabel})."
                        : "GoodbyeDPI stopped successfully.");
                }
                return (false, $"Operation failed (exit code {proc.ExitCode}).");
            }
            catch (System.ComponentModel.Win32Exception wEx) when (wEx.NativeErrorCode == 1223)
            {
                return (false, "UAC prompt was cancelled.");
            }
            catch (Exception ex)
            {
                return (false, $"Administrator permission required: {ex.Message}");
            }
        }

        public static (bool Success, string Message) SwitchModeIfRunning(string modeId)
        {
            var preset = GetPreset(modeId);
            SaveActiveModeId(preset.Id);

            if (!IsRunning())
            {
                return (true, $"Ready ({preset.ShortLabel})");
            }

            int currentPid = Environment.ProcessId;
            EnsureEventsInitialized(currentPid);

            // 1. Signal the elevated watchdog to reload goodbyedpi.exe with the new mode (no UAC prompt)
            if (TrySignalWatchdogReload())
            {
                return (true, $"Switched active mode to {preset.ShortLabel}.");
            }

            // 2. Fallback if running as admin directly
            if (MonitorEngine.IsAdministrator())
            {
                KillGoodbyeDpiProcessOnly();
                return SetEnabledDirect(true, currentPid, preset.Id);
            }

            return (false, "Restart GoodbyeDPI to apply the new mode.");
        }

        private static void EnsureEventsInitialized(int pid)
        {
            try
            {
                if (_stopEvent == null)
                {
                    _stopEvent = new EventWaitHandle(false, EventResetMode.ManualReset, GetStopEventName(pid));
                }
                _stopEvent.Reset();

                if (_reloadEvent == null)
                {
                    _reloadEvent = new EventWaitHandle(false, EventResetMode.AutoReset, GetReloadEventName(pid));
                }
                _reloadEvent.Reset();
            }
            catch (Exception ex)
            {
                Program.Log($"EnsureEventsInitialized failed: {ex.Message}");
            }
        }

        private static HashSet<int> GetRunningProcessIds()
        {
            var pids = new HashSet<int>();
            try
            {
                var procs = Process.GetProcessesByName(ProcessName);
                foreach (var p in procs)
                {
                    try { pids.Add(p.Id); } catch { }
                    finally { p.Dispose(); }
                }
            }
            catch { }
            return pids;
        }

        private static bool TrySignalWatchdogReload()
        {
            try
            {
                if (_reloadEvent == null)
                {
                    try
                    {
                        _reloadEvent = EventWaitHandle.OpenExisting(GetReloadEventName(Environment.ProcessId));
                    }
                    catch
                    {
                        try
                        {
                            _reloadEvent = new EventWaitHandle(false, EventResetMode.AutoReset, GetReloadEventName(Environment.ProcessId));
                        }
                        catch { }
                    }
                }

                if (_reloadEvent != null)
                {
                    var oldPids = GetRunningProcessIds();
                    _reloadEvent.Set();

                    var sw = Stopwatch.StartNew();
                    while (sw.ElapsedMilliseconds < 4000)
                    {
                        Thread.Sleep(50);
                        var currentPids = GetRunningProcessIds();
                        if (currentPids.Count > 0 && !currentPids.Overlaps(oldPids))
                        {
                            Thread.Sleep(250);
                            return IsRunning();
                        }
                    }
                    return IsRunning();
                }
            }
            catch { }
            return false;
        }

        private static bool TrySignalWatchdogStop()
        {
            try
            {
                if (_stopEvent == null)
                {
                    try
                    {
                        _stopEvent = EventWaitHandle.OpenExisting(GetStopEventName(Environment.ProcessId));
                    }
                    catch
                    {
                        try
                        {
                            _stopEvent = new EventWaitHandle(false, EventResetMode.ManualReset, GetStopEventName(Environment.ProcessId));
                        }
                        catch { }
                    }
                }

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
                _reloadEvent?.Dispose();
                _reloadEvent = null;
            }
        }

        private static bool StartGoodbyeDpiProcess(GoodbyeDpiPreset preset, out string errorMessage)
        {
            errorMessage = "";
            string? exePath = ResolveExecutablePath();
            if (string.IsNullOrEmpty(exePath) || !File.Exists(exePath))
            {
                errorMessage = "goodbyedpi.exe not found.";
                return false;
            }

            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = exePath,
                    Arguments = preset.Arguments,
                    WorkingDirectory = Path.GetDirectoryName(exePath) ?? AppDomain.CurrentDomain.BaseDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };

                using var proc = Process.Start(psi);
                if (proc == null)
                {
                    errorMessage = "Failed to launch goodbyedpi.exe.";
                    return false;
                }

                Thread.Sleep(250);
                if (proc.HasExited)
                {
                    errorMessage = $"goodbyedpi.exe exited immediately (code {proc.ExitCode}).";
                    return false;
                }

                return true;
            }
            catch (Exception ex)
            {
                errorMessage = $"Failed to start GoodbyeDPI: {ex.Message}";
                return false;
            }
        }

        public static (bool Success, string Message) SetEnabledDirect(bool enable, int parentPid = 0, string? modeId = null)
        {
            var preset = string.IsNullOrWhiteSpace(modeId) ? GetActivePreset() : GetPreset(modeId);

            if (enable)
            {
                SaveActiveModeId(preset.Id);

                if (IsRunning())
                {
                    if (parentPid > 0) SpawnWatchdog(parentPid);
                    return (true, $"GoodbyeDPI is already running ({preset.ShortLabel}).");
                }

                if (!StartGoodbyeDpiProcess(preset, out string err))
                {
                    return (false, err);
                }

                if (parentPid > 0)
                {
                    SpawnWatchdog(parentPid);
                }

                return (true, $"GoodbyeDPI started ({preset.ShortLabel}).");
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
            EventWaitHandle? reloadEvt = null;
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

                    if (reloadEvt == null)
                    {
                        try
                        {
                            reloadEvt = EventWaitHandle.OpenExisting(GetReloadEventName(parentPid));
                        }
                        catch { }
                    }

                    if (stopEvt != null && reloadEvt != null)
                    {
                        int signaled = WaitHandle.WaitAny(new WaitHandle[] { stopEvt, reloadEvt }, 200);
                        if (signaled == 0)
                        {
                            Program.Log("Watchdog received stop signal.");
                            break;
                        }
                        if (signaled == 1)
                        {
                            var newPreset = GetActivePreset();
                            Program.Log($"Watchdog received reload signal. Switching to mode '{newPreset.Id}' ({newPreset.Arguments}).");
                            dpiProc?.Dispose();
                            dpiProc = null;
                            KillGoodbyeDpiProcessOnly();
                            StartGoodbyeDpiProcess(newPreset, out _);
                            continue;
                        }
                    }
                    else
                    {
                        if (stopEvt != null && stopEvt.WaitOne(200))
                        {
                            Program.Log("Watchdog received stop signal.");
                            break;
                        }

                        if (reloadEvt != null && reloadEvt.WaitOne(0))
                        {
                            var newPreset = GetActivePreset();
                            Program.Log($"Watchdog received reload signal. Switching to mode '{newPreset.Id}' ({newPreset.Arguments}).");
                            dpiProc?.Dispose();
                            dpiProc = null;
                            KillGoodbyeDpiProcessOnly();
                            StartGoodbyeDpiProcess(newPreset, out _);
                            continue;
                        }
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
                            break;
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
                reloadEvt?.Dispose();
                parentProc?.Dispose();
            }
        }

        private static void KillGoodbyeDpiProcessOnly()
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
        }

        private static void KillAllProcessesDirect()
        {
            KillGoodbyeDpiProcessOnly();

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
