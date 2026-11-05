using System;
using System.IO;
using System.Net;
using System.Threading;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Archive download
    // ----------------------------------------------------------------------
    internal static class Downloader
    {
        // Progress reports (bytes done, total bytes or -1 when the server streams without a length).
        public static void Fetch(string source, string destination, InstallLog log,
            Action<long, long> progress, Func<bool> cancelled)
        {
            // --- 1) LOCAL ARCHIVE (testing before a push) ---
            if (File.Exists(source))
            {
                log.Write("Using local archive " + source);
                File.Copy(source, destination, true);
                return;
            }

            // --- 2) DOWNLOAD WITH RETRIES ---
            log.Write("Downloading " + source);
            for (int attempt = 1; ; attempt++)
            {
                try
                {
                    Download(source, destination, progress, cancelled);
                    log.Write("Downloaded " + FileOps.FormatBytes(new FileInfo(destination).Length));
                    return;
                }
                catch (WebException ex)
                {
                    if (attempt >= 3 || cancelled())
                    {
                        throw new InstallException("Could not download " + source + ": " + ex.Message);
                    }
                    log.Write("Download failed (" + ex.Message + "); retrying.");
                    Thread.Sleep(3000 * attempt);
                }
            }
        }

        private static void Download(string url, string destination, Action<long, long> progress, Func<bool> cancelled)
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
            request.UserAgent = "Drawing-Intelligence-Blender-Setup/" + AppInfo.Version;
            request.Timeout = 60000;
            request.ReadWriteTimeout = 60000;
            if (request.Proxy != null)
            {
                request.Proxy.Credentials = CredentialCache.DefaultCredentials;
            }

            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            using (Stream input = response.GetResponseStream())
            using (FileStream output = File.Create(destination))
            {
                long total = response.ContentLength;
                long done = 0;
                byte[] buffer = new byte[81920];
                int read;
                while ((read = input.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (cancelled())
                    {
                        throw new OperationCanceledException();
                    }
                    output.Write(buffer, 0, read);
                    done += read;
                    progress(done, total);
                }
            }
        }
    }

    // An expected failure with a message written for the person installing.
    internal sealed class InstallException : Exception
    {
        public InstallException(string message) : base(message)
        {
        }
    }
}
