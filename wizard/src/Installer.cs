using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Security.Cryptography;

namespace DrawingSetup
{
    // Downloads are staged before touching an existing installation.
    internal sealed class Installer
    {
        public const int StepCount = 5;
        private readonly string _root;
        private readonly Options _options;
        private readonly InstallLog _log;
        private volatile bool _cancelled;
        private bool _retainBackup;
        private long _lastReported;
        public event Action<int, string> StepStarted;
        public event Action<int> ProgressChanged;
        public event Action<string> DetailChanged;

        public Installer(string root, Options options, InstallLog log)
        {
            _root = root;
            _options = options;
            _log = log;
        }
        public bool Cancelled { get { return _cancelled; } }
        public void Cancel() { _cancelled = true; }
        public static string Describe(Exception ex)
        {
            return ex is OperationCanceledException ? "Installation cancelled." : ex.Message;
        }

        public void Run()
        {
            Begin(1, "Checking the install folder");
            string checkedRoot;
            string error = FileOps.ValidateTarget(_root, out checkedRoot);
            if (error != null) throw new InstallException(error);
            if (!Environment.Is64BitOperatingSystem) throw new InstallException("64-bit Windows is required.");
            Integration.EnsureNotRunning(_root);
            string temp = Path.Combine(Path.GetDirectoryName(_root), ".drawing-blender-setup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temp);
            string payload = Path.Combine(temp, "payload");
            string backup = Path.Combine(temp, "backup");
            Directory.CreateDirectory(payload);
            Directory.CreateDirectory(backup);
            try
            {
                Begin(2, "Downloading Blender " + AppInfo.BlenderVersion + " LTS");
                string engine = FetchAndExtract(_options.BlenderZip, temp, "engine", AppInfo.BlenderSha256);
                RequireFile(Path.Combine(engine, "blender.exe"));
                Directory.Move(engine, Path.Combine(payload, "engine"));

                Begin(3, "Downloading Drawing Intelligence from GitHub");
                string source = FetchAndExtract(_options.SourceZip, temp, "source", null);
                RequireFile(Path.Combine(source, "app", "bootstrap.py"));
                foreach (string name in AppInfo.AppEntries)
                {
                    string entry = Path.Combine(source, name);
                    if (!File.Exists(entry) && !Directory.Exists(entry)) throw new InstallException("App download is missing " + name + ".");
                    MoveEntry(entry, Path.Combine(payload, name));
                }

                Begin(4, "Installing the application");
                Integration.EnsureNotRunning(_root);
                // Recheck after downloads in case another process changed the target.
                error = FileOps.ValidateTarget(_root, out checkedRoot);
                if (error != null) throw new InstallException(error);
                ThrowIfCancelled();
                CommitPayload(payload, backup);

                // Cancellation ends before committing, so a completed install stays usable.
                _log.AttachFile(Path.Combine(_root, AppInfo.LogName));
                NotifyStep(5, "Creating shortcuts");
                Integration.Install(_root, _options.DesktopShortcut, _log);
                Progress(100);
                _log.Write("Installation complete. Projects and runtime preferences were preserved.");
            }
            finally
            {
                try { if (!_retainBackup) FileOps.DeleteTree(temp); }
                catch (IOException ex) { _log.Write("Temporary files remain in " + temp + ": " + ex.Message); }
                catch (UnauthorizedAccessException ex) { _log.Write("Temporary files remain in " + temp + ": " + ex.Message); }
            }
        }

        private void CommitPayload(string payload, string backup)
        {
            Directory.CreateDirectory(_root);
            List<string> installed = new List<string>();
            List<string> saved = new List<string>();
            try
            {
                foreach (string entry in Directory.GetFileSystemEntries(payload))
                {
                    string name = Path.GetFileName(entry);
                    string target = Path.Combine(_root, name);
                    if (File.Exists(target) || Directory.Exists(target))
                    {
                        MoveEntry(target, Path.Combine(backup, name));
                        saved.Add(name);
                    }
                    MoveEntry(entry, target);
                    installed.Add(name);
                }
                File.WriteAllText(Path.Combine(_root, AppInfo.MarkerFile), AppInfo.DisplayName + " " + AppInfo.Version);
            }
            catch
            {
                try
                {
                    foreach (string name in installed) FileOps.DeletePath(Path.Combine(_root, name));
                    foreach (string name in saved) MoveEntry(Path.Combine(backup, name), Path.Combine(_root, name));
                }
                catch (Exception ex)
                {
                    _retainBackup = true;
                    throw new InstallException("Could not restore all previous files. Your backup remains at " + backup + ". " + ex.Message);
                }
                throw;
            }
        }

        private static void MoveEntry(string source, string target)
        {
            if (Directory.Exists(source)) Directory.Move(source, target);
            else File.Move(source, target);
        }

        private string FetchAndExtract(string source, string temp, string name, string expectedHash)
        {
            string zip = Path.Combine(temp, name + ".zip");
            string target = Path.Combine(temp, name);
            _lastReported = -1;
            Downloader.Fetch(source, zip, _log, ReportDownload, delegate { return _cancelled; });
            ThrowIfCancelled();
            if (expectedHash != null)
            {
                Detail("Verifying Blender's SHA-256 checksum");
                using (SHA256 sha = SHA256.Create())
                using (Stream stream = File.OpenRead(zip))
                {
                    string hash = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                    if (hash != expectedHash) throw new InstallException("Blender checksum did not match. Run setup again to retry the download.");
                }
            }
            Progress(-1);
            Detail("Unpacking " + name);
            Directory.CreateDirectory(target);
            using (ZipArchive archive = ZipFile.OpenRead(zip))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    ThrowIfCancelled();
                    string destination = Path.GetFullPath(Path.Combine(target, entry.FullName));
                    if (!FileOps.IsInside(destination, target) || string.Equals(destination.TrimEnd('\\'), target, StringComparison.OrdinalIgnoreCase))
                        throw new InstallException("The archive contains an invalid path.");
                    if (entry.Name.Length == 0) Directory.CreateDirectory(destination);
                    else
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(destination));
                        entry.ExtractToFile(destination);
                    }
                }
            }
            File.Delete(zip);
            string[] entries = Directory.GetFileSystemEntries(target);
            return entries.Length == 1 && Directory.Exists(entries[0]) ? entries[0] : target;
        }

        private static void RequireFile(string path)
        {
            if (!File.Exists(path)) throw new InstallException("The download is missing " + Path.GetFileName(path) + ".");
        }
        private void ThrowIfCancelled() { if (_cancelled) throw new OperationCanceledException(); }
        private void Begin(int step, string title) { ThrowIfCancelled(); NotifyStep(step, title); }
        private void NotifyStep(int step, string title)
        {
            _log.Write("Step " + step + " of " + StepCount + ": " + title);
            if (StepStarted != null) StepStarted(step, title);
            Progress(-1);
        }
        private void Progress(int value) { if (ProgressChanged != null) ProgressChanged(value); }
        private void Detail(string value) { if (DetailChanged != null) DetailChanged(value); }
        private void ReportDownload(long done, long total)
        {
            if (_lastReported >= 0 && done - _lastReported < 256 * 1024 && done != total) return;
            _lastReported = done;
            Progress(total > 0 ? (int)(done * 100 / total) : -1);
            Detail(FileOps.FormatBytes(done) + (total > 0 ? " of " + FileOps.FormatBytes(total) : " downloaded"));
        }
    }
}
