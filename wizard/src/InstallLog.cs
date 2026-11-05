using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Thread-safe install log
    // ----------------------------------------------------------------------
    // Lines are buffered until the install folder exists, then written to install.log.
    // A run that fails before that point leaves its log in %TEMP%.
    internal sealed class InstallLog : IDisposable
    {
        private readonly object _lock = new object();
        private readonly List<string> _pending = new List<string>();
        private StreamWriter _writer;
        private string _path;

        // Raised on the writing thread with the unstamped line.
        public event Action<string> LineWritten;

        public string FilePath
        {
            get
            {
                lock (_lock)
                {
                    return _path;
                }
            }
        }

        public void Write(string line)
        {
            string stamped = DateTime.Now.ToString("HH:mm:ss") + "  " + line;
            Action<string> handler;
            lock (_lock)
            {
                if (_writer != null)
                {
                    _writer.WriteLine(stamped);
                }
                else
                {
                    _pending.Add(stamped);
                }
                handler = LineWritten;
            }
            if (handler != null)
            {
                handler(line);
            }
        }

        public void AttachFile(string path)
        {
            lock (_lock)
            {
                if (_writer != null)
                {
                    _writer.Dispose();
                }
                _writer = new StreamWriter(path, false, new UTF8Encoding(false));
                _writer.AutoFlush = true;
                _path = path;
                foreach (string line in _pending)
                {
                    _writer.WriteLine(line);
                }
                _pending.Clear();
            }
        }

        public void Dispose()
        {
            lock (_lock)
            {
                // --- 1) EARLY FAILURE: KEEP THE LOG IN %TEMP% ---
                if (_writer == null && _pending.Count > 0)
                {
                    try
                    {
                        _path = Path.Combine(Path.GetTempPath(), "Drawing-Intelligence-Blender-Setup.log");
                        File.WriteAllLines(_path, _pending.ToArray(), new UTF8Encoding(false));
                    }
                    catch (IOException)
                    {
                        _path = null;
                    }
                    catch (UnauthorizedAccessException)
                    {
                        _path = null;
                    }
                    _pending.Clear();
                }

                // --- 2) CLOSE THE INSTALL LOG ---
                if (_writer != null)
                {
                    _writer.Dispose();
                    _writer = null;
                }
            }
        }
    }
}
