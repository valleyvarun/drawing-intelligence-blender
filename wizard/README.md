# Drawing Intelligence for Blender setup

`dist/Drawing-Intelligence-Blender-Setup.exe` is the Windows installer linked from `../index.html`.

## Build

Run `wizard\build.bat` from the repository root. It uses the Windows .NET Framework C# compiler, treats warnings as errors, and generates the icon from `../imgs/plexus-logo-purple.png`. No Visual Studio or .NET SDK is needed.

Commit the rebuilt executable when changing the wizard. App-only updates need only a push to `main`.

## Installation

The wizard downloads the official Blender 5.2.2 LTS Windows x64 ZIP and checks the pinned SHA-256 value from Blender's official checksum list. It downloads the app from `valleyvarun/drawing-intelligence-blender`, stages both downloads, and installs them with a private runtime. Failed downloads leave the previous installation intact. Replaced application files are backed up during the commit step and restored if that step fails.

The default is `%LOCALAPPDATA%\Programs\Drawing-Intelligence-Blender`. Paths with spaces work. The installer rejects occupied unrelated folders, drive roots, symbolic links, junctions, and shell metacharacters. It creates per-user shortcuts and a separate Windows uninstall entry, without administrator rights. Blender's engine files and license notices stay together.

Updates and uninstall preserve `runtime/` and user files outside the managed application entries (`app/`, `engine/`, launch scripts, README and notices). Save drawings outside those managed entries. Uninstall may leave the install folder with these retained files.

## Command line

| Command | Result |
| --- | --- |
| `Drawing-Intelligence-Blender-Setup.exe` | Interactive wizard |
| `Drawing-Intelligence-Blender-Setup.exe /silent /accepteula /dir="C:\My Drawing App" /nodesktop` | Install without UI; acknowledges the Blender information |
| `/sourcezip=PATH-or-HTTPS-URL` | Use another app archive with the repository layout |
| `/blenderzip=PATH-or-HTTPS-URL` | Use a mirror/local copy of the exact pinned Blender ZIP; checksum validation still applies |
| `uninstall.exe /silent` | Remove managed files and integration, preserving user files |

Exit codes: `0` success, `1` cancelled, `2` invalid options/folder, `3` failed, `4` license information not acknowledged. Logs start in `%TEMP%\Drawing-Intelligence-Blender-Setup.log`; after installation they are written to `install.log` inside the install folder.

## Validation

Run `powershell -NoProfile -ExecutionPolicy Bypass -File wizard\tests\validate.ps1` for invalid-input and checksum checks. Add `-Integration -BlenderZip C:\path\blender-5.2.2-windows-x64.zip` for a full installation using the official pinned archive. This checks installation and update with a space-containing path, shortcut and uninstall registration, startup, project/preference preservation, and uninstall. It refuses to replace an existing product registration or shortcut.
