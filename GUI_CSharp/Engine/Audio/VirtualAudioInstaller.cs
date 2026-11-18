using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace GUI.Engine.Audio
{
    public static class VirtualAudioInstaller
    {
        private const string DownloadUrl = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip";

        public static bool IsInstalled(out string? renderId, out string? captureId)
        {
            (renderId, captureId) = AudioEndpointHelper.GetVirtualCableEndpoints();
            return !string.IsNullOrEmpty(renderId) && !string.IsNullOrEmpty(captureId);
        }

        public static async Task<(bool success, string message)> InstallAndConfigureAsync(
            Action<string>? statusCallback = null,
            string? targetOutputDeviceId = null)
        {
            try
            {
                bool alreadyInstalled = IsInstalled(out _, out _);

                if (alreadyInstalled)
                {
                    statusCallback?.Invoke("Synchronizing format with output device...");
                    RestartAndSyncAudioService(targetOutputDeviceId);
                    await Task.Delay(2000);

                    statusCallback?.Invoke("✓ Virtual Audio is installed and synchronized.");
                    return (true, "Virtual Audio is already installed and synchronized with your output device.");
                }

                statusCallback?.Invoke("Downloading VB-CABLE driver (~1.3MB)...");
                string tempDir = Path.Combine(Path.GetTempPath(), "VBCABLE_Setup");
                if (Directory.Exists(tempDir))
                {
                    try { Directory.Delete(tempDir, true); } catch { }
                }
                Directory.CreateDirectory(tempDir);

                string zipPath = Path.Combine(tempDir, "vbcable.zip");
                using (var http = new HttpClient())
                {
                    var data = await http.GetByteArrayAsync(DownloadUrl);
                    await File.WriteAllBytesAsync(zipPath, data);
                }

                statusCallback?.Invoke("Extracting installer package...");
                ZipFile.ExtractToDirectory(zipPath, tempDir);

                string installerExe = Environment.Is64BitOperatingSystem
                    ? Path.Combine(tempDir, "VBCABLE_Setup_x64.exe")
                    : Path.Combine(tempDir, "VBCABLE_Setup.exe");

                if (!File.Exists(installerExe))
                {
                    installerExe = Path.Combine(tempDir, "VBCABLE_Setup.exe");
                }

                if (!File.Exists(installerExe))
                {
                    return (false, "Could not find VBCABLE installer executable.");
                }

                statusCallback?.Invoke("Installing driver (Click Yes on UAC prompt)...");
                var psi = new ProcessStartInfo
                {
                    FileName = installerExe,
                    Arguments = "-i -h",
                    Verb = "runas",
                    UseShellExecute = true
                };

                using var proc = Process.Start(psi);
                if (proc == null)
                {
                    return (false, "User cancelled UAC elevation.");
                }

                await proc.WaitForExitAsync();

                statusCallback?.Invoke("Syncing format and restarting Audio Service...");
                RestartAndSyncAudioService(targetOutputDeviceId);

                // Wait 2.5 seconds for endpoints to register
                await Task.Delay(2500);

                if (IsInstalled(out _, out _))
                {
                    statusCallback?.Invoke("✓ Virtual Audio installed and ready!");
                    return (true, "Virtual Audio Cable installed and ready!\nAudio routing will automatically engage when you launch your game.");
                }
                else
                {
                    return (false, "Driver installed, but endpoints not detected yet. A system reboot may be needed.");
                }
            }
            catch (Exception ex)
            {
                return (false, $"Installation failed: {ex.Message}");
            }
        }

        public static bool RestartAndSyncAudioService(string? targetOutputDeviceId = null)
        {
            try
            {
                string? targetGuid = null;
                if (!string.IsNullOrEmpty(targetOutputDeviceId))
                {
                    var match = Regex.Match(targetOutputDeviceId, @"\{[0-9a-fA-F\-]{36}\}");
                    if (match.Success)
                    {
                        targetGuid = match.Value;
                    }
                }

                string scriptPath = Path.Combine(Path.GetTempPath(), "sync_audio_cable.ps1");
                string psScript;

                if (!string.IsNullOrEmpty(targetGuid))
                {
                    psScript = $@"
$targetGuid = '{targetGuid}'
try {{
    $srcPath = ""HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$targetGuid\Properties""
    $src = Get-ItemProperty $srcPath -ErrorAction SilentlyContinue
    if ($src) {{
        $fmt = $src.'{{f19f064d-082c-4e27-bc73-6882a1bb8e4c}},0'
        $oem = $src.'{{e4870e26-3cc5-4cd2-ba46-ca0a9a70ed04}},0'
        Get-ChildItem ""HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"" -ErrorAction SilentlyContinue | ForEach-Object {{
            $p = Join-Path $_.PSPath ""Properties""
            $props = Get-ItemProperty $p -ErrorAction SilentlyContinue
            if ($props -and ($props.'{{b3f8fa53-0004-438e-9003-51a46e139bfc}},6' -like '*CABLE*' -or $props.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -like '*CABLE Input*')) {{
                if ($fmt) {{ Set-ItemProperty $p -Name '{{f19f064d-082c-4e27-bc73-6882a1bb8e4c}},0' -Value $fmt -ErrorAction SilentlyContinue }}
                if ($oem) {{ Set-ItemProperty $p -Name '{{e4870e26-3cc5-4cd2-ba46-ca0a9a70ed04}},0' -Value $oem -ErrorAction SilentlyContinue }}
            }}
        }}
        Get-ChildItem ""HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"" -ErrorAction SilentlyContinue | ForEach-Object {{
            $p = Join-Path $_.PSPath ""Properties""
            $props = Get-ItemProperty $p -ErrorAction SilentlyContinue
            if ($props -and ($props.'{{b3f8fa53-0004-438e-9003-51a46e139bfc}},6' -like '*CABLE*' -or $props.'{{a45c254e-df1c-4efd-8020-67d146a850e0}},2' -like '*CABLE Output*')) {{
                if ($fmt) {{ Set-ItemProperty $p -Name '{{f19f064d-082c-4e27-bc73-6882a1bb8e4c}},0' -Value $fmt -ErrorAction SilentlyContinue }}
                if ($oem) {{ Set-ItemProperty $p -Name '{{e4870e26-3cc5-4cd2-ba46-ca0a9a70ed04}},0' -Value $oem -ErrorAction SilentlyContinue }}
            }}
        }}
    }}
}} catch {{}}

Restart-Service audiosrv -Force
";
                }
                else
                {
                    psScript = "Restart-Service audiosrv -Force\n";
                }

                File.WriteAllText(scriptPath, psScript);

                var psi = new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = $"-NoProfile -ExecutionPolicy Bypass -File \"{scriptPath}\"",
                    Verb = "runas",
                    UseShellExecute = true,
                    WindowStyle = ProcessWindowStyle.Hidden
                };

                using var proc = Process.Start(psi);
                proc?.WaitForExit(10000);
                return true;
            }
            catch (Exception ex)
            {
                Program.Log($"[VirtualAudioInstaller] RestartAndSyncAudioService error: {ex.Message}");
                return false;
            }
        }

        public static void OpenSoundControlPanel()
        {
            try
            {
                Process.Start(new ProcessStartInfo
                {
                    FileName = "control.exe",
                    Arguments = "mmsys.cpl sounds",
                    UseShellExecute = true
                });
            }
            catch { }
        }
    }
}
