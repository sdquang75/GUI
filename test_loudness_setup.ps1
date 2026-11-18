# 1. Download EnableLoudness.ps1 if not present
$scriptPath = "$env:USERPROFILE\EnableLoudness.ps1"
if (!(Test-Path $scriptPath)) {
    Write-Host "Downloading EnableLoudness.ps1..."
    Invoke-WebRequest "https://raw.githubusercontent.com/Falcosc/enable-loudness-equalisation/main/EnableLoudness.ps1" -OutFile $scriptPath
}

# 2. Check CABLE Input existence
$devices = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\*\Properties"
$cableFound = $false
$cableKeyPath = ""
foreach ($dev in $devices) {
    $props = Get-ItemProperty $dev.PSPath -ErrorAction SilentlyContinue
    $name = $props.'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
    if ($name -like "*CABLE Input*") {
        $cableFound = $true
        $cableKeyPath = $dev.PSParentPath
        Write-Host "Found CABLE Input: $cableKeyPath"
        break
    }
}

if (!$cableFound) {
    Write-Host "CABLE Input not found in MMDevices. Running installer..."
    $installer = "$env:TEMP\VBCABLE_Setup\VBCABLE_Setup_x64.exe"
    if (Test-Path $installer) {
        $proc = Start-Process -FilePath $installer -ArgumentList "-i -h" -Verb RunAs -Wait -PassThru
        Write-Host "Installer finished with code $($proc.ExitCode). Restarting audiosrv..."
        Restart-Service audiosrv -Force
        Start-Sleep -Seconds 3
    }
}

# 3. Apply EnableLoudness.ps1 to CABLE Input
Write-Host "Applying EnableLoudness.ps1 to 'CABLE Input' with releaseTime 4..."
& powershell.exe -ExecutionPolicy Bypass -File $scriptPath -playbackDeviceName "CABLE Input" -releaseTime 4

Write-Host "Done!"
