using System;
using System.Drawing;
using System.Windows.Forms;
using GUI.Config;
using GUI.Engine;

namespace GUI.Forms
{
    public class UtilityForm : Form
    {
        private readonly AppConfig _config;
        private bool _suppressModeChange = false;

        private Label _lblDpiStatus;
        private ComboBox _cmbMode;
        private Button _btnToggleDpi;
        private Label _lblFlags;
        private Label _lblFeedback;
        private Label _lblDescription;
        private System.Windows.Forms.Timer _statusTimer;

        public UtilityForm(AppConfig? config = null)
        {
            _config = config ?? ConfigManager.Load();

            this.Text = "Utility";
            this.Size = new Size(460, 325);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            InitializeComponent();
            LoadSelectedMode();
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
                Size = new Size(420, 262)
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

            var lblModePrompt = new Label
            {
                Text = "Mode:",
                Location = new Point(12, 55),
                AutoSize = true,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _cmbMode = new ComboBox
            {
                Location = new Point(58, 52),
                Size = new Size(348, 24),
                DropDownStyle = ComboBoxStyle.DropDownList,
                Font = new Font("Segoe UI", 8.75F, FontStyle.Regular, GraphicsUnit.Point)
            };
            foreach (var preset in GoodbyeDpiEngine.Presets)
            {
                _cmbMode.Items.Add(preset);
            }
            _cmbMode.SelectedIndexChanged += (s, e) => OnModeChanged();

            _btnToggleDpi = new Button
            {
                Text = "Enable GoodbyeDPI",
                Location = new Point(12, 86),
                Size = new Size(394, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _btnToggleDpi.Click += (s, e) => OnToggleGoodbyeDpi();

            _lblFlags = new Label
            {
                Text = "Flags: -f 2 -e 2 --reverse-frag --max-payload",
                Location = new Point(12, 124),
                Size = new Size(394, 18),
                AutoEllipsis = true,
                ForeColor = Color.FromArgb(51, 65, 85),
                Font = new Font("Consolas", 8.25F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblFeedback = new Label
            {
                Text = "Ready",
                Location = new Point(12, 146),
                Size = new Size(394, 20),
                ForeColor = Color.FromArgb(71, 85, 105),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblDescription = new Label
            {
                Text = "",
                Location = new Point(12, 170),
                Size = new Size(394, 82),
                ForeColor = Color.FromArgb(71, 85, 105),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            gbDpi.Controls.Add(lblStatusPrompt);
            gbDpi.Controls.Add(_lblDpiStatus);
            gbDpi.Controls.Add(lblModePrompt);
            gbDpi.Controls.Add(_cmbMode);
            gbDpi.Controls.Add(_btnToggleDpi);
            gbDpi.Controls.Add(_lblFlags);
            gbDpi.Controls.Add(_lblFeedback);
            gbDpi.Controls.Add(_lblDescription);

            this.Controls.Add(gbDpi);
        }

        private GoodbyeDpiPreset GetSelectedPreset()
        {
            if (_cmbMode.SelectedItem is GoodbyeDpiPreset preset)
            {
                return preset;
            }
            return GoodbyeDpiEngine.GetPreset(_config.GoodbyeDpiMode);
        }

        private void LoadSelectedMode()
        {
            _suppressModeChange = true;
            try
            {
                string targetId = GoodbyeDpiEngine.IsRunning()
                    ? GoodbyeDpiEngine.GetActivePreset().Id
                    : _config.GoodbyeDpiMode;

                var preset = GoodbyeDpiEngine.GetPreset(targetId);
                int idx = 0;
                for (int i = 0; i < _cmbMode.Items.Count; i++)
                {
                    if (_cmbMode.Items[i] is GoodbyeDpiPreset item &&
                        string.Equals(item.Id, preset.Id, StringComparison.OrdinalIgnoreCase))
                    {
                        idx = i;
                        break;
                    }
                }

                if (_cmbMode.Items.Count > 0)
                {
                    _cmbMode.SelectedIndex = idx;
                }

                UpdateModeInfoUi(preset);
                _lblFeedback.Text = $"Ready ({preset.ShortLabel})";
            }
            finally
            {
                _suppressModeChange = false;
            }
        }

        private void UpdateModeInfoUi(GoodbyeDpiPreset preset)
        {
            _lblFlags.Text = $"Flags: {preset.Arguments}";
            _lblDescription.Text = $"{preset.Description}\n• Runs silently in background and stops automatically on GUI exit.";
        }

        private async void OnModeChanged()
        {
            if (_suppressModeChange) return;

            var preset = GetSelectedPreset();
            _config.GoodbyeDpiMode = preset.Id;
            ConfigManager.Save(_config);
            UpdateModeInfoUi(preset);

            if (GoodbyeDpiEngine.IsRunning())
            {
                _cmbMode.Enabled = false;
                _btnToggleDpi.Enabled = false;
                _lblFeedback.Text = $"Switching to {preset.ShortLabel}...";
                _lblFeedback.ForeColor = Color.FromArgb(71, 85, 105);

                var (ok, msg) = await Task.Run(() => GoodbyeDpiEngine.SwitchModeIfRunning(preset.Id));

                if (!this.IsDisposed)
                {
                    _cmbMode.Enabled = true;
                    _btnToggleDpi.Enabled = true;
                    _lblFeedback.Text = msg;
                    _lblFeedback.ForeColor = ok ? Color.FromArgb(22, 163, 74) : Color.FromArgb(220, 38, 38);
                    UpdateToggleUi();
                }
            }
            else
            {
                GoodbyeDpiEngine.SaveActiveModeId(preset.Id);
                _lblFeedback.Text = $"Ready ({preset.ShortLabel})";
                _lblFeedback.ForeColor = Color.FromArgb(71, 85, 105);
            }
        }

        private async void OnToggleGoodbyeDpi()
        {
            bool currentlyRunning = GoodbyeDpiEngine.IsRunning();
            bool targetState = !currentlyRunning;
            var preset = GetSelectedPreset();

            _btnToggleDpi.Enabled = false;
            _cmbMode.Enabled = false;
            _lblFeedback.Text = targetState
                ? $"Starting GoodbyeDPI ({preset.ShortLabel})..."
                : "Stopping GoodbyeDPI...";
            _lblFeedback.ForeColor = Color.FromArgb(71, 85, 105);

            var (ok, msg) = await Task.Run(() => GoodbyeDpiEngine.SetEnabled(targetState, preset.Id));

            if (!this.IsDisposed)
            {
                _btnToggleDpi.Enabled = true;
                _cmbMode.Enabled = true;

                _lblFeedback.Text = msg;
                _lblFeedback.ForeColor = ok ? Color.FromArgb(22, 163, 74) : Color.FromArgb(220, 38, 38);

                UpdateToggleUi();
            }
        }

        private void UpdateToggleUi()
        {
            if (this.IsDisposed) return;
            bool running = GoodbyeDpiEngine.IsRunning();
            if (running)
            {
                var activePreset = GoodbyeDpiEngine.GetActivePreset();
                _lblDpiStatus.Text = $"Running! [{activePreset.ShortLabel}]";
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
