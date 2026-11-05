using System;
using System.IO;
using System.Management;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;
using Microsoft.Win32;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Windows integration: shortcuts, uninstall entry, running-app check
    // ----------------------------------------------------------------------
    internal static class Integration
    {
        // Start the launcher's console minimized behind the application.
        private const int ShowMinimized = 7;

        // --- 1) INSTALL ---
        public static void Install(string root, bool desktopShortcut, InstallLog log)
        {
            // App icon (the plexus logo) for the shortcuts and Settings > Apps.
            string icon = Path.Combine(root, AppInfo.IconName);
            using (Stream resource = Resources.Open("app.ico"))
            using (FileStream file = File.Create(icon))
            {
                resource.CopyTo(file);
            }

            // The uninstaller is a copy of this program; a byte copy leaves the download's web mark behind.
            string uninstaller = Path.Combine(root, AppInfo.UninstallerName);
            string self = Assembly.GetExecutingAssembly().Location;
            if (!string.Equals(Path.GetFullPath(self), uninstaller, StringComparison.OrdinalIgnoreCase))
            {
                File.WriteAllBytes(uninstaller, File.ReadAllBytes(self));
            }

            string target = AppInfo.LaunchScript(root);
            if (desktopShortcut)
            {
                CreateShortcut(AppInfo.DesktopShortcut, target, icon);
                log.Write("Created " + AppInfo.DesktopShortcut);
            }
            CreateShortcut(AppInfo.StartMenuShortcut, target, icon);
            log.Write("Created " + AppInfo.StartMenuShortcut);

            RegisterUninstall(root, icon, uninstaller);
            log.Write("Added " + AppInfo.DisplayName + " to Settings > Apps.");
        }

        // --- 2) REMOVE ---
        public static void Remove(string root)
        {
            foreach (string shortcut in new[] { AppInfo.DesktopShortcut, AppInfo.StartMenuShortcut })
            {
                if (File.Exists(shortcut) && ShortcutPointsInto(shortcut, root))
                {
                    File.Delete(shortcut);
                }
            }

            // Leave the entry alone if it belongs to an installation in another folder.
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(AppInfo.UninstallKey))
            {
                if (key == null)
                {
                    return;
                }
                string location = key.GetValue("InstallLocation") as string;
                if (location != null && !string.Equals(location.TrimEnd('\\'), root, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
            }
            Registry.CurrentUser.DeleteSubKeyTree(AppInfo.UninstallKey, false);
        }

        // Folder of the registered installation, when it still exists.
        public static string ExistingInstallDir()
        {
            using (RegistryKey key = Registry.CurrentUser.OpenSubKey(AppInfo.UninstallKey))
            {
                string location = key == null ? null : key.GetValue("InstallLocation") as string;
                return location != null && FileOps.IsInstallFolder(location) ? location : null;
            }
        }

        // --- 3) RUNNING-APP CHECK ---
        public static void EnsureNotRunning(string root)
        {
            if (IsRunning(root))
            {
                throw new InstallException("Drawing Intelligence (Blender) is running. Close it, then try again.");
            }
        }

        // Match the isolated app path in Blender's command line.
        private static bool IsRunning(string root)
        {
            string needle = root.TrimEnd('\\') + "\\";
            try
            {
                using (ManagementObjectSearcher searcher =
                    new ManagementObjectSearcher("SELECT CommandLine FROM Win32_Process WHERE Name = 'blender.exe'"))
                using (ManagementObjectCollection results = searcher.Get())
                {
                    foreach (ManagementBaseObject process in results)
                    {
                        using (process)
                        {
                            string commandLine = process["CommandLine"] as string;
                            if (commandLine != null && commandLine.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
                            {
                                return true;
                            }
                        }
                    }
                }
            }
            catch (ManagementException)
            {
            }
            catch (COMException)
            {
            }
            return false;
        }

        // ----------------------------------------------------------------------
        // Helpers
        // ----------------------------------------------------------------------
        private static void RegisterUninstall(string root, string icon, string uninstaller)
        {
            using (RegistryKey key = Registry.CurrentUser.CreateSubKey(AppInfo.UninstallKey))
            {
                key.SetValue("DisplayName", AppInfo.DisplayName);
                key.SetValue("DisplayVersion", AppInfo.Version);
                key.SetValue("Publisher", AppInfo.Publisher);
                key.SetValue("DisplayIcon", icon);
                key.SetValue("InstallLocation", root);
                key.SetValue("InstallDate", DateTime.Now.ToString("yyyyMMdd"));
                key.SetValue("URLInfoAbout", AppInfo.RepoUrl);
                key.SetValue("UninstallString", "\"" + uninstaller + "\" /uninstall");
                key.SetValue("QuietUninstallString", "\"" + uninstaller + "\" /uninstall /silent");
                key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
        }

        private static void CreateShortcut(string path, string target, string icon)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            IShellLinkW link = (IShellLinkW)new ShellLink();
            try
            {
                link.SetPath(target);
                link.SetWorkingDirectory(Path.GetDirectoryName(target));
                link.SetIconLocation(icon, 0);
                link.SetDescription("Open " + AppInfo.DisplayName);
                link.SetShowCmd(ShowMinimized);
                ((IPersistFile)link).Save(path, true);
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }

        private static bool ShortcutPointsInto(string path, string root)
        {
            IShellLinkW link = (IShellLinkW)new ShellLink();
            try
            {
                ((IPersistFile)link).Load(path, 0);
                StringBuilder target = new StringBuilder(1024);
                link.GetPath(target, target.Capacity, IntPtr.Zero, 0);
                return target.Length > 0 && FileOps.IsInside(target.ToString(), root);
            }
            catch (COMException)
            {
                return false;
            }
            finally
            {
                Marshal.FinalReleaseComObject(link);
            }
        }

        // ----------------------------------------------------------------------
        // Shell link COM interop (shobjidl_core.h)
        // ----------------------------------------------------------------------
        [ComImport]
        [Guid("00021401-0000-0000-C000-000000000046")]
        private class ShellLink
        {
        }

        [ComImport]
        [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        [Guid("000214F9-0000-0000-C000-000000000046")]
        private interface IShellLinkW
        {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder file, int maxChars, IntPtr findData, uint flags);
            void GetIDList(out IntPtr idList);
            void SetIDList(IntPtr idList);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder name, int maxChars);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string name);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder dir, int maxChars);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string dir);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder args, int maxChars);
            void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string args);
            void GetHotkey(out short hotkey);
            void SetHotkey(short hotkey);
            void GetShowCmd(out int showCmd);
            void SetShowCmd(int showCmd);
            void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder iconPath, int maxChars, out int iconIndex);
            void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string iconPath, int iconIndex);
            void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string relativePath, int reserved);
            void Resolve(IntPtr hwnd, uint flags);
            void SetPath([MarshalAs(UnmanagedType.LPWStr)] string file);
        }
    }
}
