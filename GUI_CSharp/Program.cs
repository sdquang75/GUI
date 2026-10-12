using System;
using System.IO;
using System.Windows.Forms;
using GUI.Forms;

namespace GUI
{
    static class Program
    {
        [System.Runtime.InteropServices.DllImport("kernel32.dll")]
        private static extern bool AttachConsole(int dwProcessId);

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

        [STAThread]
        static void Main(string[] args)
        {
            Log("Program.Main started with args: " + string.Join(" ", args));

            AppDomain.CurrentDomain.UnhandledException += (s, e) =>
            {
                Log($"UnhandledException: {e.ExceptionObject}");
            };

            Application.ThreadException += (s, e) =>
            {
                Log($"ThreadException: {e.Exception}");
            };

            Application.ApplicationExit += (s, e) =>
            {
                Log("Application.ApplicationExit triggered");
            };

            AppDomain.CurrentDomain.ProcessExit += (s, e) =>
            {
                Log("AppDomain.ProcessExit triggered");
            };

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
                vib.Restore();

                using var dwm = new Engine.WindowsDwmEngine();
                Console.WriteLine($"DWM Engine Available: {dwm.IsAvailable} ({dwm.EngineName})");
                Console.WriteLine($"DWM SetLevel(70): {dwm.SetLevel(70)}");
                dwm.Restore();

                using var gamma = new Engine.GammaEngine();
                bool gammaRes = gamma.ApplyCurve(1.80, 0.0);
                Log($"Gamma 1.8 Apply: {gammaRes}");
                gamma.Restore();

                var testIcon = Utils.IconHelper.GetGameIcon("cs2.exe", @"D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe", 48);
                Log($"CS2 Icon Loaded: {testIcon.Width}x{testIcon.Height}");
                var npIcon = Utils.IconHelper.GetGameIcon("notepad.exe", @"C:\Windows\System32\notepad.exe", 48);
                Log($"Notepad Icon Loaded: {npIcon.Width}x{npIcon.Height}");
                Log("=== Test Complete ===");
                return;
            }

            ApplicationConfiguration.Initialize();
            try
            {
                Log("Entering Application.Run");
                Application.Run(new MainForm());
                Log("Exited Application.Run normally");
            }
            catch (Exception ex)
            {
                Log($"Exception in Application.Run: {ex}");
            }
        }
    }
}