$renders = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
foreach ($r in $renders) {
    $p = Join-Path $r.PSPath "Properties"
    $name = (Get-ItemProperty $p -ErrorAction SilentlyContinue).'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
    $fx = Join-Path $r.PSPath "FxProperties"
    if (Test-Path $fx) {
        $fxProps = Get-ItemProperty $fx -ErrorAction SilentlyContinue
        $cnt = ($fxProps.PSObject.Properties | Where-Object { $_.Name -like "{*" }).Count
        Write-Host "$name ($($r.PSChildName)) -> FxProperties count: $cnt"
    }
}
