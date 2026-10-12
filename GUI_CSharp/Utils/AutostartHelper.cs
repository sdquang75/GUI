using System;
using System.Windows.Forms;
using Microsoft.Win32;

namespace GUI.Utils
{
    public static class AutostartHelper
    {
        private const string RunKeyPath = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string AppName = "GUI";

        public static void SetAutostart(bool enable)
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, true);
                if (key != null)
                {
                    if (enable)
                    {
                        string exePath = Application.ExecutablePath;
                        key.SetValue(AppName, $"\"{exePath}\"");
                    }
                    else
                    {
                        if (key.GetValue(AppName) != null)
                        {
                            key.DeleteValue(AppName, false);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to update autostart: {ex.Message}");
            }
        }

        public static bool IsAutostartEnabled()
        {
            try
            {
                using var key = Registry.CurrentUser.OpenSubKey(RunKeyPath, false);
                return key?.GetValue(AppName) != null;
            }
            catch
            {
                return false;
            }
        }
    }
}
