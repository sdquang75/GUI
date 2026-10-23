using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace GUI.Config
{
    public class DesktopConfig
    {
        [JsonPropertyName("vibrance")]
        public int Vibrance { get; set; } = 50;
    }

    public class GameProfile
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("path")]
        public string Path { get; set; } = "";

        [JsonPropertyName("vibrance_enabled")]
        public bool VibranceEnabled { get; set; } = true;

        [JsonPropertyName("vibrance")]
        public int Vibrance { get; set; } = 50;

        [JsonPropertyName("gamma_enabled")]
        public bool GammaEnabled { get; set; } = false;

        [JsonPropertyName("gamma")]
        public double Gamma { get; set; } = 1.80;

        [JsonPropertyName("boost_enabled")]
        public bool BoostEnabled { get; set; } = false;

        [JsonPropertyName("boost")]
        public int Boost { get; set; } = 0;

        [JsonPropertyName("cs2_antiflash_enabled")]
        public bool Cs2AntiFlashEnabled { get; set; } = true;

        [JsonPropertyName("cs2_antismoke_enabled")]
        public bool Cs2AntiSmokeEnabled { get; set; } = false;

        [JsonPropertyName("cs2_antiflash_max_dim")]
        public int Cs2AntiFlashMaxDim { get; set; } = 100;

        // Monitor this profile's gamma / black flash / vibrance applies to (the one the game is played on).
        // Stable monitor device path from the DisplayConfig API; empty = primary monitor.
        [JsonPropertyName("display_device_path")]
        public string DisplayDevicePath { get; set; } = "";

        // Friendly name of that monitor, used as a fallback match when it is plugged into another port
        [JsonPropertyName("display_name")]
        public string DisplayName { get; set; } = "";
    }

    public class AppConfig
    {
        [JsonPropertyName("desktop")]
        public DesktopConfig Desktop { get; set; } = new();

        [JsonPropertyName("autostart")]
        public bool Autostart { get; set; } = false;

        [JsonPropertyName("goodbyedpi_mode")]
        public string GoodbyeDpiMode { get; set; } = "git_safe_reverse";

        [JsonPropertyName("cs2_gsi_port")]
        public int Cs2GsiPort { get; set; } = 23456;

        private Dictionary<string, GameProfile> _games = new(StringComparer.OrdinalIgnoreCase);

        [JsonPropertyName("games")]
        public Dictionary<string, GameProfile> Games
        {
            get => _games;
            set
            {
                var dict = new Dictionary<string, GameProfile>(StringComparer.OrdinalIgnoreCase);
                if (value != null)
                {
                    foreach (var kvp in value)
                    {
                        dict[kvp.Key.ToLowerInvariant()] = kvp.Value;
                    }
                }
                _games = dict;
            }
        }
    }

    public static class ConfigManager
    {
        public static string GetConfigPath()
        {
            // 1. Check AppData first (clean single-exe mode like vibranceGUI)
            string appDataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "GUI");
            string appDataFile = Path.Combine(appDataDir, "config.json");
            if (File.Exists(appDataFile)) return appDataFile;

            // 2. Check local directory (portable mode)
            string localPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "config.json");
            if (File.Exists(localPath)) return localPath;

            // 3. Default to AppData
            if (!Directory.Exists(appDataDir)) Directory.CreateDirectory(appDataDir);
            return appDataFile;
        }

        // Set when the existing config could not be read: a copy kept for the user (null if none could be made)
        public static string? RecoveredBackupPath { get; private set; }
        // Unreadable config that could not be backed up either: Save must not overwrite the only copy of it
        public static bool IsSaveBlocked { get; private set; }

        public static AppConfig Load()
        {
            string path = GetConfigPath();
            if (File.Exists(path))
            {
                Exception? error = null;
                // A config that is momentarily locked (antivirus, cloud sync) is retried before giving up on it
                for (int attempt = 0; attempt < 3; attempt++)
                {
                    try
                    {
                        string json = File.ReadAllText(path);
                        var cfg = JsonSerializer.Deserialize<AppConfig>(json, new JsonSerializerOptions
                        {
                            PropertyNameCaseInsensitive = true,
                            ReadCommentHandling = JsonCommentHandling.Skip
                        });
                        if (cfg == null) throw new JsonException("Config file contains null.");

                        cfg.Desktop ??= new DesktopConfig();
                        cfg.Games = cfg.Games;
                        return cfg;
                    }
                    catch (IOException ex)
                    {
                        error = ex;
                        System.Threading.Thread.Sleep(150);
                    }
                    catch (Exception ex)
                    {
                        error = ex;
                        break;
                    }
                }

                // Starting from defaults is fine, but the next Save would silently replace every game profile:
                // keep a copy of the unreadable file first, and refuse to save over it if no copy could be made.
                Program.Log($"ConfigManager: cannot read '{path}': {error?.Message}");
                try
                {
                    string backup = $"{path}.corrupt-{DateTime.Now:yyyyMMdd-HHmmss}.bak";
                    File.Copy(path, backup, overwrite: true);
                    RecoveredBackupPath = backup;
                    Program.Log($"ConfigManager: unreadable config backed up to '{backup}', starting from defaults.");
                }
                catch (Exception ex)
                {
                    IsSaveBlocked = true;
                    Program.Log($"ConfigManager: backup failed ({ex.Message}) -> saving disabled for this session to keep the original file.");
                }
            }

            var defaultCfg = new AppConfig();
            defaultCfg.Desktop.Vibrance = 50;
            defaultCfg.Autostart = false;
            return defaultCfg;
        }

        public static void Save(AppConfig cfg)
        {
            if (IsSaveBlocked)
            {
                Program.Log("ConfigManager: save skipped (original config could not be read or backed up).");
                return;
            }

            try
            {
                string path = GetConfigPath();
                string dir = Path.GetDirectoryName(path)!;
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                var opts = new JsonSerializerOptions
                {
                    WriteIndented = true,
                    Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping
                };
                string json = JsonSerializer.Serialize(cfg, opts);
                string tmpPath = path + ".tmp";
                File.WriteAllText(tmpPath, json);
                File.Move(tmpPath, path, overwrite: true);
            }
            catch (Exception ex)
            {
                Program.Log($"Failed to save config: {ex.Message}");
                System.Diagnostics.Debug.WriteLine($"Failed to save config: {ex.Message}");
            }
        }
    }
}
