$cableGuid = "{212dba36-aa81-45f3-8734-4d6a8a1f81ad}"
$psScript = "$env:TEMP\del_fx.ps1"

$content = @"
Remove-Item "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties" -Recurse -Force -ErrorAction SilentlyContinue
Restart-Service audiosrv -Force
"@

[System.IO.File]::WriteAllText($psScript, $content)
$proc = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$psScript`"" -Verb RunAs -Wait -PassThru
Write-Host "Exit code: $($proc.ExitCode)"
Start-Sleep -Seconds 2

$still = Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties"
Write-Host "Still exists: $still"
