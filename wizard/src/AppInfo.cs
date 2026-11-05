using System;
using System.IO;

namespace DrawingSetup
{
    internal static class AppInfo
    {
        // Product and downloads.
        public const string DisplayName = "Drawing Intelligence (Blender)";
        public const string Version = "0.1.0";
        public const string Publisher = "valleyvarun";
        public const string FolderName = "Drawing-Intelligence-Blender";
        public const string BlenderVersion = "5.2.2";
        public const string BlenderZipUrl =
            "https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip";
        // https://download.blender.org/release/Blender5.2/blender-5.2.2.sha256
        public const string BlenderSha256 = "3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535";
        public const string RepoUrl = "https://github.com/valleyvarun/drawing-intelligence-blender";
        public const string SourceZipUrl = RepoUrl + "/archive/refs/heads/main.zip";
        public const string LicenseUrl = "https://www.blender.org/about/license/";
        public const string SourceUrl = "https://projects.blender.org/blender/blender/src/tag/v5.2.2";

        // Only these entries belong to setup. Projects and runtime preferences are preserved.
        public static readonly string[] AppEntries = {
            "app", "launch.cmd", "run.ps1", "README.md", "THIRD_PARTY_NOTICES.md"
        };
        public const string MarkerFile = ".drawing-intelligence-blender-install";
        public const string UninstallerName = "uninstall.exe";
        public const string IconName = "drawing-intelligence-blender.ico";
        public const string LogName = "install.log";
        public const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\valleyvarun.drawing-intelligence-blender";
        public const long RequiredBytes = 5L * 1024 * 1024 * 1024;
        public const int MaxInstallPathLength = 120;

        public static string DefaultInstallDir
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", FolderName); }
        }

        public static string LaunchScript(string root) { return Path.Combine(root, "launch.cmd"); }
        public static string DesktopShortcut
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), DisplayName + ".lnk"); }
        }
        public static string StartMenuShortcut
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), DisplayName + ".lnk"); }
        }
    }
}
