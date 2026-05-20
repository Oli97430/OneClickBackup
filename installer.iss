; =============================================================================
; OneClick Backup - Inno Setup Installer Script
; =============================================================================
;
; Build with:  iscc installer.iss
;
; Prerequisites:
;   1. Run "python build.py" first to produce dist\OneClickBackup.exe
;   2. Install Inno Setup 6.x from https://jrsoftware.org/issetup.php
;   3. (Optional) Run sign.ps1 to sign the EXE before packaging
;
; =============================================================================

#define MyAppName      "OneClick Backup"
#define MyAppExeName   "OneClickBackup.exe"
#define MyAppVersion   "1.2.0"
#define MyAppPublisher "OneClickBackup"
#define MyAppURL       "https://github.com/OneClickBackup/OneClickBackup"

[Setup]
AppId={{B7F1A2D4-9E3C-4F5B-8A1D-6C2E7F0B3D9A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\OneClickBackup
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Include the MIT license as the agreement screen
LicenseFile=LICENSE
; Output installer settings
OutputDir=dist
OutputBaseFilename=OneClickBackup_Setup_{#MyAppVersion}
; Icon for the installer itself
SetupIconFile=assets\icon.ico
; Compression
Compression=lzma2/ultra64
SolidCompression=yes
; Modern wizard style
WizardStyle=modern
; Require admin for VHDX association; otherwise install per-user is fine
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; Uninstaller settings
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; Minimum Windows 10 (build 10240 = version 10.0.10240)
MinVersion=10.0.10240
; Version info embedded in the installer EXE
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "french";  MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon";    Description: "{cm:CreateDesktopIcon}";    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "vhdxassoc";      Description: "Associate .vhdx files with {#MyAppName}"; GroupDescription: "File associations:"; Flags: unchecked

[Files]
; Main executable from PyInstaller output
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Icon file for shortcuts and file associations
Source: "assets\icon.ico";      DestDir: "{app}"; Flags: ignoreversion
; License file
Source: "LICENSE";              DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}";           Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
; Start Menu uninstall shortcut
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop shortcut (optional)
Name: "{commondesktop}\{#MyAppName}";   Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Registry]
; VHDX file association (only when user selects the task)
Root: HKCR; Subkey: ".vhdx";                                 ValueType: string; ValueName: ""; ValueData: "OneClickBackup.vhdx"; Tasks: vhdxassoc; Flags: uninsdeletevalue
Root: HKCR; Subkey: "OneClickBackup.vhdx";                   ValueType: string; ValueName: ""; ValueData: "VHDX Disk Image"; Tasks: vhdxassoc; Flags: uninsdeletekey
Root: HKCR; Subkey: "OneClickBackup.vhdx\DefaultIcon";       ValueType: string; ValueName: ""; ValueData: "{app}\icon.ico,0"; Tasks: vhdxassoc
Root: HKCR; Subkey: "OneClickBackup.vhdx\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: vhdxassoc

[Run]
; Offer to launch the app after installation
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// Prevent installing on Windows versions older than 10
function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsWindows10OrGreater() then
  begin
    MsgBox('OneClick Backup requires Windows 10 or later.', mbError, MB_OK);
    Result := False;
  end;
end;

// Notify the shell that file associations changed (so Explorer refreshes icons)
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Refresh shell icon cache for new file associations
    Exec('ie4uinit.exe', '-show', '', SW_HIDE, ewNoWait, ResultCode);
  end;
end;
