$cableGuid = "{212dba36-aa81-45f3-8734-4d6a8a1f81ad}"
$regFile = "$env:TEMP\cable_loudness.reg"

$regContent = @"
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties]
"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},1"="{62dc1a93-ae24-464c-a43e-452f824c4250}"
"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},2"="{637c490d-eee3-4c0a-973f-371958802da2}"
"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},3"="{5860E1C5-F95C-4a7a-8EC8-8AEF24F379A1}"
"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},5"="{62dc1a93-ae24-464c-a43e-452f824c4250}"
"{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},6"="{637c490d-eee3-4c0a-973f-371958802da2}"
"{fc52a749-4be9-4510-896e-966ba6525980},3"=hex:0b,00,00,00,01,00,00,00,ff,ff,00,00
"{9c00eeed-edce-4cd8-ae08-cb05e8ef57a0},3"=hex:03,00,00,00,01,00,00,00,04,00,00,00
"@

[System.IO.File]::WriteAllText($regFile, $regContent, [System.Text.Encoding]::ASCII)

Write-Host "Created registry file: $regFile"

# Create a combined elevated runner script
$elevatedScript = "$env:TEMP\import_and_restart.bat"
$batContent = @"
@echo off
regedit.exe /s "$regFile"
powershell.exe -NoProfile -Command "Restart-Service audiosrv -Force"
"@
[System.IO.File]::WriteAllText($elevatedScript, $batContent)

Write-Host "Triggering elevated import and audio service restart..."
$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$elevatedScript`"" -Verb RunAs -Wait -PassThru

Write-Host "Process exit code: $($proc.ExitCode)"
Start-Sleep -Seconds 2

# Verify
$props = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties" -ErrorAction SilentlyContinue
if ($props -and $props.'{fc52a749-4be9-4510-896e-966ba6525980},3') {
    Write-Host "SUCCESS: Loudness Equalization is ENABLED on CABLE Input!" -ForegroundColor Green
} else {
    Write-Host "WARNING: Registry properties not detected yet." -ForegroundColor Yellow
}
