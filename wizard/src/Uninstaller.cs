using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Uninstall
    // ----------------------------------------------------------------------
    // Keep user projects and runtime preferences in place.
    internal static class Uninstaller
    {
        private const string Title = AppInfo.DisplayName + " Uninstall";

        public static int Run(Options options)
        {
            string self = Assembly.GetExecutingAssembly().Location;
            string root = Path.GetFullPath(options.InstallDir ?? Path.GetDirectoryName(self)).TrimEnd('\\');

            // --- 1) CONFIRM ---
            if (!FileOps.IsInstallFolder(root))
            {
                Report(options, "No Drawing Intelligence installation was found in " + root + ".", true);
                return 2;
            }
            if (!options.Silent && !options.Confirmed)
            {
                DialogResult answer = MessageBox.Show(
                    "Remove " + AppInfo.DisplayName + " from " + root + "?",
                    Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
                if (answer != DialogResult.Yes)
                {
                    return 1;
                }
            }

            // --- 2) FINISH FROM A TEMPORARY COPY ---
            // Windows cannot delete a running program or a folder that is a working directory.
            string temp = Path.GetTempPath();
            Environment.CurrentDirectory = temp;
            if (FileOps.IsInside(self, root))
            {
                string copy = Path.Combine(temp, "Drawing-Intelligence-Blender-Uninstall-" + Guid.NewGuid().ToString("N").Substring(0, 8) + ".exe");
                File.WriteAllBytes(copy, File.ReadAllBytes(self));
                ProcessStartInfo info = new ProcessStartInfo(copy,
                    "/uninstall /confirmed /dir=\"" + root + "\"" + (options.Silent ? " /silent" : ""));
                info.UseShellExecute = false;
                info.WorkingDirectory = temp;
                using (Process.Start(info))
                {
                }
                return 0;
            }

            // --- 3) REMOVE ---
            // Files go first; the marker and uninstaller stay until the end so a failed run can be retried.
            int exitCode = 0;
            try
            {
                Integration.EnsureNotRunning(root);
                foreach (string name in AppInfo.AppEntries) FileOps.DeletePath(Path.Combine(root, name));
                FileOps.DeletePath(Path.Combine(root, "engine"));
                FileOps.DeletePath(Path.Combine(root, AppInfo.IconName));
                FileOps.DeletePath(Path.Combine(root, AppInfo.LogName));
                Integration.Remove(root);
                FileOps.DeletePath(Path.Combine(root, AppInfo.UninstallerName));
                FileOps.DeletePath(Path.Combine(root, AppInfo.MarkerFile));
                if (Directory.GetFileSystemEntries(root).Length == 0) Directory.Delete(root);
                Report(options, AppInfo.DisplayName + " was removed. Saved projects and runtime preferences were kept.", false);
            }
            catch (Exception ex)
            {
                if (!(ex is InstallException || ex is IOException || ex is UnauthorizedAccessException))
                {
                    throw;
                }
                Report(options, "Some files could not be removed: " + ex.Message +
                    "\n\nClose Drawing Intelligence and any files open from " + root + ", then uninstall again.", true);
                exitCode = 3;
            }

            // --- 4) DELETE THE TEMPORARY COPY AFTER EXIT ---
            if (FileOps.IsInside(self, temp))
            {
                DeleteAfterExit(self);
            }
            return exitCode;
        }

        private static void Report(Options options, string message, bool isError)
        {
            if (!options.Silent)
            {
                MessageBox.Show(message, Title, MessageBoxButtons.OK,
                    isError ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
            }
        }

        private static void DeleteAfterExit(string path)
        {
            try
            {
                string script = "Start-Sleep -Seconds 3; Remove-Item -LiteralPath '" + path.Replace("'", "''") + "' -Force";
                string encoded = Convert.ToBase64String(System.Text.Encoding.Unicode.GetBytes(script));
                ProcessStartInfo info = new ProcessStartInfo("powershell.exe", "-NoProfile -WindowStyle Hidden -EncodedCommand " + encoded);
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                using (Process.Start(info))
                {
                }
            }
            catch (Win32Exception)
            {
            }
        }
    }
}
