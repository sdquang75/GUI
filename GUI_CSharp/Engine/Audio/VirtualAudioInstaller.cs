using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// One-button setup of the Sound Lock path on any PC: installs VB-CABLE when missing, removes anything on the
    /// cable that would color the game before the limiter (Loudness EQ / enhancements, "Listen to this device"),
    /// puts back default speakers / microphone that the install moved onto the cable, and sets the cable to 100%.
    /// Nothing is tied to a particular sound card, sample rate or bit depth: the limiter reads the cable's own format and
    /// Windows converts to whatever the output device runs.
    /// Running it again is safe: it only repairs what is wrong.
    /// </summary>
    public static class VirtualAudioInstaller
    {
        // Newest first; VB-Audio keeps older packs online
        private static readonly string[] DownloadUrls =
        {
            "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack46.zip",
            "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip",
        };

        // Runs elevated (one UAC prompt for everything). Finds the cable by its driver interface name, so renamed
        // devices still match and VB-Audio's other cables are left alone. Restarts the audio service only when a
        // setting actually changed (the restart is what applies them). No Windows restart: a freshly installed driver
        // that does not show up is brought up by a hardware rescan + rebuilding the audio device list.
        private const string ElevatedScript = @"
param([string]$Installer = '')
$ErrorActionPreference = 'Continue'
$base = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio'
$iface = '{b3f8fa53-0004-438e-9003-51a46e139bfc},6'

function Get-CableKeys {
    foreach ($flow in 'Render', 'Capture') {
        Get-ChildItem ""$base\$flow"" -ErrorAction SilentlyContinue | Where-Object {
            (Get-ItemProperty (Join-Path $_.PSPath 'Properties') -ErrorAction SilentlyContinue).$iface -eq 'VB-Audio Virtual Cable'
        } | ForEach-Object { [pscustomobject]@{ Flow = $flow; Path = $_.PSPath } }
    }
}

function Set-Binary($path, $name, [byte[]]$value) {
    $current = (Get-Item $path -ErrorAction SilentlyContinue).GetValue($name)
    if ($current -and (($current -join ',') -eq ($value -join ','))) { return $false }
    Set-ItemProperty -Path $path -Name $name -Value $value -Type Binary -ErrorAction SilentlyContinue
    return $true
}

# Both sides of the cable present and enabled (DEVICE_STATE_ACTIVE)
function Test-CableActive {
    foreach ($flow in 'Render', 'Capture') {
        $ok = Get-ChildItem ""$base\$flow"" -ErrorAction SilentlyContinue | Where-Object {
            $_.GetValue('DeviceState') -eq 1 -and
            (Get-ItemProperty (Join-Path $_.PSPath 'Properties') -ErrorAction SilentlyContinue).$iface -eq 'VB-Audio Virtual Cable'
        }
        if (-not $ok) { return $false }
    }
    return $true
}

if ($Installer) {
    $p = Start-Process -FilePath $Installer -ArgumentList '-i', '-h' -Wait -PassThru
    for ($i = 0; $i -lt 15 -and -not (Test-CableActive); $i++) { Start-Sleep -Seconds 1 }
    if (-not (Test-CableActive)) {
        # Driver in place but its devices are not up yet. Instead of a reboot: rescan hardware (Device Manager's
        # 'Scan for hardware changes') and rebuild the audio device list (Windows Audio restarts with it).
        & pnputil.exe /scan-devices 2>&1 | Out-Null
        Restart-Service AudioEndpointBuilder -Force -ErrorAction SilentlyContinue
        Start-Service audiosrv -ErrorAction SilentlyContinue
        for ($i = 0; $i -lt 20 -and -not (Test-CableActive); $i++) { Start-Sleep -Seconds 1 }
    }
}

$vtTrue  = [byte[]](11,0,0,0,1,0,0,0,255,255,0,0)
$vtFalse = [byte[]](11,0,0,0,1,0,0,0,0,0,0,0)
$changed = [bool]$Installer
$rebuild = $false
foreach ($c in Get-CableKeys) {
    $props = Join-Path $c.Path 'Properties'
    $fx = Join-Path $c.Path 'FxProperties'
    # Audio enhancements off for the cable
    if (Set-Binary $props '{b7a0c3a5-e0f3-4995-bac8-0ad9aba9d0ed},0' $vtTrue) { $changed = $true }
    # Every Windows enhancement setting is a value in FxProperties: the effect processors (APOs) attached to the
    # device, Loudness EQ, bass boost, room correction... A fresh VB-CABLE has no values there (only Windows' own
    # subkeys, which stay), so all values go and the cable is a plain wire again.
    # (Reinstalling the driver would not do this: Windows keeps these settings across reinstalls.)
    $k = Get-Item $fx -ErrorAction SilentlyContinue
    if ($k -and $k.ValueCount -gt 0) {
        foreach ($n in $k.GetValueNames()) { Remove-ItemProperty -Path $fx -Name $n -ErrorAction SilentlyContinue }
        $changed = $true
        $rebuild = $true
    }
    # 'Listen to this device' on CABLE Output would play the game a second time, unprocessed
    if ($c.Flow -eq 'Capture') {
        if (Set-Binary $props '{24dbb0fc-9311-4b3d-9cf0-18ff155639d4},1' $vtFalse) { $changed = $true }
    }
}

# Processors detached -> rebuild the device list so the cable reloads without them (Windows Audio restarts with it)
if ($rebuild) {
    Restart-Service AudioEndpointBuilder -Force -ErrorAction SilentlyContinue
    Start-Service audiosrv -ErrorAction SilentlyContinue
} elseif ($changed) {
    Restart-Service audiosrv -Force
}
exit 0
";

        public static bool IsInstalled(out string? renderId, out string? captureId)
        {
            (renderId, captureId) = AudioEndpointHelper.GetVirtualCableEndpoints();
            return !string.IsNullOrEmpty(renderId) && !string.IsNullOrEmpty(captureId);
        }

        public static async Task<(bool success, string message)> SetupAsync(Action<string>? status = null)
        {
            if (RuntimeInformation.OSArchitecture is Architecture.Arm64 or Architecture.Arm)
            {
                return (false, "VB-CABLE has no driver for ARM Windows, so Sound Lock cannot be set up on this PC.");
            }

            // Before anything changes: the defaults the user has now
            var defaults = AudioDefaults.Take();

            // A cable that is installed but was disabled in the Sound control panel only needs switching back on
            if (!IsInstalled(out _, out _) && AudioDefaults.EnableDisabledCable() > 0)
            {
                for (int i = 0; i < 10 && !IsInstalled(out _, out _); i++) await Task.Delay(300);
            }
            bool wasInstalled = IsInstalled(out _, out _);

            string work = Path.Combine(Path.GetTempPath(), "GUI_AudioSetup_" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(work);
            try
            {
                string? installer = null;
                if (!wasInstalled)
                {
                    status?.Invoke("Downloading VB-CABLE driver...");
                    installer = await DownloadInstallerAsync(work);
                    if (installer == null)
                    {
                        return (false, "Could not download VB-CABLE. Check the internet connection, or install it from " +
                                       "vb-audio.com/Cable yourself and press Setup again.");
                    }
                }

                status?.Invoke(wasInstalled
                    ? "Checking the cable (accept the admin prompt)..."
                    : "Installing the driver (accept the admin and driver prompts)...");
                string script = Path.Combine(work, "setup.ps1");
                await File.WriteAllTextAsync(script, ElevatedScript);
                int? exit = await RunElevatedAsync(script, installer);
                if (exit == null)
                {
                    return (false, "Setup needs administrator permission and the prompt was cancelled.");
                }
                Program.Log($"[AudioSetup] Elevated step exit code {exit}");

                status?.Invoke("Waiting for the audio devices...");
                string? render = null, capture = null;
                for (int i = 0; i < 40 && !IsInstalled(out render, out capture); i++)
                {
                    await Task.Delay(500);
                }
                if (string.IsNullOrEmpty(render) || string.IsNullOrEmpty(capture))
                {
                    return (false, wasInstalled
                        ? "The audio service is still restarting. Wait a few seconds and press Setup again."
                        : "The driver is installed but Windows has not brought the cable up yet.\n" +
                          "Press Setup again (it rescans the devices). Restart Windows only if it still does not appear.");
                }

                status?.Invoke("Restoring your default speakers and microphone...");
                int restored = AudioDefaults.MoveDefaultsOffCable(defaults, render, capture);
                AudioDefaults.SetCableLevels(render, capture);

                string? issue = AudioEndpointHelper.GetCableInputIssue(render, capture);
                if (issue != null)
                {
                    return (false, $"The cable is installed, but: {issue}.\nPress Setup again to repair it.");
                }

                string msg = "Sound Lock is ready on this PC.";
                if (restored > 0) msg += $"\n{restored} default speaker / microphone setting(s) were on the cable and were moved to your real device(s).";
                msg += "\nIf the game was running, restart it so its audio picks up the new setup.";
                return (true, msg);
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioSetup] Failed: {ex}");
                return (false, $"Setup failed: {ex.Message}");
            }
            finally
            {
                try { Directory.Delete(work, true); } catch { }
            }
        }

        private static async Task<string?> DownloadInstallerAsync(string work)
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(60) };
            foreach (string url in DownloadUrls)
            {
                try
                {
                    byte[] data = await http.GetByteArrayAsync(url);
                    string zip = Path.Combine(work, "vbcable.zip");
                    await File.WriteAllBytesAsync(zip, data);

                    string dir = Path.Combine(work, "pack");
                    if (Directory.Exists(dir)) Directory.Delete(dir, true);
                    ZipFile.ExtractToDirectory(zip, dir);

                    string wanted = Environment.Is64BitOperatingSystem ? "VBCABLE_Setup_x64.exe" : "VBCABLE_Setup.exe";
                    string[] found = Directory.GetFiles(dir, wanted, SearchOption.AllDirectories);
                    if (found.Length > 0) return found[0];
                    Program.Log($"[AudioSetup] {url}: no {wanted} inside");
                }
                catch (Exception ex)
                {
                    Program.Log($"[AudioSetup] {url}: {ex.Message}");
                }
            }
            return null;
        }

        // null = the UAC prompt was declined
        private static async Task<int?> RunElevatedAsync(string script, string? installer)
        {
            var psi = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = $"-NoProfile -ExecutionPolicy Bypass -File \"{script}\" -Installer \"{installer ?? ""}\"",
                Verb = "runas",
                UseShellExecute = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };

            Process? proc;
            try
            {
                proc = Process.Start(psi);
            }
            catch (Win32Exception ex) when (ex.NativeErrorCode == 1223) // ERROR_CANCELLED
            {
                return null;
            }
            if (proc == null) return null;

            using (proc)
            using (var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(5)))
            {
                try
                {
                    await proc.WaitForExitAsync(timeout.Token);
                    return proc.ExitCode;
                }
                catch (OperationCanceledException)
                {
                    Program.Log("[AudioSetup] Elevated step still running after 5 minutes");
                    return -1;
                }
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
