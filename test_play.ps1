Add-Type -AssemblyName System.Speech
$cableGuid = "{212dba36-aa81-45f3-8734-4d6a8a1f81ad}"

# Check all loaded modules in audiodg right now
$mods = (Get-Process audiodg).Modules | Select-Object -ExpandProperty ModuleName
Write-Host "Total modules in audiodg: $($mods.Count)"

# Let's search if WMALFXGFXDSP is in any audio device
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
# Find CABLE Input audio device in SpeechSynthesizer
Write-Host "Speech devices:"
# Or let's play a .wav using SoundPlayer or WASAPI
