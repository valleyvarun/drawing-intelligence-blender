using System;
using System.Collections;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("Drawing Intelligence")]
[assembly: AssemblyProduct("Drawing Intelligence")]
[assembly: AssemblyCompany("valleyvarun")]
[assembly: AssemblyCopyright("valleyvarun")]
[assembly: AssemblyVersion("0.2.0.0")]
[assembly: AssemblyFileVersion("0.2.0.0")]

namespace DrawingIntelligence
{
    // ----------------------------------------------------------------------
    // Drawing Intelligence.exe: starts the private engine without a console,
    // shows a splash until the application window is ready, then exits.
    // ----------------------------------------------------------------------
    internal static class Launcher
    {
        [STAThread]
        private static int Main(string[] args)
        {
            NativeMethods.SetCurrentProcessExplicitAppUserModelID(AppIdentity.UserModelId);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            string root = Path.GetDirectoryName(Path.GetFullPath(Assembly.GetExecutingAssembly().Location));
            string engine = Path.Combine(root, AppIdentity.EngineFolder, AppIdentity.Executable);
            string bootstrap = Path.Combine(root, "app", "bootstrap.py");
            string startup = Path.Combine(root, "app", "template", "startup", "bl_app_templates_system",
                AppIdentity.Template, "startup.blend");
            if (!File.Exists(engine) || !File.Exists(bootstrap) || !File.Exists(startup))
            {
                ShowError("The application files are incomplete.\n\nRun setup again to repair " + AppIdentity.Name + ".");
                return 2;
            }

            EngineSession session;
            try
            {
                session = EngineSession.Start(root, engine, bootstrap, ProjectArgument(args));
            }
            catch (Exception ex)
            {
                if (!(ex is Win32Exception || ex is IOException || ex is UnauthorizedAccessException)) throw;
                ShowError(AppIdentity.Name + " could not start.\n\n" + ex.Message);
                return 3;
            }

            using (session)
            using (Splash splash = new Splash(session))
            {
                Application.Run(splash);
                return splash.ExitCode;
            }
        }

        // Accepts one existing project path, as passed by "Open with" or drag and drop.
        private static string ProjectArgument(string[] args)
        {
            foreach (string argument in args)
            {
                if (argument.EndsWith(".blend", StringComparison.OrdinalIgnoreCase) && File.Exists(argument))
                {
                    return Path.GetFullPath(argument);
                }
            }
            return null;
        }

