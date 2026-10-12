using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text.Json;
using System.Threading;
using Microsoft.Win32;

namespace GUI.Engine
{
    public class ScalingRegistryItem
    {
        public string SubPath { get; set; } = "";
        public string FullPath { get; set; } = "";
        public string Description { get; set; } = "";
        public int? CurrentValue { get; set; }
        public string SourceType { get; set; } = ""; // "Driver" or "Configuration"

        public string ValueLabel => CurrentValue switch
        {
            2 => "2 (Stretched / Full Screen)",
            4 => "4 (Maintain Aspect Ratio / Black Bars)",
            1 => "1 (Center)",
            null => "Not set (Default)",
            _ => $"{CurrentValue} (Custom)"
        };
    }

    public static class ScalingUtilityEngine
    {
        private const string DISPLAY_CLASS_PATH = @"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}";
        private const string CONFIG_PATH = @"SYSTEM\CurrentControlSet\Control\GraphicsDrivers\Configuration";

        private static string BackupFilePath => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "GUI", "scaling_backup.json");

        [DllImport("user32.dll")]
        private static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);

        private const int KEYEVENTF_KEYUP = 0x0002;
        private const byte VK_LWIN = 0x5B;
        private const byte VK_CONTROL = 0x11;
        private const byte VK_SHIFT = 0x10;
        private const byte VK_B = 0x42;

        public static bool IsAdministrator()
        {
            try
            {
                using var identity = WindowsIdentity.GetCurrent();
                var principal = new WindowsPrincipal(identity);
                return principal.IsInRole(WindowsBuiltInRole.Administrator);
            }
            catch
            {
                return false;
            }
        }

        public static List<ScalingRegistryItem> ReadAllScalingItems()
        {
            var list = new List<ScalingRegistryItem>();

            // 1. Read Display Adapter Drivers
            try
            {
                using var classKey = Registry.LocalMachine.OpenSubKey(DISPLAY_CLASS_PATH, false);
                if (classKey != null)
                {
                    foreach (var subName in classKey.GetSubKeyNames())
                    {
                        if (subName.Length == 4 && int.TryParse(subName, out _))
                        {
                            using var sub = classKey.OpenSubKey(subName, false);
                            if (sub != null)
                            {
                                string desc = sub.GetValue("DriverDesc") as string
                                    ?? sub.GetValue("ProviderName") as string
                                    ?? $"Adapter {subName}";

                                object? val = sub.GetValue("Scaling");
                                int? scalingVal = val is int i ? i : null;

                                list.Add(new ScalingRegistryItem
                                {
                                    SubPath = subName,
                                    FullPath = $@"HKLM\{DISPLAY_CLASS_PATH}\{subName}",
                                    Description = desc,
                                    CurrentValue = scalingVal,
                                    SourceType = "Display Driver"
                                });
                            }
                        }
                    }
                }
            }
            catch { }

            // 2. Read Windows GraphicsDrivers Configuration
            try
            {
                using var cfgKey = Registry.LocalMachine.OpenSubKey(CONFIG_PATH, false);
                if (cfgKey != null)
                {
                    foreach (var monitorKeyName in cfgKey.GetSubKeyNames())
                    {
                        using var monitorKey = cfgKey.OpenSubKey(monitorKeyName, false);
                        if (monitorKey != null)
                        {
                            foreach (var sub00 in monitorKey.GetSubKeyNames())
                            {
                                using var sub00Key = monitorKey.OpenSubKey(sub00, false);
                                if (sub00Key != null)
                                {
                                    foreach (var sub0000 in sub00Key.GetSubKeyNames())
                                    {
                                        using var leafKey = sub00Key.OpenSubKey(sub0000, false);
                                        if (leafKey != null)
                                        {
                                            object? val = leafKey.GetValue("Scaling");
                                            int? scalingVal = val is int i ? i : null;

                                            string shortKey = monitorKeyName.Length > 20
                                                ? monitorKeyName.Substring(0, 18) + "..."
                                                : monitorKeyName;

                                            list.Add(new ScalingRegistryItem
                                            {
                                                SubPath = $@"{monitorKeyName}\{sub00}\{sub0000}",
                                                FullPath = $@"HKLM\{CONFIG_PATH}\{monitorKeyName}\{sub00}\{sub0000}",
                                                Description = $"Topology ({shortKey})",
                                                CurrentValue = scalingVal,
                                                SourceType = "Display Topology"
                                            });
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            catch { }

            return list;
        }

        public static (bool Success, string Message, int Count) SetAllScaling(int targetValue = 2)
        {
            if (!IsAdministrator())
            {
                return RunElevated($"--apply-scaling {targetValue}");
            }

            // Save backup first
            SaveCurrentBackup();

            int count = 0;

            // 1. Set in Display Adapter Driver keys
            try
            {
                using var classKey = Registry.LocalMachine.OpenSubKey(DISPLAY_CLASS_PATH, true);
                if (classKey != null)
                {
                    foreach (var subName in classKey.GetSubKeyNames())
                    {
                        if (subName.Length == 4 && int.TryParse(subName, out _))
                        {
                            try
                            {
                                using var sub = classKey.OpenSubKey(subName, true);
                                if (sub != null)
                                {
                                    sub.SetValue("Scaling", targetValue, RegistryValueKind.DWord);
                                    count++;
                                }
                            }
                            catch { }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                return (false, $"Error writing display class keys: {ex.Message}", count);
            }

            // 2. Set in GraphicsDrivers Configuration
            try
            {
                using var cfgKey = Registry.LocalMachine.OpenSubKey(CONFIG_PATH, true);
                if (cfgKey != null)
                {
                    foreach (var monitorKeyName in cfgKey.GetSubKeyNames())
                    {
                        try
                        {
                            using var monitorKey = cfgKey.OpenSubKey(monitorKeyName, true);
                            if (monitorKey != null)
                            {
                                foreach (var sub00 in monitorKey.GetSubKeyNames())
                                {
                                    using var sub00Key = monitorKey.OpenSubKey(sub00, true);
                                    if (sub00Key != null)
                                    {
                                        foreach (var sub0000 in sub00Key.GetSubKeyNames())
                                        {
                                            using var leafKey = sub00Key.OpenSubKey(sub0000, true);
                                            if (leafKey != null)
                                            {
                                                leafKey.SetValue("Scaling", targetValue, RegistryValueKind.DWord);
                                                count++;
                                            }
                                        }
                                    }
                                }
                            }
                        }
                        catch { }
                    }
                }
            }
            catch (Exception ex)
            {
                return (false, $"Error writing topology config: {ex.Message}", count);
            }

            return (true, $"Successfully set Scaling = {targetValue} on {count} registry entries.", count);
        }

        public static (bool Success, string Message, int Count) RevertScaling()
        {
            if (!IsAdministrator())
            {
                return RunElevated("--revert-scaling");
            }

            var backup = LoadBackup();
            int count = 0;

            if (backup != null && backup.Count > 0)
            {
                // Restore from backup
                foreach (var (fullPath, prevVal) in backup)
                {
                    try
                    {
                        string prefix = @"HKLM\";
                        string relPath = fullPath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)
                            ? fullPath.Substring(prefix.Length)
                            : fullPath;

                        using var key = Registry.LocalMachine.OpenSubKey(relPath, true);
                        if (key != null)
                        {
                            if (prevVal.HasValue)
                            {
                                key.SetValue("Scaling", prevVal.Value, RegistryValueKind.DWord);
                            }
                            else
                            {
                                key.DeleteValue("Scaling", false);
                            }
                            count++;
                        }
                    }
                    catch { }
                }
                return (true, $"Reverted {count} registry entries to their saved state.", count);
            }

            // Fallback: set Scaling back to 4 (Maintain Aspect Ratio)
            return SetAllScaling(4);
        }

        private static void SaveCurrentBackup()
        {
            try
            {
                var items = ReadAllScalingItems();
                var dict = new Dictionary<string, int?>();
                foreach (var item in items)
                {
                    dict[item.FullPath] = item.CurrentValue;
                }

                string dir = Path.GetDirectoryName(BackupFilePath)!;
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                File.WriteAllText(BackupFilePath, JsonSerializer.Serialize(dict));
            }
            catch { }
        }

        private static Dictionary<string, int?>? LoadBackup()
        {
            try
            {
                if (File.Exists(BackupFilePath))
                {
                    string json = File.ReadAllText(BackupFilePath);
                    return JsonSerializer.Deserialize<Dictionary<string, int?>>(json);
                }
            }
            catch { }
            return null;
        }

        private static (bool Success, string Message, int Count) RunElevated(string args)
        {
            try
            {
                string? exePath = Environment.ProcessPath;
                if (string.IsNullOrEmpty(exePath))
                {
                    return (false, "Could not determine exe path for elevation.", 0);
                }

                var psi = new ProcessStartInfo
                {
                    FileName = exePath,
                    Arguments = args,
                    Verb = "runas",
                    UseShellExecute = true,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };

                using var proc = Process.Start(psi);
                if (proc == null) return (false, "UAC prompt was cancelled.", 0);

                proc.WaitForExit(10000);
                if (proc.ExitCode == 0)
                {
                    return (true, "Operation completed with Administrator permissions.", 1);
                }
                return (false, $"Process exited with code {proc.ExitCode}.", 0);
            }
            catch (Exception ex)
            {
                return (false, $"Elevation failed: {ex.Message}", 0);
            }
        }

        public static void RestartGpuDriver()
        {
            // Send Win + Ctrl + Shift + B hotkey broadcast
            keybd_event(VK_LWIN, 0, 0, UIntPtr.Zero);
            keybd_event(VK_CONTROL, 0, 0, UIntPtr.Zero);
            keybd_event(VK_SHIFT, 0, 0, UIntPtr.Zero);
            keybd_event(VK_B, 0, 0, UIntPtr.Zero);

            keybd_event(VK_B, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
            keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
            keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
            keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, UIntPtr.Zero);
        }
    }
}
