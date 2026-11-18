$cableGuid = "{212dba36-aa81-45f3-8734-4d6a8a1f81ad}"
$regFile = "$env:TEMP\delete_fx.reg"

$regContent = @"
Windows Registry Editor Version 5.00

[-HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties]
"@

[System.IO.File]::WriteAllText($regFile, $regContent, [System.Text.Encoding]::ASCII)

$elevatedScript = "$env:TEMP\run_delete.bat"
$batContent = @"
@echo off
regedit.exe /s "$regFile"
powershell.exe -NoProfile -Command "Restart-Service audiosrv -Force"
"@
[System.IO.File]::WriteAllText($elevatedScript, $batContent)

$proc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c `"$elevatedScript`"" -Verb RunAs -Wait -PassThru
Write-Host "Delete process exit code: $($proc.ExitCode)"
Start-Sleep -Seconds 2

$stillExists = Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties"
Write-Host "FxProperties still exists: $stillExists"
