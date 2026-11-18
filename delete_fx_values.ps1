$cableGuid = "{212dba36-aa81-45f3-8734-4d6a8a1f81ad}"
$psScript = "$env:TEMP\del_fx_values.ps1"

$content = @"
`$path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties"
`$props = Get-ItemProperty `$path -ErrorAction SilentlyContinue
if (`$props) {
    foreach (`$p in (`$props.PSObject.Properties | Where-Object { `$_.Name -like "{*" })) {
        Remove-ItemProperty -Path `$path -Name `$p.Name -Force -ErrorAction SilentlyContinue
    }
}
Restart-Service audiosrv -Force
"@

[System.IO.File]::WriteAllText($psScript, $content)
$proc = Start-Process -FilePath "powershell.exe" -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$psScript`"" -Verb RunAs -Wait -PassThru
Write-Host "Exit code: $($proc.ExitCode)"
Start-Sleep -Seconds 2

$remaining = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$cableGuid\FxProperties" -ErrorAction SilentlyContinue).PSObject.Properties | Where-Object { $_.Name -like "{*" }
Write-Host "Remaining values count: $($remaining.Count)"
