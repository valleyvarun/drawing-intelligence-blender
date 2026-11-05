using System;
using System.Collections.Concurrent;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace DrawingSetup
{
    // ----------------------------------------------------------------------
    // Setup wizard window
    // ----------------------------------------------------------------------
    // Welcome -> License -> Location -> Progress -> Finish
    internal sealed class WizardForm : Form
    {
        private enum Page
        {
            Welcome,
            License,
            Location,
            Progress,
            Finish,
        }

        // Characters kept in the on-screen log; install.log keeps everything.
        private const int LogLimit = 400000;

        private readonly Options _options;
        private readonly ConcurrentQueue<string> _pendingLines = new ConcurrentQueue<string>();
        private readonly System.Windows.Forms.Timer _flushTimer = new System.Windows.Forms.Timer();
        private readonly Font _titleFont = new Font("Segoe UI Semibold", 12F);
        private readonly Font _strongFont = new Font("Segoe UI Semibold", 9F);
        private readonly Font _logFont = new Font("Consolas", 8.25F);

        // Frame
        private readonly Label _title = new Label();
        private readonly Label _subtitle = new Label();
        private readonly Panel _body = new Panel();
        private readonly Button _back = new Button();
        private readonly Button _next = new Button();
        private readonly Button _cancel = new Button();

        // Pages and their inputs
        private readonly TableLayoutPanel _welcomePage;
        private readonly TableLayoutPanel _licensePage;
        private readonly TableLayoutPanel _locationPage;
        private readonly TableLayoutPanel _progressPage;
        private readonly TableLayoutPanel _finishPage;
        private readonly CheckBox _accept = new CheckBox();
        private readonly TextBox _folder = new TextBox();
        private readonly CheckBox _desktop = new CheckBox();
        private readonly Label _space = new Label();
        private readonly Label _step = new Label();
        private readonly ProgressBar _bar = new ProgressBar();
        private readonly Label _detail = new Label();
        private readonly TextBox _logBox = new TextBox();
        private readonly Label _result = new Label();
        private readonly CheckBox _launch = new CheckBox();
        private readonly LinkLabel _openLog = new LinkLabel();

        // Install state
        private Page _page;
        private Installer _installer;
        private InstallLog _log;
        private string _root;
        private bool _installing;
        private bool _closeWhenStopped;

        public WizardForm(Options options)
        {
            _options = options;

            // --- 1) WINDOW ---
            SuspendLayout();
            AutoScaleDimensions = new SizeF(96F, 96F);
            AutoScaleMode = AutoScaleMode.Dpi;
            Font = new Font("Segoe UI", 9F);
            Text = AppInfo.DisplayName + " Setup";
            Icon = Resources.AppIcon();
            ClientSize = new Size(760, 520);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;

            // --- 2) FRAME: header, page area, buttons ---
            TableLayoutPanel frame = new TableLayoutPanel();
            frame.Dock = DockStyle.Fill;
            frame.Margin = Padding.Empty;
            frame.ColumnCount = 1;
            frame.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            frame.RowCount = 5;
            frame.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            frame.RowStyles.Add(new RowStyle(SizeType.Absolute, 1F));
            frame.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            frame.RowStyles.Add(new RowStyle(SizeType.Absolute, 1F));
            frame.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            frame.Controls.Add(BuildHeader(), 0, 0);
            frame.Controls.Add(Rule(), 0, 1);
            frame.Controls.Add(_body, 0, 2);
            frame.Controls.Add(Rule(), 0, 3);
            frame.Controls.Add(BuildButtons(), 0, 4);
            _body.Dock = DockStyle.Fill;
            _body.Margin = Padding.Empty;
            _body.Padding = new Padding(28, 18, 28, 8);

            // --- 3) PAGES ---
            _welcomePage = BuildWelcome();
            _licensePage = BuildLicense();
            _locationPage = BuildLocation();
            _progressPage = BuildProgress();
            _finishPage = BuildFinish();
            foreach (Control page in new Control[] { _welcomePage, _licensePage, _locationPage, _progressPage, _finishPage })
            {
                _body.Controls.Add(page);
            }
            Controls.Add(frame);

            // --- 4) LOG REFRESH ---
            _flushTimer.Interval = 250;
            _flushTimer.Tick += delegate { FlushLog(); };

            ResumeLayout(false);
            PerformLayout();
            ShowPage(Page.Welcome, "Drawing Intelligence — Blender Setup",
                "A work-in-progress 3D sketching app powered by Blender.");
        }

        // ----------------------------------------------------------------------
        // Layout
        // ----------------------------------------------------------------------
        private Control BuildHeader()
        {
            TableLayoutPanel header = new TableLayoutPanel();
            header.Dock = DockStyle.Fill;
            header.AutoSize = true;
            header.Margin = Padding.Empty;
            header.Padding = new Padding(24, 14, 18, 14);
            header.BackColor = SystemColors.Window;
            header.ColumnCount = 2;
            header.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            header.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            header.RowCount = 2;
            header.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            header.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            _title.AutoSize = true;
            _title.Font = _titleFont;
            _title.Margin = new Padding(0, 2, 0, 2);
            _subtitle.AutoSize = true;
            _subtitle.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _subtitle.Margin = Padding.Empty;

            PictureBox logo = new PictureBox();
            logo.Image = Resources.Logo();
            logo.SizeMode = PictureBoxSizeMode.Zoom;
            logo.Size = new Size(48, 48);
            logo.Margin = new Padding(12, 0, 0, 0);

            header.Controls.Add(_title, 0, 0);
            header.Controls.Add(_subtitle, 0, 1);
            header.Controls.Add(logo, 1, 0);
            header.SetRowSpan(logo, 2);
            return header;
        }

        private Control BuildButtons()
        {
            FlowLayoutPanel buttons = new FlowLayoutPanel();
            buttons.Dock = DockStyle.Fill;
            buttons.AutoSize = true;
            buttons.FlowDirection = FlowDirection.RightToLeft;
            buttons.Margin = Padding.Empty;
            buttons.Padding = new Padding(12, 10, 12, 10);
            foreach (Button button in new[] { _cancel, _next, _back })
            {
                button.Size = new Size(92, 28);
                button.Margin = new Padding(8, 0, 0, 0);
                button.UseVisualStyleBackColor = true;
                buttons.Controls.Add(button);
            }
            // Right-to-left flow adds Cancel first; tab order still runs Back, Next, Cancel.
            _back.TabIndex = 0;
            _next.TabIndex = 1;
            _cancel.TabIndex = 2;
            _cancel.Text = "Cancel";
            _back.Text = "Back";
            _back.Click += delegate { OnBack(); };
            _next.Click += delegate { OnNext(); };
            _cancel.Click += delegate { OnCancel(); };
            AcceptButton = _next;
            return buttons;
        }

        private TableLayoutPanel BuildWelcome()
        {
            TableLayoutPanel page = NewPage();
            AddRow(page, Paragraph("This wizard installs Drawing Intelligence (Blender) on this PC. It will:"));
            AddRow(page, Bullet("Download Blender " + AppInfo.BlenderVersion + " LTS from blender.org and verify its checksum."));
            AddRow(page, Bullet("Download the drawing app from github.com/valleyvarun/drawing-intelligence-blender."));
            AddRow(page, Bullet("Install a private Blender runtime and add shortcuts and an uninstall entry."));
            AddRow(page, Paragraph("Stay connected until setup finishes. Allow about 5 GB of free disk space during setup. " +
                "No administrator rights, Git, or build tools are needed."), 8);
            AddFiller(page);
            return page;
        }

        private TableLayoutPanel BuildLicense()
        {
            TableLayoutPanel page = NewPage();
            AddRow(page, Paragraph("Setup downloads an official, unmodified Blender runtime. " +
                "Blender is free and open-source software; its license files stay with the engine."));
            AddRow(page, Link("Blender license information", AppInfo.LicenseUrl));
            AddRow(page, Link("Blender " + AppInfo.BlenderVersion + " source code", AppInfo.SourceUrl));
            AddRow(page, Paragraph("Drawing Intelligence is an independent work-in-progress project by Varun SA. " +
                "It is not affiliated with or endorsed by the Blender Foundation."), 8);
            _accept.Text = "I have read the Blender license information";
            _accept.AutoSize = true;
            _accept.Margin = new Padding(0, 6, 0, 0);
            _accept.Checked = _options.AcceptEula;
            _accept.CheckedChanged += delegate { _next.Enabled = _accept.Checked; };
            AddRow(page, _accept);
            AddFiller(page);
            return page;
        }

        private TableLayoutPanel BuildLocation()
        {
            TableLayoutPanel page = NewPage();
            AddRow(page, Paragraph("Install Drawing Intelligence (Blender) in this folder:"));

            // Folder box and Browse button share one row.
            TableLayoutPanel row = new TableLayoutPanel();
            row.AutoSize = true;
            row.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            row.Margin = new Padding(0, 0, 0, 8);
            row.ColumnCount = 2;
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            row.RowCount = 1;
            row.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            _folder.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _folder.Margin = new Padding(0, 1, 8, 0);
            _folder.Text = _options.InstallDir ?? Integration.ExistingInstallDir() ?? AppInfo.DefaultInstallDir;
            _folder.TextChanged += delegate { UpdateSpace(); };
            Button browse = new Button();
            browse.Text = "Browse...";
            browse.Size = new Size(92, 26);
            browse.Margin = Padding.Empty;
            browse.UseVisualStyleBackColor = true;
            browse.Click += delegate { Browse(); };
            row.Controls.Add(_folder, 0, 0);
            row.Controls.Add(browse, 1, 0);
            AddRow(page, row);

            _space.AutoSize = true;
            _space.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _space.ForeColor = SystemColors.GrayText;
            _space.Margin = new Padding(0, 0, 0, 14);
            AddRow(page, _space);

            _desktop.Text = "Create a desktop shortcut";
            _desktop.AutoSize = true;
            _desktop.Checked = _options.DesktopShortcut;
            _desktop.Margin = new Padding(0, 0, 0, 14);
            AddRow(page, _desktop);

            AddRow(page, Paragraph("Spaces in the path are supported. Updating replaces application files and preserves runtime preferences and saved projects."));
            AddFiller(page);
            return page;
        }

        private TableLayoutPanel BuildProgress()
        {
            TableLayoutPanel page = NewPage();
            _step.AutoSize = true;
            _step.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _step.Font = _strongFont;
            _step.Margin = new Padding(0, 0, 0, 6);
            AddRow(page, _step);

            _bar.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _bar.Height = 18;
            _bar.Margin = new Padding(0, 0, 0, 6);
            AddRow(page, _bar);

            _detail.AutoSize = true;
            _detail.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _detail.ForeColor = SystemColors.GrayText;
            _detail.Margin = new Padding(0, 0, 0, 8);
            AddRow(page, _detail);

            _logBox.Dock = DockStyle.Fill;
            _logBox.Multiline = true;
            _logBox.ReadOnly = true;
            _logBox.WordWrap = false;
            _logBox.ScrollBars = ScrollBars.Both;
            _logBox.Font = _logFont;
            _logBox.BackColor = SystemColors.Window;
            _logBox.Margin = Padding.Empty;
            page.RowCount++;
            page.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            page.Controls.Add(_logBox, 0, page.RowCount - 1);
            return page;
        }

        private TableLayoutPanel BuildFinish()
        {
            TableLayoutPanel page = NewPage();
            _result.AutoSize = true;
            _result.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            _result.Margin = new Padding(0, 0, 0, 14);
            AddRow(page, _result);

            _launch.Text = "Open Drawing Intelligence (Blender) now";
            _launch.AutoSize = true;
            _launch.Margin = new Padding(0, 0, 0, 10);
            AddRow(page, _launch);

            _openLog.Text = "Open the install log";
            _openLog.AutoSize = true;
            _openLog.Margin = Padding.Empty;
            _openLog.LinkClicked += delegate { OpenPath(_log == null ? null : _log.FilePath); };
            AddRow(page, _openLog);
            AddFiller(page);
            return page;
        }

        // --- Layout helpers ---
        private static TableLayoutPanel NewPage()
        {
            TableLayoutPanel page = new TableLayoutPanel();
            page.Dock = DockStyle.Fill;
            page.Margin = Padding.Empty;
            page.Visible = false;
            page.ColumnCount = 1;
            page.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            return page;
        }

        private static void AddRow(TableLayoutPanel page, Control control)
        {
            page.RowCount++;
            page.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            page.Controls.Add(control, 0, page.RowCount - 1);
        }

        private static void AddRow(TableLayoutPanel page, Control control, int topMargin)
        {
            control.Margin = new Padding(control.Margin.Left, topMargin, control.Margin.Right, control.Margin.Bottom);
            AddRow(page, control);
        }

        // An empty stretching row keeps the content pinned to the top.
        private static void AddFiller(TableLayoutPanel page)
        {
            page.RowCount++;
            page.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
        }

        // Anchored left and right inside a table column, an auto-size label wraps at the column width.
        private static Label Paragraph(string text)
        {
            Label label = new Label();
            label.AutoSize = true;
            label.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            label.Margin = new Padding(0, 0, 0, 10);
            label.Text = text;
            return label;
        }

        // Dot and text in separate columns so wrapped lines hang under the text.
        private static Control Bullet(string text)
        {
            TableLayoutPanel row = new TableLayoutPanel();
            row.AutoSize = true;
            row.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            row.Margin = new Padding(12, 0, 0, 6);
            row.ColumnCount = 2;
            row.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            row.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
            row.RowCount = 1;
            row.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            Label dot = new Label();
            dot.AutoSize = true;
            dot.Margin = new Padding(0, 0, 4, 0);
            dot.Text = "•";
            Label label = Paragraph(text);
            label.Margin = Padding.Empty;
            row.Controls.Add(dot, 0, 0);
            row.Controls.Add(label, 1, 0);
            return row;
        }

        private static LinkLabel Link(string text, string url)
        {
            LinkLabel link = new LinkLabel();
            link.Text = text;
            link.AutoSize = true;
            link.Margin = new Padding(12, 0, 0, 8);
            link.LinkClicked += delegate { OpenPath(url); };
            return link;
        }

        private static Control Rule()
        {
            Panel line = new Panel();
            line.Dock = DockStyle.Fill;
            line.Margin = Padding.Empty;
            line.BackColor = SystemColors.ControlLight;
            return line;
        }

        // ----------------------------------------------------------------------
        // Navigation
        // ----------------------------------------------------------------------
        private void ShowPage(Page page, string title, string subtitle)
        {
            _page = page;
            _title.Text = title;
            _subtitle.Text = subtitle;
            _welcomePage.Visible = page == Page.Welcome;
            _licensePage.Visible = page == Page.License;
            _locationPage.Visible = page == Page.Location;
            _progressPage.Visible = page == Page.Progress;
            _finishPage.Visible = page == Page.Finish;

            _back.Visible = page == Page.License || page == Page.Location;
            _next.Text = page == Page.Location ? "Install" : page == Page.Finish ? "Finish" : "Next";
            _next.Enabled = page != Page.Progress && (page != Page.License || _accept.Checked);
            _cancel.Visible = page != Page.Finish;
            _cancel.Enabled = true;
            if (page == Page.Location)
            {
                UpdateSpace();
            }
            // ActiveControl also works before the window is shown, unlike Focus().
            if (_next.Enabled)
            {
                ActiveControl = _next;
            }
            else if (page == Page.License)
            {
                ActiveControl = _accept;
            }
        }

        private void OnBack()
        {
            if (_page == Page.License)
            {
                ShowWelcome();
            }
            else if (_page == Page.Location)
            {
                ShowLicense();
            }
        }

        private void OnNext()
        {
            switch (_page)
            {
                case Page.Welcome:
                    ShowLicense();
                    break;
                case Page.License:
                    ShowPage(Page.Location, "Install location", "Choose where to put Drawing Intelligence (Blender).");
                    break;
                case Page.Location:
                    string root;
                    if (ConfirmLocation(out root))
                    {
                        StartInstall(root);
                    }
                    break;
                case Page.Finish:
                    if (_launch.Visible && _launch.Checked)
                    {
                        Launch();
                    }
                    Close();
                    break;
            }
        }

        private void OnCancel()
        {
            if (!_installing)
            {
                Close();
                return;
            }
            if (ConfirmStop())
            {
                StopInstall();
            }
        }

        private void ShowWelcome()
        {
            ShowPage(Page.Welcome, "Drawing Intelligence — Blender Setup",
                "A work-in-progress 3D sketching app powered by Blender.");
        }

        private void ShowLicense()
        {
            ShowPage(Page.License, "License terms", "Drawing Intelligence (Blender) uses its own Blender runtime.");
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            base.OnFormClosing(e);
            if (!_installing)
            {
                return;
            }
            // Closing mid-install stops the build first, then closes.
            e.Cancel = true;
            if (!_closeWhenStopped && ConfirmStop())
            {
                _closeWhenStopped = true;
                StopInstall();
            }
        }

        // ----------------------------------------------------------------------
        // Location page
        // ----------------------------------------------------------------------
        private void Browse()
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Choose a folder for Drawing Intelligence (Blender).";
                dialog.ShowNewFolderButton = true;
                string current = _folder.Text.Trim();
                string parent = Directory.Exists(current) ? current : Path.GetDirectoryName(current);
                if (!string.IsNullOrEmpty(parent) && Directory.Exists(parent))
                {
                    dialog.SelectedPath = parent;
                }
                if (dialog.ShowDialog(this) != DialogResult.OK)
                {
                    return;
                }
                // A folder with other files gets its own Drawing-Intelligence-Blender subfolder.
                string chosen = dialog.SelectedPath;
                if (FileOps.HasOtherFiles(chosen))
                {
                    chosen = Path.Combine(chosen, AppInfo.FolderName);
                }
                _folder.Text = chosen;
            }
        }

        private void UpdateSpace()
        {
            string root;
            string needed = "The first install needs about " + FileOps.FormatBytes(AppInfo.RequiredBytes) +
                ", including temporary downloads and the Blender runtime.";
            string error = FileOps.ValidateTarget(_folder.Text, out root);
            if (error != null)
            {
                _space.Text = error;
                return;
            }
            long free = FileOps.FreeBytes(root);
            _space.Text = (free >= 0 ? FileOps.FormatBytes(free) + " free on " + Path.GetPathRoot(root) + ". " : "") + needed;
        }

        private bool ConfirmLocation(out string root)
        {
            string error = FileOps.ValidateTarget(_folder.Text, out root);
            if (error != null)
            {
                MessageBox.Show(this, error, Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
            long free = FileOps.FreeBytes(root);
            if (free >= 0 && free < AppInfo.RequiredBytes)
            {
                string message = "Only " + FileOps.FormatBytes(free) + " is free on " + Path.GetPathRoot(root) +
                    ". Setup needs about " + FileOps.FormatBytes(AppInfo.RequiredBytes) + ". Install anyway?";
                if (MessageBox.Show(this, message, Text, MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes)
                {
                    return false;
                }
            }
            if (FileOps.IsInstallFolder(root))
            {
                string message = "Drawing Intelligence (Blender) is already installed in " + root + ". Setup will replace it with the latest version.";
                if (MessageBox.Show(this, message, Text, MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK)
                {
                    return false;
                }
            }
            return true;
        }

        // ----------------------------------------------------------------------
        // Install
        // ----------------------------------------------------------------------
        private async void StartInstall(string root)
        {
            // --- 1) WIRE THE INSTALLER TO THE UI ---
            _root = root;
            _options.DesktopShortcut = _desktop.Checked;
            _log = new InstallLog();
            _log.LineWritten += delegate(string line) { _pendingLines.Enqueue(line); };
            _installer = new Installer(root, _options, _log);
            _installer.StepStarted += delegate(int step, string title)
            {
                Post(delegate
                {
                    _step.Text = "Step " + step + " of " + Installer.StepCount + ": " + title;
                    _detail.Text = "";
                });
            };
            _installer.ProgressChanged += delegate(int percent) { Post(delegate { SetProgress(percent); }); };
            _installer.DetailChanged += delegate(string text) { Post(delegate { _detail.Text = text; }); };

            _installing = true;
            ShowPage(Page.Progress, "Installing Drawing Intelligence (Blender)", "This can take a while. You can keep using your PC.");
            _flushTimer.Start();

            // --- 2) RUN ON A WORKER THREAD ---
            Exception failure = null;
            try
            {
                await Task.Run(new Action(_installer.Run));
            }
            catch (Exception ex)
            {
                failure = ex;
                _log.Write("Error: " + Installer.Describe(ex));
                if (!(ex is InstallException || ex is OperationCanceledException))
                {
                    _log.Write(ex.ToString());
                }
            }

            // --- 3) FINISH ---
            _log.Dispose();
            _installing = false;
            _flushTimer.Stop();
            FlushLog();
            ShowFinish(failure);
            if (_closeWhenStopped)
            {
                Close();
            }
        }

        private void StopInstall()
        {
            _cancel.Enabled = false;
            _detail.Text = "Stopping...";
            _installer.Cancel();
        }

        private bool ConfirmStop()
        {
            string message = "Stop installing Drawing Intelligence (Blender)? Temporary downloads will be removed. Run setup again to retry.";
            return MessageBox.Show(this, message, Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question) == DialogResult.Yes;
        }

        private void ShowFinish(Exception failure)
        {
            bool succeeded = failure == null;
            _launch.Visible = succeeded;
            _launch.Checked = succeeded;
            _openLog.Visible = _log.FilePath != null;
            if (succeeded)
            {
                string where = _options.DesktopShortcut
                    ? "Open it from the shortcut on your desktop or from the Start menu."
                    : "Open it from the Start menu.";
                _result.Text = "Drawing Intelligence (Blender) is installed in " + _root + ". " + where + "\n\n" +
                    "To update to the latest version, run this setup again. " +
                    "To remove the app, go to Settings > Apps > Installed apps.";
                ShowPage(Page.Finish, "Setup complete", AppInfo.DisplayName + " is ready.");
            }
            else if (failure is OperationCanceledException)
            {
                _result.Text = "Drawing Intelligence (Blender) was not installed. Run setup again to retry. Your projects and preferences were preserved.";
                ShowPage(Page.Finish, "Setup cancelled", "Nothing was added to your desktop or Start menu.");
            }
            else
            {
                _result.Text = Installer.Describe(failure) + "\n\nRun setup again to retry. " +
                    "The install log shows what happened.";
                ShowPage(Page.Finish, "Setup did not finish", "Drawing Intelligence (Blender) could not be installed.");
            }
            _next.Text = succeeded ? "Finish" : "Close";
        }

        private void Launch()
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(AppInfo.LaunchScript(_root));
                info.WorkingDirectory = _root;
                info.UseShellExecute = true;
                info.WindowStyle = ProcessWindowStyle.Minimized;
                using (Process.Start(info))
                {
                }
            }
            catch (Win32Exception ex)
            {
                MessageBox.Show(this, "Drawing Intelligence (Blender) could not start: " + ex.Message, Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        // ----------------------------------------------------------------------
        // UI helpers
        // ----------------------------------------------------------------------
        private void Post(Action action)
        {
            if (IsHandleCreated && !IsDisposed)
            {
                BeginInvoke(action);
            }
        }

        private void SetProgress(int percent)
        {
            if (percent < 0)
            {
                _bar.Style = ProgressBarStyle.Marquee;
                _bar.MarqueeAnimationSpeed = 30;
                return;
            }
            _bar.Style = ProgressBarStyle.Continuous;
            _bar.Value = Math.Max(0, Math.Min(100, percent));
        }

        private void FlushLog()
        {
            if (_pendingLines.IsEmpty)
            {
                return;
            }
            StringBuilder text = new StringBuilder();
            string line;
            while (_pendingLines.TryDequeue(out line))
            {
                text.Append(line).Append("\r\n");
            }
            // Keep the newest half when the box grows too large.
            if (_logBox.TextLength + text.Length > LogLimit)
            {
                string kept = _logBox.Text;
                _logBox.Text = kept.Substring(Math.Max(0, kept.Length - LogLimit / 2));
            }
            _logBox.AppendText(text.ToString());
        }

        private static void OpenPath(string target)
        {
            if (string.IsNullOrEmpty(target))
            {
                return;
            }
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(target);
                info.UseShellExecute = true;
                using (Process.Start(info))
                {
                }
            }
            catch (Win32Exception)
            {
            }
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _flushTimer.Dispose();
                _titleFont.Dispose();
                _strongFont.Dispose();
                _logFont.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
