; Inno Setup script for the Hussaini Secure Chat installer.
; Built automatically by .github/workflows/build-windows.yml (needs the release folder produced by
; packaging/assemble.py). Installs for the current user only - no administrator rights needed - so the
; server can keep its database in the install folder.
#ifndef AppVersion
  #define AppVersion "1.4.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\release\HussainiChat-Windows"
#endif

[Setup]
AppId={{B7C5E1A2-6B1F-4C55-9F3A-4D1D7E0A5C21}
AppName=Hussaini Secure Chat
AppVersion={#AppVersion}
AppPublisher=Hussaini
DefaultDirName={localappdata}\Programs\Hussaini Secure Chat
DefaultGroupName=Hussaini Secure Chat
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\release
OutputBaseFilename=HussainiChat-Setup
SetupIconFile=..\..\client\assets\icon.ico
UninstallDisplayIcon={app}\HussainiChat.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Dirs]
Name: "{app}\data"

[Files]
Source: "{#SourceDir}\HussainiChat.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\HussainiServer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\settings.txt"; DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "{#SourceDir}\README.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\PRESENTATION_GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\start-demo.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\show-database.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\server-tools.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Hussaini Secure Chat\Hussaini Chat"; Filename: "{app}\HussainiChat.exe"
Name: "{autoprograms}\Hussaini Secure Chat\Hussaini Server"; Filename: "{app}\HussainiServer.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\Hussaini Secure Chat\Start demo (server + 2 chat windows)"; Filename: "{app}\start-demo.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\Hussaini Secure Chat\Server owner tools"; Filename: "{app}\server-tools.bat"; WorkingDir: "{app}"
Name: "{autoprograms}\Hussaini Secure Chat\Presentation guide"; Filename: "{app}\PRESENTATION_GUIDE.txt"
Name: "{autoprograms}\Hussaini Secure Chat\Uninstall Hussaini Secure Chat"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Hussaini Chat"; Filename: "{app}\HussainiChat.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\HussainiChat.exe"; Description: "Start Hussaini Chat now"; Flags: nowait postinstall skipifsilent
