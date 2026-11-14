#define AppName "NVIDIA GPU Driver Profile Switcher"
#define AppVersion "2.2.0"
#define PortableName "NVIDIA-GPU-Driver-Profile-Switcher-v2.2.0-Portable-Windows-x64.exe"
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
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany=zerØ software
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\NVIDIA GPU Driver Profile Switcher
UsePreviousAppDir=no
DefaultGroupName={#AppName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\v{#AppVersion}-github
OutputBaseFilename=NVIDIA-GPU-Driver-Profile-Switcher-v{#AppVersion}-Setup-Windows-x64
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
WelcomeLabel1=GPU Driver Profile Switcher {#AppVersion}
WelcomeLabel2=Install NVIDIA GPU Driver Profile Switcher for all users.%n%nThe application starts with administrator rights because it manages system GPU drivers. Python and Qt are included. Setup does not download dependencies or install NVIDIA drivers.%n%nYour saved profiles and driver caches are kept when upgrading.
FinishedHeadingLabel=Installation complete
FinishedLabel=GPU Driver Profile Switcher {#AppVersion} is installed.%n%nOpen the application to prepare profiles or manage cached drivers. Windows will request administrator approval whenever the app starts. Uninstall removes the application's profiles, driver caches, downloads, settings, and recovery files.
SelectDirDesc=Choose the application folder.
SelectDirLabel3=Profiles and settings are saved beside the application. The application runs as administrator to write its data and manage drivers.

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
Filename: "{app}\{#PortableName}"; Description: "Open GPU Driver Profile Switcher"; Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallDelete]
; Remove app-created state beside the executable as well as Inno's tracked files.
Type: filesandordirs; Name: "{app}\.driver_imports"
Type: filesandordirs; Name: "{app}\driver_cache"
Type: filesandordirs; Name: "{app}\downloads"
Type: filesandordirs; Name: "{app}\driver_history"
Type: filesandordirs; Name: "{app}\recovery"
Type: filesandordirs; Name: "{app}\driver_profiles.json"
Type: filesandordirs; Name: "{app}\ui_settings.json"
Type: filesandordirs; Name: "{app}\last-driver-update-check.json"
Type: filesandordirs; Name: "{app}\pending-driver-switch.json"
Type: filesandordirs; Name: "{app}\update-install.log"
; Remove data left by the older per-user installation after it has been migrated.
Type: filesandordirs; Name: "{localappdata}\Programs\NVIDIA GPU Driver Profile Switcher"

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
