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
                string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "app.log");
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
                using var nv = new Engine.NvApiEngine();
                Console.WriteLine($"NvAPI Available: {nv.IsAvailable}");
                if (nv.IsAvailable)
                {
                    Console.WriteLine($"Current DVC Level: {nv.GetLevel()}");
                    bool setRes = nv.SetLevel(50);
                    Console.WriteLine($"SetLevel(50): {setRes}");
                }

                using var gamma = new Engine.GammaEngine();
                bool gammaRes = gamma.ApplyCurve(1.80, 0.0);
                Console.WriteLine($"Gamma 1.8 Apply: {gammaRes}");
                gamma.Restore();
                Console.WriteLine("=== Test Complete ===");
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