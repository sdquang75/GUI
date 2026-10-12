using System;
using System.Drawing;
using System.Windows.Forms;
using GUI.Engine;

namespace GUI.Forms
{
    public class UtilityForm : Form
    {
        private Label _lblDpiStatus;
        private Button _btnToggleDpi;
        private Label _lblFeedback;
        private Label _lblDescription;
        private System.Windows.Forms.Timer _statusTimer;

        public UtilityForm()
        {
            this.Text = "Utility";
            this.Size = new Size(420, 270);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            InitializeComponent();
            UpdateToggleUi();

            _statusTimer = new System.Windows.Forms.Timer { Interval = 1500 };
            _statusTimer.Tick += (s, e) => UpdateToggleUi();
            _statusTimer.Start();

            this.FormClosed += (s, e) =>
            {
                _statusTimer.Stop();
                _statusTimer.Dispose();
            };
        }

        private void InitializeComponent()
        {
            var gbDpi = new GroupBox
            {
                Text = "GoodbyeDPI — Deep Packet Inspection Bypass",
                Location = new Point(12, 10),
                Size = new Size(380, 198)
            };

            var lblStatusPrompt = new Label
            {
                Text = "Status:",
                Location = new Point(12, 26),
                AutoSize = true,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblDpiStatus = new Label
            {
                Text = "Checking...",
                Location = new Point(58, 26),
                AutoSize = true,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _btnToggleDpi = new Button
            {
                Text = "Enable GoodbyeDPI",
                Location = new Point(12, 54),
                Size = new Size(356, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _btnToggleDpi.Click += (s, e) => OnToggleGoodbyeDpi();

            _lblFeedback = new Label
            {
                Text = "Ready (Mode: 2_any_country / -5)",
                Location = new Point(12, 94),
                Size = new Size(356, 20),
                ForeColor = Color.FromArgb(71, 85, 105),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblDescription = new Label
            {
                Text = "• Runs goodbyedpi.exe -5 silently in the background without keeping a command prompt window open.\n• Automatically stops when you exit GUI.",
                Location = new Point(12, 120),
                Size = new Size(356, 64),
                ForeColor = Color.FromArgb(71, 85, 105),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            gbDpi.Controls.Add(lblStatusPrompt);
            gbDpi.Controls.Add(_lblDpiStatus);
            gbDpi.Controls.Add(_btnToggleDpi);
            gbDpi.Controls.Add(_lblFeedback);
            gbDpi.Controls.Add(_lblDescription);

            this.Controls.Add(gbDpi);
        }

        private void OnToggleGoodbyeDpi()
        {
            bool currentlyRunning = GoodbyeDpiEngine.IsRunning();
            bool targetState = !currentlyRunning;

            _btnToggleDpi.Enabled = false;
            _lblFeedback.Text = targetState ? "Starting GoodbyeDPI..." : "Stopping GoodbyeDPI...";
            _lblFeedback.ForeColor = Color.FromArgb(71, 85, 105);
            Application.DoEvents();

            var (ok, msg) = GoodbyeDpiEngine.SetEnabled(targetState);
            _btnToggleDpi.Enabled = true;

            _lblFeedback.Text = msg;
            _lblFeedback.ForeColor = ok ? Color.FromArgb(22, 163, 74) : Color.FromArgb(220, 38, 38);

            UpdateToggleUi();
        }

        private void UpdateToggleUi()
        {
            bool running = GoodbyeDpiEngine.IsRunning();
            if (running)
            {
                _lblDpiStatus.Text = "Running! [Mode -5]";
                _lblDpiStatus.ForeColor = Color.FromArgb(22, 163, 74);

                _btnToggleDpi.Text = "Disable GoodbyeDPI";
            }
            else
            {
                _lblDpiStatus.Text = "Stopped";
                _lblDpiStatus.ForeColor = Color.FromArgb(100, 116, 139);

                _btnToggleDpi.Text = "Enable GoodbyeDPI";
            }
        }
    }
}
