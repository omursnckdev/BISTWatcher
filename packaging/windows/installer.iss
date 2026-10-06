; Inno Setup script: per-user install (no admin rights), Start menu + optional desktop icon.
; Built by .github/workflows/windows-app.yml:  iscc /DAppVersion=x.y.z installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1F6C3E-7B0B-4E55-9E0B-3A1B5D2B9F41}
AppName=BISTWatcher
AppVersion={#AppVersion}
AppPublisher=BISTWatcher
DefaultDirName={localappdata}\Programs\BISTWatcher
DefaultGroupName=BISTWatcher
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=BISTWatcher-Kurulum
SetupIconFile=bistwatcher.ico
UninstallDisplayIcon={app}\BISTWatcher.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\BISTWatcher\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\BISTWatcher"; Filename: "{app}\BISTWatcher.exe"
Name: "{group}\{cm:UninstallProgram,BISTWatcher}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\BISTWatcher"; Filename: "{app}\BISTWatcher.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\BISTWatcher.exe"; Description: "{cm:LaunchProgram,BISTWatcher}"; Flags: nowait postinstall skipifsilent
