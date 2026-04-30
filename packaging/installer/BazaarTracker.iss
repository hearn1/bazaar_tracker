#define AppName "Bazaar Tracker"
#ifndef AppVersion
#define AppVersion "0.1.0-dev"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\BazaarTracker"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{B2CE81B8-0D78-4424-9D88-2B290B215991}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Bazaar Tracker
AppPublisherURL=https://github.com/
AppSupportURL=https://github.com/
DefaultDirName={autopf}\Bazaar Tracker\{#AppVersion}
DefaultGroupName=Bazaar Tracker
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=BazaarTrackerSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
UninstallDisplayIcon={app}\BazaarTracker.exe
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Bazaar Tracker"; Filename: "{app}\BazaarTracker.exe"; WorkingDir: "{app}"
Name: "{group}\Bazaar Tracker Doctor"; Filename: "{app}\BazaarTracker.exe"; Parameters: "doctor"; WorkingDir: "{app}"
Name: "{group}\Uninstall Bazaar Tracker"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Bazaar Tracker"; Filename: "{app}\BazaarTracker.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\BazaarTracker.exe"; Parameters: "doctor"; Description: "Run Bazaar Tracker Doctor"; Flags: postinstall skipifsilent nowait

[Code]
var
  RemoveUserData: Boolean;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  RemoveUserData :=
    MsgBox(
      'Remove all Bazaar Tracker user data from %APPDATA% and %LOCALAPPDATA%?' + #13#10 + #13#10 +
      'Choose No to keep settings, logs, cache, and the run database.',
      mbConfirmation,
      MB_YESNO
    ) = IDYES;
end;

function ShouldRemoveUserData(): Boolean;
begin
  Result := RemoveUserData;
end;

[UninstallDelete]
Type: filesandordirs; Name: "{userappdata}\BazaarTracker"; Check: ShouldRemoveUserData
Type: filesandordirs; Name: "{localappdata}\BazaarTracker"; Check: ShouldRemoveUserData
