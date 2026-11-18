$modules = (Get-Process audiodg -ErrorAction SilentlyContinue).Modules
$found = $false
foreach ($m in $modules) {
    if ($m.ModuleName -like "*DSP*" -or $m.ModuleName -like "*WMA*" -or $m.ModuleName -like "*cable*") {
        Write-Host "Found: $($m.ModuleName) -> $($m.FileName)"
        $found = $true
    }
}
if (!$found) {
    Write-Host "WMALFXGFXDSP.dll is NOT currently loaded in audiodg.exe"
}
