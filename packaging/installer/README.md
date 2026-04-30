# Bazaar Tracker Windows Installer

The installer is built with Inno Setup 6 from the existing PyInstaller onedir output in `dist\BazaarTracker`.

Build flow:

1. Build or refresh the portable package:
   `powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1`
   Optional fresh-clone/custom-venv form:
   `powershell -ExecutionPolicy Bypass -File packaging\pyinstaller\build_portable.ps1 -PythonExe C:\Path\To\python.exe`
2. Build the installer:
   `powershell -ExecutionPolicy Bypass -File packaging\installer\build_installer.ps1`

`packaging\pyinstaller\build_portable.ps1` is fresh-clone friendly. It prefers an explicit `-PythonExe`, otherwise uses `.\venv312\Scripts\python.exe` only when that local environment exists, and finally falls back to the active `python` on PATH. The script prints the Python executable it selected and keeps the existing `-NoClean` behavior.

Update distribution should use GitHub Releases rather than a hosted project website. Update checks are disabled by default until `updates.github_repo` or `updates.manifest_url` is configured, and dashboard update status remains non-blocking even when the release source is unavailable or misconfigured.

The installer writes app files to a versioned install directory, creates Start Menu shortcuts, offers an optional desktop shortcut, and exposes a Start Menu doctor shortcut. Uninstall removes installed app files by default and prompts before deleting `%APPDATA%\BazaarTracker` and `%LOCALAPPDATA%\BazaarTracker`.
