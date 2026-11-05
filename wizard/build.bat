@echo off
setlocal
rem ----------------------------------------------------------------------
rem Build dist\Drawing-Intelligence-Blender-Setup.exe with the C# compiler built into Windows
rem (.NET Framework 4.x), so no SDK or Visual Studio is needed.
rem ----------------------------------------------------------------------
pushd "%~dp0"

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo C# compiler not found. Install .NET Framework 4.8.
    goto :fail
)
if not exist dist mkdir dist

rem --- 1) Convert the app logo (..\imgs\plexus-logo.png) into the icon ---
rem Runs inside PowerShell so no throwaway tool .exe is written (antivirus quarantines those).
powershell -NoProfile -Command "Add-Type -Path 'icon\MakeIcon.cs' -ReferencedAssemblies System.Drawing; exit [MakeIcon]::Run('..\imgs\plexus-logo.png', 'icon')" || goto :fail

rem --- 2) Compile the wizard ---
"%CSC%" /nologo /warnaserror+ /optimize+ /target:winexe /platform:anycpu ^
    /out:dist\Drawing-Intelligence-Blender-Setup.exe ^
    /win32icon:icon\app.ico /win32manifest:app.manifest ^
    /resource:icon\app.ico,DrawingSetup.app.ico /resource:icon\app.png,DrawingSetup.app.png ^
    /r:System.dll /r:System.Core.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll ^
    /r:System.Management.dll /r:System.IO.Compression.dll /r:System.IO.Compression.FileSystem.dll ^
    src\*.cs || goto :fail

echo Built %~dp0dist\Drawing-Intelligence-Blender-Setup.exe
popd
exit /b 0

:fail
echo Build failed.
popd
exit /b 1
