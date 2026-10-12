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
    }

    public class AppConfig
    {
        [JsonPropertyName("desktop")]
        public DesktopConfig Desktop { get; set; } = new();

        [JsonPropertyName("autostart")]
        public bool Autostart { get; set; } = false;

        [JsonPropertyName("never_change_res")]
        public bool NeverChangeRes { get; set; } = true;

        [JsonPropertyName("quickres_native")]
        public string QuickResNativeRes { get; set; } = "1920x1080";

        [JsonPropertyName("quickres_stretched")]
        public string QuickResStretchedRes { get; set; } = "1440x1080";

        [JsonPropertyName("quickres_hotkey")]
        public string QuickResHotkey { get; set; } = "F11";

        [JsonPropertyName("quickres_hotkey_enabled")]
        public bool QuickResHotkeyEnabled { get; set; } = false;

        [JsonPropertyName("games")]
        public Dictionary<string, GameProfile> Games { get; set; } = new(StringComparer.OrdinalIgnoreCase);
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

        public static AppConfig Load()
        {
            string path = GetConfigPath();
            if (File.Exists(path))
            {
                try
                {
                    string json = File.ReadAllText(path);
                    var cfg = JsonSerializer.Deserialize<AppConfig>(json, new JsonSerializerOptions
                    {
                        PropertyNameCaseInsensitive = true,
                        ReadCommentHandling = JsonCommentHandling.Skip
                    });
                    if (cfg != null) return cfg;
                }
                catch
                {
                    // Fallback to default
                }
            }

            var defaultCfg = new AppConfig();
            defaultCfg.Desktop.Vibrance = 50;
            defaultCfg.Autostart = false;
            defaultCfg.NeverChangeRes = true;
            return defaultCfg;
        }

        public static void Save(AppConfig cfg)
        {
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
                File.WriteAllText(path, json);
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to save config: {ex.Message}");
            }
        }
    }
}
