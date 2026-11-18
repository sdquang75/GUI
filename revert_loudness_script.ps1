$cableGuid = "{212dba36-aa81-45f3-8734-4d6a8a1f81ad}"

# Elevated cleanup batch file
$cleanBat = "$env:TEMP\clean_cable_fx.bat"
$batContent = @"
@echo off
reg delete "HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties" /f
powershell.exe -NoProfile -Command "Restart-Service audiosrv -Force"
"@
[System.IO.File]::WriteAllText($cleanBat, $batContent)

Write-Host "Deleting FxProperties registry key and restarting Audio Service..."
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$cleanBat`"" -Verb RunAs -Wait -PassThru
Write-Host "Registry delete process exit code: $($proc.ExitCode)"

# Delete downloaded files
$filesToDelete = @(
    "$env:USERPROFILE\EnableLoudness.ps1",
    "$env:TEMP\cable_loudness.reg",
    "$env:TEMP\import_and_restart.bat",
    "$env:TEMP\SoundEnhancementsTMP.reg",
    "$env:TEMP\fix_cable_enhancements.reg",
    "$env:TEMP\run_fix.bat",
    "$env:TEMP\clean_cable_fx.bat"
)

foreach ($f in $filesToDelete) {
    if (Test-Path $f) {
        Remove-Item $f -Force -ErrorAction SilentlyContinue
        Write-Host "Deleted file: $f"
    }
}

Start-Sleep -Seconds 2

$stillExists = Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties"
if (!$stillExists) {
    Write-Host "SUCCESS: CABLE Input has been completely restored to default clean state!" -ForegroundColor Green
} else {
    Write-Host "WARNING: Key still exists." -ForegroundColor Yellow
}
