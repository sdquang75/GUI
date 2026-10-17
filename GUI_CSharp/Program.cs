using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using GUI.Forms;

namespace GUI
{
    static class Program
    {
        public const string SingleInstanceMutexName = @"Local\VibeGamerRamp_GUI_SingleInstance_Mutex";
        public const string ShowWindowEventName = @"Local\VibeGamerRamp_GUI_ShowWindow_Event";

        [DllImport("kernel32.dll")]
        private static extern bool AttachConsole(int dwProcessId);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern uint RegisterWindowMessage(string lpString);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        private static readonly IntPtr HWND_BROADCAST = new IntPtr(0xFFFF);
        public static readonly uint WM_SHOW_EXISTING_INSTANCE = RegisterWindowMessage("WM_VIBE_GAMER_RAMP_SHOW_EXISTING");

        public static void Log(string msg)
        {
            try
            {
                string logDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GUI");
                if (!Directory.Exists(logDir)) Directory.CreateDirectory(logDir);
                string path = Path.Combine(logDir, "gui.log");
                File.AppendAllText(path, $"[{DateTime.Now:HH:mm:ss.fff}] {msg}\n");
            }
            catch { }
        }

        private static void SignalExistingInstance()
        {
            try
            {
                using var evt = EventWaitHandle.OpenExisting(ShowWindowEventName);
                evt.Set();
            }
            catch { }

            try
            {
                if (WM_SHOW_EXISTING_INSTANCE != 0)
                {
                    PostMessage(HWND_BROADCAST, WM_SHOW_EXISTING_INSTANCE, IntPtr.Zero, IntPtr.Zero);
                }
            }
            catch { }
        }

