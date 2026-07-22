; Version and publisher are supplied by build-release.ps1 from product.py
; (the single source of truth). The defaults below are only a fallback for a
; manual ISCC run and must not be relied on for a real release.
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-manual"
#endif
#ifndef MyAppPublisher
  #define MyAppPublisher "4G1 Live"
#endif
#ifndef MyAppVersionNumeric
  #define MyAppVersionNumeric "0.0.0.0"
#endif
#define MyAppName "4G1 Live"
#define MyAppExeName "4G1 Live.exe"

[Setup]
AppId={{EE6C48A9-3EA4-4A7F-B0D4-4C7CD78FD186}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf32}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=EULA.txt
InfoAfterFile=README-FIRST.txt
OutputDir=releases
OutputBaseFilename=4G1-Live-Setup-{#MyAppVersion}-win32
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x86 x64compatible
PrivilegesRequired=admin
MinVersion=10.0
VersionInfoVersion={#MyAppVersionNumeric}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersionNumeric}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "dist\4G1 Live\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "PRIVACY.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "SUPPORT.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "EULA.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Documentation"; Filename: "{app}\README.md"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
