#define AppName "NVIDIA GPU Driver Profile Switcher"
#define AppVersion "2.0"
#define PortableName "NVIDIA-GPU-Driver-Profile-Switcher-v2.0-Portable-Windows-x64.exe"
#ifndef AppIdValue
  #define AppIdValue "{{9A365626-8393-4FD5-B731-10BB713E4E42}"
#endif

[Setup]
AppId={#AppIdValue}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=zerØ software / snuffy420
AppPublisherURL=https://github.com/snuffy420/NvidiaGPUProfileSwitcher
AppSupportURL=https://github.com/snuffy420/NvidiaGPUProfileSwitcher/issues
AppUpdatesURL=https://github.com/snuffy420/NvidiaGPUProfileSwitcher/releases
VersionInfoVersion=2.0.0.0
VersionInfoCompany=zerØ software
VersionInfoDescription={#AppName} Setup
DefaultDirName={localappdata}\Programs\NVIDIA GPU Driver Profile Switcher
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\v2.0-github
OutputBaseFilename=NVIDIA-GPU-Driver-Profile-Switcher-v2.0-Setup-Windows-x64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dark windows11 includetitlebar hidebevels
WizardSizePercent=120,115
WizardResizable=no
WizardBackColor=#0f1217
WizardImageBackColor=#0f1217
WizardSmallImageBackColor=#0f1217
WizardImageFile=installer_art\wizard.bmp
WizardSmallImageFile=installer_art\wizard-small.bmp
WizardImageStretch=yes
SetupIconFile=..\assets\app-icon.ico
UninstallDisplayIcon={app}\assets\app-icon.ico
UninstallDisplayName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=no
DisableDirPage=no
LicenseFile=..\LICENSE
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Messages]
WelcomeLabel1=GPU Driver Profile Switcher 2.0
WelcomeLabel2=Install the application for this Windows account.%n%nPython and Qt are included. Setup does not download dependencies or install NVIDIA drivers.%n%nYour saved profiles and driver caches are kept when upgrading.
FinishedHeadingLabel=Installation complete
FinishedLabel=GPU Driver Profile Switcher 2.0 is installed.%n%nOpen the application to prepare profiles or manage cached drivers. Changing GPU or driver state requires Restart as administrator inside the app.
SelectDirDesc=Choose a writable application folder.
SelectDirLabel3=Profiles and settings are saved beside the application. The default folder is writable for this Windows account.

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\portable\{#PortableName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\app-icon.ico"; DestDir: "{app}\assets"; Flags: ignoreversion
Source: "..\docs\PACKAGE_README.md"; DestDir: "{app}"; DestName: "README.md"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\NOTICE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\runtime-licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#PortableName}"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app-icon.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#PortableName}"; WorkingDir: "{app}"; IconFilename: "{app}\assets\app-icon.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#PortableName}"; Description: "Open GPU Driver Profile Switcher"; Flags: nowait postinstall skipifsilent

[Code]
function LoadImageFile(Instance: THandle; FileName: String; ImageType: UINT; Width, Height: Integer; Flags: UINT): THandle;
  external 'LoadImageW@user32.dll stdcall';

procedure InitializeWizard();
var
  LargeIcon, SmallIcon: THandle;
begin
  ExtractTemporaryFile('app-icon.ico');
  LargeIcon := LoadImageFile(0, ExpandConstant('{tmp}\app-icon.ico'), 1, ScaleX(32), ScaleY(32), $10);
  SmallIcon := LoadImageFile(0, ExpandConstant('{tmp}\app-icon.ico'), 1, ScaleX(16), ScaleY(16), $10);
  if LargeIcon <> 0 then SendMessage(WizardForm.Handle, $80, 1, LargeIcon);
  if SmallIcon <> 0 then SendMessage(WizardForm.Handle, $80, 0, SmallIcon);
end;
