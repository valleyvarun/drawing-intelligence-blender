using System;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.Versioning;
using System.Windows.Forms;

// ----------------------------------------------------------------------
// Assembly identity (shown in the file's Properties > Details)
// ----------------------------------------------------------------------
[assembly: AssemblyTitle("Drawing Intelligence (Blender) Setup")]
[assembly: AssemblyProduct("Drawing Intelligence (Blender)")]
[assembly: AssemblyCompany("valleyvarun")]
[assembly: AssemblyCopyright("valleyvarun")]
[assembly: AssemblyVersion("0.1.0.0")]
[assembly: AssemblyFileVersion("0.1.0.0")]
// Opt into .NET Framework 4.8 runtime behavior (e.g. OS-chosen TLS versions).
[assembly: TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName = ".NET Framework 4.8")]

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Entry point
    // ----------------------------------------------------------------------
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            // Blender bundles deeply nested Python packages.
            AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling", false);
            AppContext.SetSwitch("Switch.System.IO.BlockLongPaths", false);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            // GitHub requires TLS 1.2; add it when the runtime does not defer to the OS.
            if (ServicePointManager.SecurityProtocol != 0)
            {
                ServicePointManager.SecurityProtocol |= (SecurityProtocolType)3072;
            }

            // --- 1) PARSE ---
            Options options;
            try
            {
                options = Options.Parse(args);
            }
            catch (ArgumentException ex)
            {
                if (Array.IndexOf(args, "/silent") < 0)
                {
                    MessageBox.Show(ex.Message + "\n\n" + Options.Usage, AppInfo.DisplayName + " Setup",
                        MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
                return 2;
            }

            // --- 2) DISPATCH ---
            // The installed copy is named uninstall.exe; opening it directly also uninstalls.
            string self = Assembly.GetExecutingAssembly().Location;
            if (string.Equals(Path.GetFileName(self), AppInfo.UninstallerName, StringComparison.OrdinalIgnoreCase))
            {
                options.Uninstall = true;
            }
            if (options.Uninstall)
            {
                return Uninstaller.Run(options);
            }
            if (options.Silent)
            {
                return RunSilent(options);
            }
            Application.Run(new WizardForm(options));
            return 0;
        }

        // Exit codes: 0 installed, 1 cancelled, 2 bad folder or options, 3 failed, 4 license not accepted.
        private static int RunSilent(Options options)
        {
            if (!options.AcceptEula)
            {
                return 4;
            }
            string requested = options.InstallDir ?? Integration.ExistingInstallDir() ?? AppInfo.DefaultInstallDir;
            using (InstallLog log = new InstallLog())
            {
                string root;
                string error = FileOps.ValidateTarget(requested, out root);
                if (error != null)
                {
                    log.Write("Error: " + error);
                    return 2;
                }
                Installer installer = new Installer(root, options, log);
                try
                {
                    installer.Run();
                    return 0;
                }
                catch (Exception ex)
                {
                    log.Write("Error: " + Installer.Describe(ex));
                    if (!(ex is InstallException || ex is OperationCanceledException))
                    {
                        log.Write(ex.ToString());
                    }
                    return installer.Cancelled ? 1 : 3;
                }
            }
        }
    }

    // ----------------------------------------------------------------------
    // Embedded resources (icon/app.ico, icon/app.png)
    // ----------------------------------------------------------------------
    internal static class Resources
    {
        public static Stream Open(string name)
        {
            Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("DrawingSetup." + name);
            if (stream == null)
            {
                throw new InvalidOperationException("Missing embedded resource: " + name);
            }
            return stream;
        }

        public static Icon AppIcon()
        {
            using (Stream stream = Open("app.ico"))
            {
                return new Icon(stream);
            }
        }

        // Copied into a Bitmap so the image does not depend on the closed stream.
        public static Image Logo()
        {
            using (Stream stream = Open("app.png"))
            using (Image image = Image.FromStream(stream))
            {
                return new Bitmap(image);
            }
        }
    }
}