        public static void ShowError(string message)
        {
            MessageBox.Show(message, AppIdentity.Name, MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    // ----------------------------------------------------------------------
    // Engine process
    // ----------------------------------------------------------------------
    internal sealed class EngineSession : IDisposable
    {
        private IntPtr _process;
        public readonly int ProcessId;
        public readonly string LogPath;
        // Where the engine window opens, in physical screen pixels.
        public readonly Rectangle Target;

        private EngineSession(IntPtr process, int processId, string logPath, Rectangle target)
        {
            _process = process;
            ProcessId = processId;
            LogPath = logPath;
            Target = target;
        }

        public static EngineSession Start(string root, string engine, string bootstrap, string project)
        {
            EngineSafety.EnsureThumbnailDirectory();
            string runtime = Path.Combine(root, "runtime");
            Dictionary<string, string> folders = new Dictionary<string, string>();
            foreach (string name in new[] { "config", "scripts", "datafiles", "extensions", "temp", "logs" })
            {
                string folder = Path.Combine(runtime, name);
                Directory.CreateDirectory(folder);
                folders[name] = folder;
            }
            // App settings and temporary files stay here; native thumbnails use the Windows profile.
            Environment.SetEnvironmentVariable("BLENDER_USER_RESOURCES", runtime);
            Environment.SetEnvironmentVariable("BLENDER_USER_CONFIG", folders["config"]);
            Environment.SetEnvironmentVariable("BLENDER_USER_SCRIPTS", folders["scripts"]);
            Environment.SetEnvironmentVariable("BLENDER_USER_DATAFILES", folders["datafiles"]);
            Environment.SetEnvironmentVariable("BLENDER_USER_EXTENSIONS", folders["extensions"]);
            Environment.SetEnvironmentVariable("BLENDER_SYSTEM_SCRIPTS", Path.Combine(root, "app", "template"));
            Environment.SetEnvironmentVariable("TEMP", folders["temp"]);
            Environment.SetEnvironmentVariable("TMP", folders["temp"]);
            Environment.SetEnvironmentVariable("PYTHONDONTWRITEBYTECODE", "1");
            Environment.SetEnvironmentVariable("DRAWING_APP_ROOT", root);
            Environment.SetEnvironmentVariable("DRAWING_APP_CONFIG", folders["config"]);

            List<string> arguments = new List<string> {
                engine, "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                "--app-template", AppIdentity.Template
            };
            Rectangle target;
            arguments.AddRange(WindowArguments(Path.Combine(folders["config"], "drawing-preferences.json"), out target));
            if (project != null) arguments.Add(project);
            arguments.Add("--python");
            arguments.Add(bootstrap);
            StringBuilder commandLine = new StringBuilder();
            foreach (string argument in arguments)
            {
                if (commandLine.Length > 0) commandLine.Append(' ');
                commandLine.Append(Quote(argument));
            }

            string logPath = OpenLogPath(folders["logs"]);
            using (FileStream log = new FileStream(logPath, FileMode.Create, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete))
            {
                IntPtr handle = log.SafeFileHandle.DangerousGetHandle();
                if (!NativeMethods.SetHandleInformation(handle, NativeMethods.HandleFlagInherit, NativeMethods.HandleFlagInherit))
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                NativeMethods.StartupInfo startup = new NativeMethods.StartupInfo();
                startup.cb = Marshal.SizeOf(typeof(NativeMethods.StartupInfo));
                // Windows keeps the engine's first window hidden where it can; the splash covers the rest.
                startup.dwFlags = NativeMethods.StartfUseShowWindow | NativeMethods.StartfUseStdHandles;
                startup.wShowWindow = NativeMethods.SwHide;
                startup.hStdOutput = handle;
                startup.hStdError = handle;
                NativeMethods.ProcessInformation info;
                if (!NativeMethods.CreateProcessW(engine, commandLine, IntPtr.Zero, IntPtr.Zero, true,
                    NativeMethods.CreateNoWindow, IntPtr.Zero, root, ref startup, out info))
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
                NativeMethods.CloseHandle(info.hThread);
                NativeMethods.AllowSetForegroundWindow(info.dwProcessId);
                return new EngineSession(info.hProcess, info.dwProcessId, logPath, target);
            }
        }

        // The window opens where it was closed, or maximized on the monitor under the pointer.
        // Blender's window coordinates measure y upward from the bottom of the virtual desktop.
        private static string[] WindowArguments(string preferences, out Rectangle target)
        {
            int desktopHeight = NativeMethods.GetSystemMetrics(NativeMethods.SmCyVirtualScreen);
            bool maximized = true;
            int[] saved = SavedGeometry(preferences, ref maximized);
            Screen screen;
            if (saved != null)
            {
                target = new Rectangle(saved[0], desktopHeight - saved[1] - saved[3], saved[2], saved[3]);
                screen = Screen.FromRectangle(target);
            }
            else
            {
                screen = Screen.FromPoint(Cursor.Position);
                target = Rectangle.Empty;
            }
            if (saved == null || maximized)
            {
                // The size the window returns to when it is restored from maximized.
                Rectangle work = screen.WorkingArea;
                int width = work.Width * 4 / 5;
                int height = work.Height * 4 / 5;
                target = new Rectangle(work.X + (work.Width - width) / 2, work.Y + (work.Height - height) / 2, width, height);
            }
            List<string> result = new List<string> {
                "--window-geometry",
                target.X.ToString(CultureInfo.InvariantCulture),
                (desktopHeight - target.Bottom).ToString(CultureInfo.InvariantCulture),
                target.Width.ToString(CultureInfo.InvariantCulture),
                target.Height.ToString(CultureInfo.InvariantCulture),
            };
            if (maximized) result.Add("--window-maximized");
            return result.ToArray();
        }

        // window_geometry and window_maximized, as saved by app/drawing_app/core/state.py.
        private static int[] SavedGeometry(string preferences, ref bool maximized)
        {
            try
            {
                if (!File.Exists(preferences)) return null;
                JavaScriptSerializer parser = new JavaScriptSerializer();
                Dictionary<string, object> data = parser.Deserialize<Dictionary<string, object>>(File.ReadAllText(preferences));
                if (data == null) return null;
                object state;
                if (data.TryGetValue("window_maximized", out state) && state is bool) maximized = (bool)state;
                object geometry;
                if (!data.TryGetValue("window_geometry", out geometry)) return null;
                ArrayList list = geometry as ArrayList;
                object[] values = list != null ? list.ToArray() : geometry as object[];
                if (values == null || values.Length != 4) return null;
                int[] result = new int[4];
                for (int index = 0; index < 4; index++)
                {
                    if (!(values[index] is int)) return null;
                    result[index] = (int)values[index];
                    if (result[index] < -100000 || result[index] > 100000 || (index >= 2 && result[index] < 240)) return null;
                }
                return result;
            }
            catch (ArgumentException)
            {
                return null;
            }
            catch (InvalidOperationException)
            {
                return null;
            }
            catch (IOException)
            {
                return null;
            }
            catch (UnauthorizedAccessException)
            {
                return null;
            }
        }

        // Keeps the previous session's log; a second running instance writes its own file.
        private static string OpenLogPath(string folder)
        {
            string current = Path.Combine(folder, "session.log");
            string previous = Path.Combine(folder, "session.previous.log");
            try
            {
                if (File.Exists(current))
                {
                    if (File.Exists(previous)) File.Delete(previous);
                    File.Move(current, previous);
                }
                return current;
            }
            catch (IOException)
            {
                return Path.Combine(folder, "session-" + Process.GetCurrentProcess().Id + ".log");
            }
            catch (UnauthorizedAccessException)
            {
                return Path.Combine(folder, "session-" + Process.GetCurrentProcess().Id + ".log");
            }
        }

        public bool HasExited(out int exitCode)
        {
            exitCode = 0;
            if (NativeMethods.WaitForSingleObject(_process, 0) != NativeMethods.WaitObject0) return false;
            uint code;
            NativeMethods.GetExitCodeProcess(_process, out code);
            exitCode = unchecked((int)code);
            return true;
        }

        // The top-level engine window, if Blender's window system has created it.
        public IntPtr MainWindow(out bool visible)
        {
            IntPtr found = IntPtr.Zero;
            bool shown = false;
            int target = ProcessId;
            NativeMethods.EnumWindowsProc callback = delegate(IntPtr window, IntPtr param)
            {
                uint owner;
                NativeMethods.GetWindowThreadProcessId(window, out owner);
                if (owner != target || NativeMethods.GetWindow(window, NativeMethods.GwOwner) != IntPtr.Zero) return true;
                StringBuilder name = new StringBuilder(64);
                NativeMethods.GetClassNameW(window, name, name.Capacity);
                if (name.ToString() != "GHOST_WindowClass") return true;
                found = window;
                shown = NativeMethods.IsWindowVisible(window);
                return false;
            };
            NativeMethods.EnumWindows(callback, IntPtr.Zero);
            GC.KeepAlive(callback);
            visible = shown;
            return found;
        }

        public void Dispose()
        {
            if (_process != IntPtr.Zero)
            {
                NativeMethods.CloseHandle(_process);
                _process = IntPtr.Zero;
            }
        }

        // CommandLineToArgvW quoting.
        private static string Quote(string argument)
        {
            if (argument.Length > 0 && argument.IndexOfAny(new[] { ' ', '\t', '"' }) < 0) return argument;
            StringBuilder result = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char character in argument)
            {
                if (character == '\\')
                {
                    backslashes++;
                    continue;
                }
                result.Append('\\', character == '"' ? backslashes * 2 + 1 : backslashes);
                result.Append(character);
                backslashes = 0;
            }
            result.Append('\\', backslashes * 2);
            result.Append('"');
            return result.ToString();
        }
    }

    // ----------------------------------------------------------------------
    // Splash
    // ----------------------------------------------------------------------
    internal sealed class Splash : Form
    {
        // The application reveals itself; this only rescues a window that never appears.
        private static readonly TimeSpan RevealTimeout = TimeSpan.FromSeconds(60);
        private const string ReadyProperty = "DrawingIntelligence.Ready";
        private readonly EngineSession _session;
        private readonly Timer _timer;
        private readonly Stopwatch _clock = Stopwatch.StartNew();
        public int ExitCode;

        public Splash(EngineSession session)
        {
            _session = session;
            Text = AppIdentity.Name;
            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = true;
            // The engine window opens behind the splash and is drawn before the splash closes.
            TopMost = true;
            BackColor = Color.FromArgb(30, 31, 34);
            Icon = Resources.AppIcon();
            Rectangle work = Screen.FromRectangle(session.Target).WorkingArea;
            float scale = MonitorScale(work);
            Size size = new Size(Scaled(420, scale), Scaled(240, scale));
            StartPosition = FormStartPosition.Manual;
            Bounds = new Rectangle(work.X + (work.Width - size.Width) / 2, work.Y + (work.Height - size.Height) / 2,
                size.Width, size.Height);

            PictureBox logo = new PictureBox();
            logo.Image = Resources.Logo();
            logo.SizeMode = PictureBoxSizeMode.Zoom;
            logo.Bounds = new Rectangle(Scaled(160, scale), Scaled(30, scale), Scaled(100, scale), Scaled(100, scale));
            Label title = new Label();
            title.Text = AppIdentity.Name;
            title.ForeColor = Color.FromArgb(236, 236, 236);
            title.Font = new Font("Segoe UI", 21f * scale, GraphicsUnit.Pixel);
            title.TextAlign = ContentAlignment.MiddleCenter;
            title.Bounds = new Rectangle(0, Scaled(140, scale), size.Width, Scaled(36, scale));
            Label status = new Label();
            status.Text = "Starting...";
            status.ForeColor = Color.FromArgb(160, 160, 166);
            status.Font = new Font("Segoe UI", 12.5f * scale, GraphicsUnit.Pixel);
            status.TextAlign = ContentAlignment.MiddleCenter;
            status.Bounds = new Rectangle(0, Scaled(180, scale), size.Width, Scaled(24, scale));
            Controls.Add(logo);
            Controls.Add(title);
            Controls.Add(status);

            _timer = new Timer();
            _timer.Interval = 80;
            _timer.Tick += Poll;
        }

        private static int Scaled(int value, float scale)
        {
            return (int)Math.Round(value * scale);
        }

        // The launcher is per-monitor DPI aware: sizes are physical pixels on the target monitor.
        private static float MonitorScale(Rectangle area)
        {
            NativeMethods.Point center = new NativeMethods.Point();
            center.X = area.X + area.Width / 2;
            center.Y = area.Y + area.Height / 2;
            IntPtr monitor = NativeMethods.MonitorFromPoint(center, NativeMethods.MonitorDefaultToNearest);
            uint dpiX;
            uint dpiY;
            if (monitor == IntPtr.Zero || NativeMethods.GetDpiForMonitor(monitor, 0, out dpiX, out dpiY) != 0) return 1f;
            return Math.Max(1f, dpiX / 96f);
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            _timer.Start();
        }

        private void Poll(object sender, EventArgs e)
        {
            int code;
            if (_session.HasExited(out code))
            {
                _timer.Stop();
                ExitCode = code;
                Hide();
                if (code != 0)
                {
                    Launcher.ShowError(AppIdentity.Name + " closed while starting (code " + code + ").\n\n" +
                        LogTail(_session.LogPath) + "\n\nFull log: " + _session.LogPath);
                }
                Close();
                return;
            }
            bool visible;
            IntPtr window = _session.MainWindow(out visible);
            // Set by the application (app/drawing_app/core/desktop.py) once its workspace has drawn.
            if (window != IntPtr.Zero && visible && NativeMethods.GetPropW(window, ReadyProperty) != IntPtr.Zero)
            {
                _timer.Stop();
                Close();
                return;
            }
            if (window != IntPtr.Zero && _clock.Elapsed > RevealTimeout)
            {
                _timer.Stop();
                NativeMethods.ShowWindow(window, NativeMethods.SwShowNormal);
                NativeMethods.SetForegroundWindow(window);
                Close();
            }
        }

        private static string LogTail(string path)
        {
            try
            {
                using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                using (StreamReader reader = new StreamReader(stream))
                {
                    List<string> lines = new List<string>();
                    string line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        lines.Add(line);
                        if (lines.Count > 12) lines.RemoveAt(0);
                    }
                    return string.Join("\n", lines.ToArray());
                }
            }
            catch (IOException)
            {
                return "";
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) _timer.Dispose();
            base.Dispose(disposing);
        }
    }

