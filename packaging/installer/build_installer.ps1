[CmdletBinding()]
param(
    [string]$AppVersion,
    [string]$PortableDir,
    [string]$OutputDir,
    [string]$InnoSetupCompiler
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

if (-not $AppVersion) {
    $VersionFile = Join-Path $RepoRoot "version.py"
    $VersionText = Get-Content -Raw -Path $VersionFile
    if ($VersionText -match 'APP_VERSION\s*=\s*"([^"]+)"') {
        $AppVersion = $Matches[1]
    } else {
        throw "Could not read APP_VERSION from $VersionFile"
    }
}

if (-not $PortableDir) {
    $PortableDir = Join-Path $RepoRoot "dist\BazaarTracker"
}
$PortableDir = Resolve-Path $PortableDir

if (-not (Test-Path (Join-Path $PortableDir "BazaarTracker.exe"))) {
    throw "Portable build not found at $PortableDir. Run packaging\pyinstaller\build_portable.ps1 first."
}

if (-not $OutputDir) {
    $OutputDir = Join-Path $RepoRoot "dist\installer"
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$OutputDir = Resolve-Path $OutputDir

if (-not $InnoSetupCompiler) {
    $Candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($Candidates.Count -gt 0) {
        $InnoSetupCompiler = $Candidates[0]
    }
}

if (-not $InnoSetupCompiler -or -not (Test-Path $InnoSetupCompiler)) {
    throw "Inno Setup compiler not found. Install Inno Setup 6 or pass -InnoSetupCompiler C:\Path\To\ISCC.exe."
}

$IssPath = Join-Path $ScriptDir "BazaarTracker.iss"
$Args = @(
    "/DAppVersion=$AppVersion",
    "/DSourceDir=$PortableDir",
    "/DOutputDir=$OutputDir",
    $IssPath
)

Write-Host "[Installer] Building Bazaar Tracker $AppVersion from $PortableDir"
& $InnoSetupCompiler @Args
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}

$Installer = Join-Path $OutputDir "BazaarTrackerSetup-$AppVersion.exe"
if (-not (Test-Path $Installer)) {
    throw "Expected installer was not produced: $Installer"
}

Write-Host "[Installer] Built $Installer"
