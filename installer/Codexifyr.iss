#define MyAppName "Codexifyr WebLab"
#define MyAppVersion "4.2.3"
#define MyAppPublisher "Codexifyr"
#define MyAppExeName "Codexifyr WebLab.exe"

[Setup]
AppId={{D5D22671-EDE1-4A22-9C67-0A5B5AB28D69}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Codexifyr WebLab\App
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=Codexifyr-WebLab-Setup-v{#MyAppVersion}
SetupIconFile=..\frontend\assets\favicon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\desktop\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\frontend\assets\favicon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{localappdata}\Codexifyr WebLab\Data"

[Icons]
Name: "{autodesktop}\Codexifyr WebLab"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\favicon.ico"
Name: "{autoprograms}\Codexifyr WebLab"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\favicon.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Codexifyr WebLab"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    SetEnvironmentVariable('CODEXIFYR_DATA_DIR', ExpandConstant('{localappdata}\Codexifyr WebLab\Data'));
  end;
end;
