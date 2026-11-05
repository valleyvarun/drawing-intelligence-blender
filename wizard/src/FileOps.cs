using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // File-system helpers
    // ----------------------------------------------------------------------
    internal static class FileOps
    {
        // --- 1) SAFE DELETE ---
        // Links are removed as links; their targets are never entered.
        public static void DeleteTree(string path)
        {
            DirectoryInfo dir = new DirectoryInfo(path);
            if (dir.Exists)
            {
                DeleteEntry(dir);
            }
        }

        public static void DeletePath(string path)
        {
            if (Directory.Exists(path)) DeleteTree(path);
            else if (File.Exists(path)) DeleteEntry(new FileInfo(path));
        }

        // Empties a folder, keeping the named top-level entries (the install marker, the uninstaller).
        public static void DeleteContents(string path, ICollection<string> keepNames)
        {
            foreach (FileSystemInfo entry in new DirectoryInfo(path).EnumerateFileSystemInfos())
            {
                if (keepNames.Contains(entry.Name, StringComparer.OrdinalIgnoreCase))
                {
                    continue;
                }
                DeleteEntry(entry);
            }
        }

        private static void DeleteEntry(FileSystemInfo entry)
        {
            bool isLink = (entry.Attributes & FileAttributes.ReparsePoint) != 0;
            DirectoryInfo dir = entry as DirectoryInfo;
            if (dir == null)
            {
                // DeleteFile removes a file symlink itself; attributes are only changed on real files.
                if (!isLink && (entry.Attributes & FileAttributes.ReadOnly) != 0)
                {
                    entry.Attributes &= ~FileAttributes.ReadOnly;
                }
                Retry(delegate { File.Delete(entry.FullName); });
                return;
            }
            if (!isLink)
            {
                foreach (FileSystemInfo child in dir.EnumerateFileSystemInfos())
                {
                    DeleteEntry(child);
                }
                if ((dir.Attributes & FileAttributes.ReadOnly) != 0)
                {
                    dir.Attributes &= ~FileAttributes.ReadOnly;
                }
            }
            // Non-recursive delete: RemoveDirectory drops a junction or symlink without touching its target.
            Retry(delegate { Directory.Delete(dir.FullName, false); });
        }

        // Antivirus scanners and indexers briefly lock freshly written files.
        private static void Retry(Action action)
        {
            for (int attempt = 1; ; attempt++)
            {
                try
                {
                    action();
                    return;
                }
                catch (IOException)
                {
                    if (attempt >= 5)
                    {
                        throw;
                    }
                }
                catch (UnauthorizedAccessException)
                {
                    if (attempt >= 5)
                    {
                        throw;
                    }
                }
                Thread.Sleep(200 * attempt);
            }
        }

        // --- 2) MOVE ---
        // Moves every entry of one folder into another on the same volume, merging existing folders.
        public static void MoveContents(string sourceDir, string targetDir, ICollection<string> excludes)
        {
            Directory.CreateDirectory(targetDir);
            foreach (FileSystemInfo entry in new DirectoryInfo(sourceDir).EnumerateFileSystemInfos())
            {
                if (excludes != null && excludes.Contains(entry.Name, StringComparer.OrdinalIgnoreCase))
                {
                    continue;
                }
                string target = Path.Combine(targetDir, entry.Name);
                if (entry is DirectoryInfo)
                {
                    if (Directory.Exists(target))
                    {
                        MoveContents(entry.FullName, target, null);
                    }
                    else
                    {
                        Directory.Move(entry.FullName, target);
                    }
                }
                else
                {
                    if (File.Exists(target))
                    {
                        File.Delete(target);
                    }
                    File.Move(entry.FullName, target);
                }
            }
        }

        // --- 3) QUERIES ---
        public static bool IsInside(string path, string root)
        {
            string fullPath = Path.GetFullPath(path).TrimEnd('\\') + "\\";
            string fullRoot = Path.GetFullPath(root).TrimEnd('\\') + "\\";
            return fullPath.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase);
        }

        public static bool IsInstallFolder(string path)
        {
            return File.Exists(Path.Combine(path, AppInfo.MarkerFile));
        }

        // Returns -1 when the drive cannot be queried.
        public static long FreeBytes(string path)
        {
            try
            {
                return new DriveInfo(Path.GetPathRoot(Path.GetFullPath(path))).AvailableFreeSpace;
            }
            catch (ArgumentException)
            {
                return -1;
            }
            catch (IOException)
            {
                return -1;
            }
            catch (UnauthorizedAccessException)
            {
                return -1;
            }
        }

        public static string FormatBytes(long bytes)
        {
            double gb = bytes / (1024.0 * 1024.0 * 1024.0);
            if (gb >= 1)
            {
                return gb.ToString("0.#") + " GB";
            }
            return (bytes / (1024.0 * 1024.0)).ToString("0.#") + " MB";
        }

        // --- 4) INSTALL TARGET VALIDATION ---
        // Returns an error message, or null when the folder can be used.
        public static string ValidateTarget(string path, out string fullPath)
        {
            fullPath = null;
            string trimmed = (path ?? "").Trim().Trim('"');
            if (trimmed.Length == 0)
            {
                return "Choose a folder to install into.";
            }
            if (trimmed.Length < 3 || trimmed[1] != ':' || (trimmed[2] != '\\' && trimmed[2] != '/'))
            {
                return "Enter a full folder path that starts with a drive letter, such as C:\\Drawing-Intelligence-Blender.";
            }
            try
            {
                fullPath = Path.GetFullPath(trimmed).TrimEnd('\\');
            }
            catch (Exception ex)
            {
                if (ex is ArgumentException || ex is NotSupportedException || ex is PathTooLongException)
                {
                    return "That folder path is not valid.";
                }
                throw;
            }
            if (fullPath.Length <= 3)
            {
                return "Choose a folder, not the top of a drive.";
            }
            if (fullPath.IndexOfAny(new[] { '%', '!', '&', '^', '|', '<', '>', '"' }) >= 0)
            {
                return "Choose a path without command-shell characters such as %, ! or &. Spaces are supported.";
            }
            if (fullPath.Length > AppInfo.MaxInstallPathLength)
            {
                return "Choose a shorter folder path (up to " + AppInfo.MaxInstallPathLength +
                    " characters) to leave room for Blender's files.";
            }
            DirectoryInfo ancestor = new DirectoryInfo(fullPath);
            while (ancestor != null)
            {
                if (ancestor.Exists && (ancestor.Attributes & FileAttributes.ReparsePoint) != 0)
                    return "Choose a folder outside junctions and symbolic links.";
                ancestor = ancestor.Parent;
            }
            if (File.Exists(fullPath))
            {
                return "A file already uses that name. Choose a folder instead.";
            }
            if (HasOtherFiles(fullPath))
            {
                return "That folder already contains other files. Choose an empty or new folder.";
            }
            return null;
        }

        // True for an existing folder that is not a Drawing Intelligence (Blender) install and is not empty (or cannot be read).
        public static bool HasOtherFiles(string path)
        {
            try
            {
                return Directory.Exists(path) && !IsInstallFolder(path) && Directory.EnumerateFileSystemEntries(path).Any();
            }
            catch (IOException)
            {
                return true;
            }
            catch (UnauthorizedAccessException)
            {
                return true;
            }
        }
    }
}