        [STAThread]
        static void Main(string[] args)
        {
            Log("Program.Main started with args: " + string.Join(" ", args));

            if (args.Length >= 3 && args[0] == "--monitor-op")
            {
                string op = args[1].ToLowerInvariant();
                string instanceId = args[2];
                bool enable = (op == "enable");
                var (success, msg) = Engine.MonitorEngine.SetDevNodeEnabledDirect(instanceId, enable);
                Log($"--monitor-op {op} {instanceId} -> {success} ({msg})");
                Environment.Exit(success ? 0 : 1);
                return;
            }

            if (args.Length >= 2 && args[0] == "--goodbyedpi-op")
            {
                string op = args[1].ToLowerInvariant();
                bool enable = (op == "start" || op == "enable");
                int parentPid = (args.Length >= 3 && int.TryParse(args[2], out int pid)) ? pid : 0;
                string? modeId = args.Length >= 4 ? args[3] : null;
                var (success, msg) = Engine.GoodbyeDpiEngine.SetEnabledDirect(enable, parentPid, modeId);
                Log($"--goodbyedpi-op {op} (parent={parentPid}, mode={modeId}) -> {success} ({msg})");
                Environment.Exit(success ? 0 : 1);
                return;
            }

            if (args.Length >= 2 && args[0] == "--goodbyedpi-watch")
            {
                if (int.TryParse(args[1], out int parentPid) && parentPid > 0)
                {
                    Engine.GoodbyeDpiEngine.RunWatchdog(parentPid);
                }
                Environment.Exit(0);
                return;
            }

            if (args.Length > 0 && args[0] == "--test")
            {
                AttachConsole(-1);
                Console.WriteLine();
                Console.WriteLine("=== Testing C# GUI Engines ===");
                using var vib = new Engine.UnifiedVibranceEngine();
                Console.WriteLine($"Active Vibrance Engine: {vib.EngineName}");
                Console.WriteLine($"Vibrance Available: {vib.IsAvailable}");
                Console.WriteLine($"Current Level: {vib.GetLevel()}");
                bool setRes = vib.SetLevel(60);
                Console.WriteLine($"SetLevel(60): {setRes}");
                vib.ResetToDefault();

                using var dwm = new Engine.WindowsDwmEngine();
                Console.WriteLine($"DWM Engine Available: {dwm.IsAvailable} ({dwm.EngineName})");
                Console.WriteLine($"DWM SetLevel(70): {dwm.SetLevel(70)}");
                dwm.ResetToDefault();

                using var gamma = new Engine.GammaEngine();
                bool gammaRes = gamma.ApplyCurve(1.80, 0.0);
                Log($"Gamma 1.8 Apply: {gammaRes}");
                gamma.ResetToIdentity();

                var testIcon = Utils.IconHelper.GetGameIcon("cs2.exe", @"D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe", 48);
                Log($"CS2 Icon Loaded: {testIcon.Width}x{testIcon.Height}");
                var curRes = Engine.DisplayEngine.GetCurrentResolution();
                if (curRes.HasValue)
                {
                    Console.WriteLine($"Display Resolution: {curRes.Value.Width}x{curRes.Value.Height} @ {curRes.Value.Frequency}Hz ({Engine.DisplayEngine.GetAspectRatioLabel(curRes.Value.Width, curRes.Value.Height)})");
                }
                var monitors = Engine.MonitorEngine.EnumerateMonitors();
                Console.WriteLine($"Monitors detected: {monitors.Count}");
                foreach (var m in monitors)
                {
                    Console.WriteLine($"  - {m.FriendlyName} [{(m.Enabled ? "Enabled" : "Disabled")}] (ID: {m.InstanceId})");
                }

                using var gsi = new Engine.Cs2GsiListener(23456);
                gsi.Start();
                Console.WriteLine($"CS2 GSI Listener Test: Listening={gsi.IsListening} on port {gsi.Port}");
                gsi.Stop();

                Log("=== Test Complete ===");
                return;
            }

            bool startMinimized = Array.Exists(args, a =>
                string.Equals(a, "--minimized", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(a, "-minimized", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(a, "--autostart", StringComparison.OrdinalIgnoreCase));

            // Single-instance check
            bool createdNew = false;
            Mutex? singleInstanceMutex = null;
            try
            {
                singleInstanceMutex = new Mutex(true, SingleInstanceMutexName, out createdNew);
            }
            catch (UnauthorizedAccessException)
            {
                createdNew = false;
            }

            if (!createdNew)
            {
                Log("Another GUI instance is already running. Signaling existing instance and exiting.");
                if (!startMinimized)
                {
                    SignalExistingInstance();
                }
                singleInstanceMutex?.Dispose();
                return;
            }

            MainForm? mainForm = null;

            AppDomain.CurrentDomain.UnhandledException += (s, e) =>
            {
                Log($"UnhandledException: {e.ExceptionObject}");
                try { mainForm?.CleanupAndRestoreDefaults(); } catch { }
            };

            Application.ThreadException += (s, e) =>
            {
                Log($"ThreadException: {e.Exception}");
            };

            Application.ApplicationExit += (s, e) =>
            {
                Log("Application.ApplicationExit triggered");
                try { mainForm?.CleanupAndRestoreDefaults(); } catch { }
            };

            AppDomain.CurrentDomain.ProcessExit += (s, e) =>
            {
                Log("AppDomain.ProcessExit triggered");
                try { mainForm?.CleanupAndRestoreDefaults(); } catch { }
            };

            ApplicationConfiguration.Initialize();
            try
            {
                Log($"Entering Application.Run (startMinimized={startMinimized})");
                mainForm = new MainForm(startMinimized);
                Application.Run(mainForm);
                Log("Exited Application.Run normally");
            }
            catch (Exception ex)
            {
                Log($"Exception in Application.Run: {ex}");
            }
            finally
            {
                try { mainForm?.CleanupAndRestoreDefaults(); } catch { }
                try
                {
                    if (createdNew && singleInstanceMutex != null)
                    {
                        singleInstanceMutex.ReleaseMutex();
                    }
                }
                catch { }
                singleInstanceMutex?.Dispose();
            }
        }
    }
}