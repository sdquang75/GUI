# QuickRes
<p align="center">
  <img src="QuickRes.png" width="140" alt="QuickRes">
</p>
if youve been false banned read https://sites.google.com/view/quickresonline/home

A tiny Windows tool for instantly switching your resolution with one click.

Built with stretched resolutions for Valorant in mind, but it works for switching resolution on anything.

## Why use this over NVIDIA Control Panel?

Switching resolution manually through NVIDIA Control Panel (or AMD/Intel equivalents) requires opening the panel, finding the resolution, and clicking Apply. QuickRes skips all of that: set a hotkey, pick a resolution, done.

## Install

1. Go to releases and download the latest `QuickRes.exe`
2. Run it (no install needed, it's a single portable exe)

Windows may show a SmartScreen warning ("Windows protected your PC") since the exe isn't code-signed.

## Using it with Valorant

Stretched resolutions only works while you're fully loaded into a match. Applying too early (agent select, loading screen) gives you black bars instead of stretch. Here's the setup:

**One-time setup:**
1. Open Monitors in quickre
2. Disable every monitor listed
3. In Valorant's video settings, set your aspect ratio method to Fill

**Every match:**
1. During agent select and while loading in, stay on your **native resolution** (e.g. 1920x1080, or 2560x1440 on a larger monitor)
2. Once you are **fully loaded into the game** (not agent select, not the loading screen), press your hotkey to switch to stretch
3. Play the match at your stretched res
4. Press your hotkey again to go native
5. Repeat next match, since loading into a new game resets you back to native resolution

## Custom resolutions

QuickRes can only switch to resolutions your GPU driver already knows about. If a resolution doesn't show up or gives an error, you need to register it with your driver first:

- **NVIDIA:** NVIDIA Control Panel > Display > Change Resolution > Customize > Create Custom Resolution
- **AMD:** AMD Software > Display > Custom Resolutions > Create New
- **Intel:** Intel Graphics Software > Display > Custom Resolutions

Once it's added there, QuickRes will be able to switch to it. You can also enter any custom width x height directly in the app.

More troubleshooting is in the in-app FAQ.

## License / usage

This project is fully open source. Feel free to modify it for your own use.

**Do not sell this tool or any modified version of it.**

## Building from source

QuickRes uses [pywebview](https://pywebview.flowrl.com/) for its GUI (the panel is a single `quickres/webview/panel.html` file rendered in an embedded EdgeChromium window), not Tkinter. Windows only.

```bash
pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico;." --add-data "quickres/webview;quickres/webview" --name QuickRes main.py
```

`QuickRes.spec` bundles `quickres/webview/panel.html` and `quickres/webview/QuickRes.png` at the correct path for the frozen build and excludes Tkinter (unused since the GUI rewrite). The resulting single-file `dist/QuickRes.exe` is portable, no install needed.