    internal static class Resources
    {
        private static Stream Open(string name)
        {
            Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("DrawingIntelligence." + name);
            if (stream == null) throw new InvalidOperationException("Missing embedded resource: " + name);
            return stream;
        }

        public static Icon AppIcon()
        {
            using (Stream stream = Open("app.ico"))
            {
                return new Icon(stream);
            }
        }

        public static Image Logo()
        {
            using (Stream stream = Open("app.png"))
            using (Image image = Image.FromStream(stream))
            {
                return new Bitmap(image);
            }
        }
    }

    // ----------------------------------------------------------------------
    // Win32 (processthreadsapi.h, winuser.h, shobjidl_core.h)
    // ----------------------------------------------------------------------
    public static class NativeMethods
    {
        public const uint StartfUseShowWindow = 0x00000001;
        public const uint StartfUseStdHandles = 0x00000100;
        public const short SwHide = 0;
        public const int SwShowNormal = 1;
        public const uint CreateNoWindow = 0x08000000;
        public const uint HandleFlagInherit = 0x00000001;
        public const uint WaitObject0 = 0;
        public const uint GwOwner = 4;

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct StartupInfo
        {
            public int cb;
            public string lpReserved;
            public string lpDesktop;
            public string lpTitle;
            public int dwX;
            public int dwY;
            public int dwXSize;
            public int dwYSize;
            public int dwXCountChars;
            public int dwYCountChars;
            public int dwFillAttribute;
            public uint dwFlags;
            public short wShowWindow;
            public short cbReserved2;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }

