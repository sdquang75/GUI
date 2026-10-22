using System;
using System.Diagnostics;
using System.IO;
using Microsoft.Win32;

namespace GUI.Utils
{
    public static class Cs2Helper
    {
        public static string? FindCs2CfgDirectory(string? profilePath = null)
        {
            // 1. Try from profilePath
            if (!string.IsNullOrEmpty(profilePath))
            {
                var dir = TryExtractCfgFromPath(profilePath);
                if (dir != null) return dir;
            }

            // 2. Try from running process (anti-cheat safe via ProcessHelper)
            try
            {
                var procs = Process.GetProcessesByName("cs2");
                if (procs.Length > 0)
                {
                    string exePath = ProcessHelper.GetProcessPath(procs[0].Id);
                    foreach (var p in procs) { try { p.Dispose(); } catch { } }
                    if (!string.IsNullOrEmpty(exePath))
                    {
                        var dir = TryExtractCfgFromPath(exePath);
                        if (dir != null) return dir;
                    }
                }
            }
            catch { }

            // 3. Try from Steam registry & libraryfolders.vdf
            try
            {
                string? steamPath = null;
                using (var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam"))
                {
                    steamPath = key?.GetValue("SteamPath") as string;
                }
                if (string.IsNullOrEmpty(steamPath))
                {
                    using (var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\WOW6432Node\Valve\Steam"))
                    {
                        steamPath = key?.GetValue("InstallPath") as string;
                    }
                }

                if (!string.IsNullOrEmpty(steamPath))
                {
                    steamPath = steamPath.Replace('/', '\\');
                    var directCandidate = Path.Combine(steamPath, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg");
                    if (Directory.Exists(directCandidate)) return directCandidate;

                    string vdfPath = Path.Combine(steamPath, "steamapps", "libraryfolders.vdf");
                    if (File.Exists(vdfPath))
                    {
                        var lines = File.ReadAllLines(vdfPath);
                        foreach (var line in lines)
                        {
                            var trimmed = line.Trim();
                            if (trimmed.StartsWith("\"path\"", StringComparison.OrdinalIgnoreCase))
                            {
                                int firstQuote = trimmed.IndexOf('\"', 6);
                                if (firstQuote >= 0)
                                {
                                    int secondQuote = trimmed.IndexOf('\"', firstQuote + 1);
                                    if (secondQuote > firstQuote)
                                    {
                                        string rawPath = trimmed.Substring(firstQuote + 1, secondQuote - firstQuote - 1);
                                        string libPath = rawPath.Replace(@"\\", @"\");
                                        var libCandidate = Path.Combine(libPath, "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg");
                                        if (Directory.Exists(libCandidate)) return libCandidate;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            catch { }

            // 4. Scan all ready drives dynamically
            try
            {
                foreach (var drive in DriveInfo.GetDrives())
                {
                    if (drive.IsReady)
                    {
                        string root = drive.RootDirectory.FullName;
                        var c1 = Path.Combine(root, "SteamLibrary", "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg");
                        if (Directory.Exists(c1)) return c1;
                        var c2 = Path.Combine(root, "Program Files (x86)", "Steam", "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg");
                        if (Directory.Exists(c2)) return c2;
                        var c3 = Path.Combine(root, "Games", "SteamLibrary", "steamapps", "common", "Counter-Strike Global Offensive", "game", "csgo", "cfg");
                        if (Directory.Exists(c3)) return c3;
                    }
                }
            }
            catch { }

            return null;
        }

        private static string? TryExtractCfgFromPath(string path)
        {
            try
            {
                if (string.IsNullOrEmpty(path)) return null;
                string cur = File.Exists(path) ? Path.GetDirectoryName(path)! : path;
                for (int i = 0; i < 6 && !string.IsNullOrEmpty(cur); i++)
                {
                    if (Directory.Exists(Path.Combine(cur, "game", "csgo", "cfg"))) return Path.Combine(cur, "game", "csgo", "cfg");
                    if (Directory.Exists(Path.Combine(cur, "csgo", "cfg"))) return Path.Combine(cur, "csgo", "cfg");
                    if (cur.EndsWith("cfg", StringComparison.OrdinalIgnoreCase) && Directory.Exists(cur)) return cur;

                    var parent = Directory.GetParent(cur);
                    if (parent == null) break;
                    cur = parent.FullName;
                }
            }
            catch { }
            return null;
        }

        public static (bool success, string cfgFile, string message) InstallGsiConfig(string cfgDir, int port = 23456)
        {
            try
            {
                if (!Directory.Exists(cfgDir))
                {
                    Directory.CreateDirectory(cfgDir);
                }

                string content =
$@"""GUI Anti-Flashbang CS2 GSI""
{{
    ""uri"" ""http://127.0.0.1:{port}""
    ""timeout"" ""1.1""
    ""buffer""  ""0.0""
    ""throttle"" ""0.0""
    ""heartbeat"" ""1.0""
    ""data""
    {{
        ""provider""            ""1""
        ""map""                 ""1""
        ""round""               ""1""
        ""player_id""           ""1""
        ""player_state""        ""1""
    }}
}}
";
                string targetFile = Path.Combine(cfgDir, "gamestate_integration_vibe.cfg");
                File.WriteAllText(targetFile, content);
                return (true, targetFile, "GSI config written successfully");
            }
            catch (Exception ex)
            {
                return (false, "", ex.Message);
            }
        }

        public static bool IsGdiIcmGammaRangeUnlocked()
        {
            try
            {
                using var key = Registry.LocalMachine.OpenSubKey(@"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM");
                if (key != null)
                {
                    var val = key.GetValue("GdiIcmGammaRange");
                    if (val is int intVal && intVal == 256) return true;
                }
            }
            catch { }
            return false;
        }

        public static (bool success, string message) UnlockRegistryGdiRange()
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = "reg.exe",
                    Arguments = @"add ""HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM"" /v GdiIcmGammaRange /t REG_DWORD /d 256 /f",
                    Verb = "runas",
                    UseShellExecute = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };
                using var proc = Process.Start(psi);
                proc?.WaitForExit(10000);
                bool unlocked = IsGdiIcmGammaRangeUnlocked();
                return (unlocked, unlocked ? "Registry unlocked successfully" : "Registry was not modified");
            }
            catch (Exception ex)
            {
                return (false, ex.Message);
            }
        }

        public static bool IsGsiConfigInstalled(string cfgDir)
        {
            try
            {
                string targetFile = Path.Combine(cfgDir, "gamestate_integration_vibe.cfg");
                return File.Exists(targetFile);
            }
            catch { return false; }
        }
    }
}