        [StructLayout(LayoutKind.Sequential)]
        public struct ProcessInformation
        {
            public IntPtr hProcess;
            public IntPtr hThread;
            public int dwProcessId;
            public int dwThreadId;
        }

        public delegate bool EnumWindowsProc(IntPtr window, IntPtr param);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes,
            IntPtr threadAttributes, bool inheritHandles, uint flags, IntPtr environment, string directory,
            ref StartupInfo startup, out ProcessInformation information);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetHandleInformation(IntPtr handle, uint mask, uint flags);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr handle);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool AllowSetForegroundWindow(int processId);

        [DllImport("user32.dll")]
        public static extern bool EnumWindows(EnumWindowsProc callback, IntPtr param);

        [DllImport("user32.dll")]
        public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);

        [DllImport("user32.dll")]
        public static extern IntPtr GetWindow(IntPtr window, uint command);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetClassNameW(IntPtr window, StringBuilder name, int capacity);

        [DllImport("user32.dll")]
        public static extern bool IsWindowVisible(IntPtr window);

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr window, int command);

        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr window);

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern IntPtr GetPropW(IntPtr window, string name);

        public const int SmCyVirtualScreen = 79;
        public const uint MonitorDefaultToNearest = 2;

        [StructLayout(LayoutKind.Sequential)]
        public struct Point
        {
            public int X;
            public int Y;
        }

        [DllImport("user32.dll")]
        public static extern int GetSystemMetrics(int index);

        [DllImport("user32.dll")]
        public static extern IntPtr MonitorFromPoint(Point point, uint flags);

        [DllImport("shcore.dll")]
        public static extern int GetDpiForMonitor(IntPtr monitor, int type, out uint dpiX, out uint dpiY);

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        public static extern void SetCurrentProcessExplicitAppUserModelID(string appId);
    }
}
