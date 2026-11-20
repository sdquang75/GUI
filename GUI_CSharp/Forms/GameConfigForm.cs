using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using GUI.Config;
using GUI.Engine;

namespace GUI.Forms
{
    public class GameConfigForm : Form
    {
        private readonly string _exeName;
        private readonly GameProfile _profile;
        private readonly IVibranceEngine? _vibrance;
        private readonly GammaEngine? _gammaEngine;

        private bool _isTesting = false;
        private int _prevVibranceLevel = 50;
        private GammaState _prevGammaState;

        private TrackBar _tbVibrance;
        private Label _lblVibranceVal;

        private CheckBox _chkGamma;
        private TrackBar _tbGamma;
        private Label _lblGammaVal;

        private CheckBox _chkBoost;
        private TrackBar _tbBoost;
        private Label _lblBoostVal;

        // Global "Affect game monitor only" setting; the Test preview then uses the monitor this dialog is on
        private readonly bool _gameMonitorOnly;
        private string? _prevTargetDisplay;

        private GroupBox? _gbAntiFlash;
        private CheckBox? _chkAntiFlash;
        private TrackBar? _tbMaxDim;
        private Label? _lblMaxDimVal;
        private Button? _btnSetup;
        private Label? _lblSetupStatus;
        private Label? _lblGsiConnection;
        private System.Windows.Forms.Timer? _statusTimer;
        private Cs2GsiListener? _gsiListener;

        private Button _btnSave;
        private Button _btnTest;
        private Button _btnCancel;

        public GameProfile ResultProfile { get; private set; }

        public GameConfigForm(string exeName, GameProfile? initialProfile = null, IVibranceEngine? vibrance = null, GammaEngine? gamma = null, Cs2GsiListener? gsi = null, bool gameMonitorOnly = false)
        {
            _exeName = exeName;
            _gameMonitorOnly = gameMonitorOnly;
            _vibrance = vibrance;
            _gammaEngine = gamma;
            _gsiListener = gsi;

            _profile = initialProfile ?? new GameProfile
            {
                Name = Path.GetFileNameWithoutExtension(exeName),
                VibranceEnabled = true,
                Vibrance = 50,
                GammaEnabled = false,
                Gamma = 1.80,
                BoostEnabled = false,
                Boost = 0
            };
            ResultProfile = _profile;

            InitializeComponent();
            LoadProfileData();

            this.FormClosing += (s, e) =>
            {
                _statusTimer?.Stop();
                _statusTimer?.Dispose();
                if (_isTesting)
                {
                    RevertPreview();
                    _isTesting = false;
                }
            };
        }

        private void InitializeComponent()
        {
            this.Text = $"{_exeName} - Game Profile Configuration";
            bool isCs2 = string.Equals(_exeName, "cs2.exe", StringComparison.OrdinalIgnoreCase);

            // One column: 14px outer margins, 328px wide (total width = 356)
            const int colWidth = 328;
            const int leftColX = 14;
            const int clientW = leftColX + colWidth + leftColX;

            const int trackH = 32; // TrackBar ignores Size.Height unless AutoSize = false (default 45px overlaps the next row)
            // Group heights with 8px gaps: CS2 = 92+118+118+146, others = 100+130+130
            int columnBottom = isCs2 ? 12 + 92 + 8 + 118 + 8 + 118 + 8 + 146 : 12 + 100 + 8 + 130 + 8 + 130;
            int separatorY = columnBottom + 10;
            int btnY = separatorY + 10;
            int clientH = btnY + 30 + 12;

            // Scale the hand-placed layout with the system DPI, otherwise larger fonts overlap fixed positions
            this.AutoScaleDimensions = new SizeF(96F, 96F);
            this.AutoScaleMode = AutoScaleMode.Dpi;
            this.ClientSize = new Size(clientW, clientH);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(243, 244, 246);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            // Group 1: In-game Vibrance Level
            int vibH = isCs2 ? 92 : 100;
            var gbVib = new GroupBox
            {
                Text = "In-game Vibrance Level",
                Location = new Point(leftColX, 12),
                Size = new Size(colWidth, vibH),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblVibranceVal = new Label
            {
                Text = "50%",
                Location = new Point(246, 22),
                Size = new Size(70, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbVibrance = new TrackBar
            {
                Minimum = 0,
                Maximum = 100,
                Value = 50,
                TickFrequency = 10,
                AutoSize = false,
                Location = new Point(10, 46),
                Size = new Size(308, trackH)
            };
            _tbVibrance.ValueChanged += (s, e) =>
            {
                _lblVibranceVal.Text = $"{_tbVibrance.Value}%";
                if (_isTesting) ApplyPreview();
            };

            gbVib.Controls.Add(_lblVibranceVal);
            gbVib.Controls.Add(_tbVibrance);

            // Group 2: Gamma Setting
            int gammaY = 12 + vibH + 8;
            int gammaH = isCs2 ? 118 : 130;
            var gbGamma = new GroupBox
            {
                Text = "Gamma Setting",
                Location = new Point(leftColX, gammaY),
                Size = new Size(colWidth, gammaH),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _chkGamma = new CheckBox
            {
                Text = "Enable Gamma",
                Location = new Point(12, 24),
                AutoSize = true
            };
            _chkGamma.CheckedChanged += (s, e) =>
            {
                UpdateGammaControlsEnabled();
                if (_isTesting) ApplyPreview();
            };

            _lblGammaVal = new Label
            {
                Text = "1.80",
                Location = new Point(246, 24),
                Size = new Size(70, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbGamma = new TrackBar
            {
                Minimum = 140,
                Maximum = 240,
                Value = 180,
                TickFrequency = 10,
                AutoSize = false,
                Location = new Point(10, 50),
                Size = new Size(308, trackH)
            };
            _tbGamma.ValueChanged += (s, e) =>
            {
                _lblGammaVal.Text = $"{(_tbGamma.Value / 100.0):0.00}";
                if (_isTesting) ApplyPreview();
            };

            gbGamma.Controls.Add(_chkGamma);
            gbGamma.Controls.Add(_lblGammaVal);
            gbGamma.Controls.Add(_tbGamma);

            if (!isCs2)
            {
                var lblGammaTip = new Label
                {
                    Text = "Default is 2.20. Lower values brighten dark areas.",
                    Location = new Point(12, 100),
                    Size = new Size(304, 20),
                    ForeColor = Color.FromArgb(100, 100, 100),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point)
                };
                gbGamma.Controls.Add(lblGammaTip);
            }

            // Group 3: Black Boost
            int boostY = gammaY + gammaH + 8;
            int boostH = gammaH;
            var gbBoost = new GroupBox
            {
                Text = "Black Boost",
                Location = new Point(leftColX, boostY),
                Size = new Size(colWidth, boostH),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _chkBoost = new CheckBox
            {
                Text = "Enable Black Boost",
                Location = new Point(12, 24),
                AutoSize = true
            };
            _chkBoost.CheckedChanged += (s, e) =>
            {
                UpdateBoostControlsEnabled();
                if (_isTesting) ApplyPreview();
            };

            _lblBoostVal = new Label
            {
                Text = "0%",
                Location = new Point(246, 24),
                Size = new Size(70, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbBoost = new TrackBar
            {
                Minimum = 0,
                Maximum = 100,
                Value = 0,
                TickFrequency = 10,
                AutoSize = false,
                Location = new Point(10, 50),
                Size = new Size(308, trackH)
            };
            _tbBoost.ValueChanged += (s, e) =>
            {
                _lblBoostVal.Text = $"{_tbBoost.Value}%";
                if (_isTesting) ApplyPreview();
            };

            gbBoost.Controls.Add(_chkBoost);
            gbBoost.Controls.Add(_lblBoostVal);
            gbBoost.Controls.Add(_tbBoost);

            if (!isCs2)
            {
                var lblBoostTip = new Label
                {
                    Text = "Lifts dark shadows without washing out highlights.",
                    Location = new Point(12, 100),
                    Size = new Size(304, 20),
                    ForeColor = Color.FromArgb(100, 100, 100),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point)
                };
                gbBoost.Controls.Add(lblBoostTip);
            }

            // Group 4: CS2 Anti-Flashbang (Only for CS2)
            if (isCs2)
            {
                _gbAntiFlash = new GroupBox
                {
                    Text = "CS2 Anti-Flashbang",
                    Location = new Point(leftColX, boostY + boostH + 8),
                    Size = new Size(colWidth, 146),
                    Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
                };

                _chkAntiFlash = new CheckBox
                {
                    Text = "Enable Black Flash",
                    Location = new Point(12, 24),
                    AutoSize = true
                };
                _chkAntiFlash.CheckedChanged += (s, e) => UpdateAntiFlashControlsEnabled();

                var lblDimPrompt = new Label
                {
                    Text = "Dim:",
                    Location = new Point(12, 56),
                    Size = new Size(34, 20),
                    Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
                };

                _lblMaxDimVal = new Label
                {
                    Text = "100%",
                    Location = new Point(46, 56),
                    Size = new Size(42, 20),
                    Font = new Font("Segoe UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point)
                };

                _tbMaxDim = new TrackBar
                {
                    Minimum = 20,
                    Maximum = 100,
                    Value = 100,
                    TickFrequency = 10,
                    AutoSize = false,
                    Location = new Point(88, 50),
                    Size = new Size(142, trackH)
                };
                _tbMaxDim.ValueChanged += (s, e) =>
                {
                    if (_lblMaxDimVal != null) _lblMaxDimVal.Text = $"{_tbMaxDim.Value}%";
                };

                _btnSetup = new Button
                {
                    Text = "Setup",
                    Location = new Point(236, 52),
                    Size = new Size(80, 28),
                    BackColor = Color.White,
                    FlatStyle = FlatStyle.System,
                    Font = new Font("Segoe UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point)
                };
                _btnSetup.Click += (s, e) => OnSetupClick();

                _lblSetupStatus = new Label
                {
                    Location = new Point(12, 92),
                    Size = new Size(304, 20),
                    Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
                };

                _lblGsiConnection = new Label
                {
                    Location = new Point(12, 116),
                    Size = new Size(304, 20),
                    Font = new Font("Segoe UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point)
                };

                _gbAntiFlash.Controls.Add(_chkAntiFlash);
                _gbAntiFlash.Controls.Add(lblDimPrompt);
                _gbAntiFlash.Controls.Add(_lblMaxDimVal);
                _gbAntiFlash.Controls.Add(_tbMaxDim);
                _gbAntiFlash.Controls.Add(_btnSetup);
                _gbAntiFlash.Controls.Add(_lblSetupStatus);
                _gbAntiFlash.Controls.Add(_lblGsiConnection);
                this.Controls.Add(_gbAntiFlash);

                UpdateCs2StatusUi();

                _statusTimer = new System.Windows.Forms.Timer { Interval = 600 };
                _statusTimer.Tick += (s, e) => UpdateCs2StatusUi();
                _statusTimer.Start();
            }

            // ==========================================
            // BOTTOM BAR: Separator & Balanced Action Buttons
            // ==========================================
            var pnlSeparator = new Panel
            {
                Location = new Point(leftColX, separatorY),
                Size = new Size(colWidth, 1),
                BackColor = Color.FromArgb(220, 224, 230)
            };

            // Left anchor: Test Preview button
            _btnTest = new Button
            {
                Text = "Test Preview",
                Location = new Point(leftColX, btnY),
                Size = new Size(110, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _btnTest.Click += (s, e) => OnToggleTest();

            // Right anchor: Save (Accept) and Cancel buttons
            _btnSave = new Button
            {
                Text = "Save",
                Location = new Point(leftColX + colWidth - 96 - 8 - 96, btnY),
                Size = new Size(96, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };
            _btnSave.Click += (s, e) => OnSave();

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(leftColX + colWidth - 96, btnY),
                Size = new Size(96, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _btnCancel.Click += (s, e) => { this.DialogResult = DialogResult.Cancel; this.Close(); };

            this.Controls.Add(gbVib);
            this.Controls.Add(gbGamma);
            this.Controls.Add(gbBoost);
            this.Controls.Add(pnlSeparator);
            this.Controls.Add(_btnTest);
            this.Controls.Add(_btnSave);
            this.Controls.Add(_btnCancel);

            this.AcceptButton = _btnSave;
            this.CancelButton = _btnCancel;
        }

        private void UpdateGammaControlsEnabled()
        {
            bool enabled = _chkGamma.Checked;
            _tbGamma.Enabled = enabled;
            _lblGammaVal.ForeColor = enabled ? Color.Black : SystemColors.GrayText;
        }

        private void UpdateBoostControlsEnabled()
        {
            bool enabled = _chkBoost.Checked;
            _tbBoost.Enabled = enabled;
            _lblBoostVal.ForeColor = enabled ? Color.Black : SystemColors.GrayText;
        }

        private void UpdateAntiFlashControlsEnabled()
        {
            bool enabled = _chkAntiFlash?.Checked ?? false;
            if (_tbMaxDim != null) _tbMaxDim.Enabled = enabled;
            if (_lblMaxDimVal != null) _lblMaxDimVal.ForeColor = enabled ? Color.Black : SystemColors.GrayText;
        }

        private void LoadProfileData()
        {
            _tbVibrance.Value = Math.Clamp(_profile.Vibrance, 0, 100);
            _lblVibranceVal.Text = $"{_tbVibrance.Value}%";

            _chkGamma.Checked = _profile.GammaEnabled;
            int gVal = (int)Math.Round(_profile.Gamma * 100.0);
            _tbGamma.Value = Math.Clamp(gVal, 140, 240);
            _lblGammaVal.Text = $"{(_tbGamma.Value / 100.0):0.00}";
            UpdateGammaControlsEnabled();

            _chkBoost.Checked = _profile.BoostEnabled;
            _tbBoost.Value = Math.Clamp(_profile.Boost, 0, 100);
            _lblBoostVal.Text = $"{_tbBoost.Value}%";
            UpdateBoostControlsEnabled();

            if (_chkAntiFlash != null) _chkAntiFlash.Checked = _profile.Cs2AntiFlashEnabled;
            if (_tbMaxDim != null)
            {
                int dim = Math.Clamp(_profile.Cs2AntiFlashMaxDim, 20, 100);
                _tbMaxDim.Value = dim;
                if (_lblMaxDimVal != null) _lblMaxDimVal.Text = $"{dim}%";
            }
            UpdateAntiFlashControlsEnabled();
        }

        private string? _cachedCs2CfgDir = null;
        private bool _hasCheckedCs2CfgDir = false;
        // Whether the installed GSI config is the current one (read once, cleared by Setup); null = not read yet
        private bool? _cachedCfgCurrent = null;

        // cs2.exe located via the Setup folder picker; only written to ResultProfile on Save
        private string? _setupCs2ExePath = null;

        private string CurrentProfilePath => _setupCs2ExePath ?? _profile.Path;

        private void UpdateCs2StatusUi()
        {
            if (this.IsDisposed) return;

            // Connection status (pure in-memory check, 0ms)
            bool isConn = _gsiListener?.IsCs2Connected ?? false;
            if (_lblGsiConnection != null)
            {
                if (isConn)
                {
                    _lblGsiConnection.Text = "CS2: Connected";
                    _lblGsiConnection.ForeColor = Color.FromArgb(22, 163, 74);
                }
                else
                {
                    _lblGsiConnection.Text = "CS2: Disconnected";
                    _lblGsiConnection.ForeColor = Color.FromArgb(120, 120, 120);
                }
            }

            // Setup / Registry status (cached directory path, zero disk I/O on tick)
            if (_lblSetupStatus != null)
            {
                if (!_hasCheckedCs2CfgDir)
                {
                    _cachedCs2CfgDir = Utils.Cs2Helper.FindCs2CfgDirectory(CurrentProfilePath);
                    _hasCheckedCs2CfgDir = true;
                }

                bool cfgOk = !string.IsNullOrEmpty(_cachedCs2CfgDir) && Utils.Cs2Helper.IsGsiConfigInstalled(_cachedCs2CfgDir);
                bool regOk = Utils.Cs2Helper.IsGdiIcmGammaRangeUnlocked();
                if (cfgOk && _cachedCfgCurrent == null)
                {
                    _cachedCfgCurrent = Utils.Cs2Helper.IsGsiConfigCurrent(_cachedCs2CfgDir!, _gsiListener?.Port ?? 23456);
                }

                if (cfgOk && _cachedCfgCurrent == false)
                {
                    // Written by an older version (larger packets) or for another port
                    _lblSetupStatus.Text = "⚠ GSI config outdated (Click Setup to update)";
                    _lblSetupStatus.ForeColor = Color.FromArgb(180, 83, 9);
                }
                else if (cfgOk && regOk)
                {
                    _lblSetupStatus.Text = "✓ Ready (0% Black Unlocked)";
                    _lblSetupStatus.ForeColor = Color.FromArgb(22, 163, 74);
                }
                else if (cfgOk && !regOk)
                {
                    _lblSetupStatus.Text = "⚠ 50% clamp (Reboot PC for 0% black)";
                    _lblSetupStatus.ForeColor = Color.FromArgb(180, 83, 9);
                }
                else
                {
                    _lblSetupStatus.Text = "⚠ Not setup (Click Setup to install)";
                    _lblSetupStatus.ForeColor = Color.FromArgb(220, 38, 38);
                }
            }
        }

        private void OnSetupClick()
        {
            string? cfgDir = Utils.Cs2Helper.FindCs2CfgDirectory(CurrentProfilePath);
            if (string.IsNullOrEmpty(cfgDir))
            {
                using var fbd = new FolderBrowserDialog
                {
                    Description = "Select your CS2 game folder (e.g. Counter-Strike Global Offensive\\game)",
                    UseDescriptionForTitle = true
                };
                if (fbd.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(fbd.SelectedPath))
                {
                    string p = fbd.SelectedPath;
                    _setupCs2ExePath = FindCs2ExeNear(p) ?? _setupCs2ExePath;
                    if (Directory.Exists(Path.Combine(p, "csgo", "cfg")))
                    {
                        cfgDir = Path.Combine(p, "csgo", "cfg");
                    }
                    else if (Directory.Exists(Path.Combine(p, "game", "csgo", "cfg")))
                    {
                        cfgDir = Path.Combine(p, "game", "csgo", "cfg");
                    }
                    else if (p.EndsWith("cfg", StringComparison.OrdinalIgnoreCase))
                    {
                        cfgDir = p;
                    }
                }
            }

            if (string.IsNullOrEmpty(cfgDir))
            {
                MessageBox.Show(this, "Could not find CS2 cfg folder. Please make sure Counter-Strike 2 is installed.", "Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            // Must match the port the running listener uses (config "cs2_gsi_port")
            int gsiPort = _gsiListener?.Port ?? 23456;
            var (cfgSuccess, cfgPath, cfgMsg) = Utils.Cs2Helper.InstallGsiConfig(cfgDir, gsiPort);
            if (!cfgSuccess)
            {
                MessageBox.Show(this, "Failed to write GSI config: " + cfgMsg, "Setup Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            bool regUnlocked = Utils.Cs2Helper.IsGdiIcmGammaRangeUnlocked();
            if (!regUnlocked)
            {
                var (regSuccess, regMsg) = Utils.Cs2Helper.UnlockRegistryGdiRange();
                regUnlocked = regSuccess;
            }

            bool isCs2Running = false;
            try
            {
                var procs = Process.GetProcessesByName("cs2");
                isCs2Running = procs.Length > 0;
                foreach (var p in procs) { try { p.Dispose(); } catch { } }
            }
            catch { }

            _cachedCs2CfgDir = cfgDir;
            _hasCheckedCs2CfgDir = true;
            _cachedCfgCurrent = null;
            UpdateCs2StatusUi();

            string regInfo = regUnlocked
                ? "✓ 0% Pure Black: Unlocked! (Please RESTART your computer once for exclusive fullscreen 0% black to take effect; until then, hardware dimming operates at 50% clamp)."
                : "⚠ Registry: Skipped or not elevated.";

            string runningNotice = isCs2Running
                ? "\n\n⚠️ NOTE: CS2 is currently running. Please RESTART CS2 so it loads the new GSI config!"
                : "\n\nCS2 will now automatically connect to GUI Anti-Flashbang when launched.";

            MessageBox.Show(this,
                $"GUI Anti-Flashbang Setup Complete!\n\n" +
                $"• GSI Config installed to:\n  {cfgPath}\n\n" +
                $"• {regInfo}" +
                runningNotice,
                "GUI Anti-Flashbang",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }

        private static string? FindCs2ExeNear(string folder)
        {
            try
            {
                string[] candidates =
                {
                    Path.Combine(folder, "cs2.exe"),
                    Path.Combine(folder, "bin", "win64", "cs2.exe"),
                    Path.Combine(folder, "game", "bin", "win64", "cs2.exe"),
                    Path.Combine(folder, "..", "..", "bin", "win64", "cs2.exe") // folder = game\csgo\cfg
                };
                foreach (var c in candidates)
                {
                    if (File.Exists(c)) return Path.GetFullPath(c);
                }
            }
            catch { }
            return null;
        }

        private void OnToggleTest()
        {
            if (!_isTesting)
            {
                _isTesting = true;
                _prevVibranceLevel = _vibrance?.GetLevel() ?? 50;
                if (_gammaEngine != null)
                {
                    _gammaEngine.SaveState(out _prevGammaState);
                    _prevTargetDisplay = _gammaEngine.TargetDisplay;
                }
                _btnTest.Text = "■ Stop Test";
                _btnTest.BackColor = Color.FromArgb(254, 226, 226);
                _btnTest.ForeColor = Color.FromArgb(185, 28, 28);
                ApplyPreview();
            }
            else
            {
                RevertPreview();
                _isTesting = false;
                _btnTest.Text = "Test Preview";
                _btnTest.BackColor = Color.White;
                _btnTest.ForeColor = SystemColors.ControlText;
            }
        }

        private void ApplyPreview()
        {
            if (!_isTesting) return;

            string? target = _gameMonitorOnly
                ? DisplayTopology.GetMonitorOfWindow(this.IsHandleCreated ? this.Handle : IntPtr.Zero) ?? DisplayTopology.GetPrimaryName()
                : null;

            if (_vibrance is UnifiedVibranceEngine unified)
            {
                unified.Apply(_tbVibrance.Value, target);
            }
            else if (_vibrance != null)
            {
                _vibrance.SetLevel(_tbVibrance.Value);
            }

            if (_gammaEngine != null)
            {
                _gammaEngine.SetTargetDisplay(target);
                if (_chkGamma.Checked || _chkBoost.Checked)
                {
                    double g = _chkGamma.Checked ? (_tbGamma.Value / 100.0) : 2.20;
                    double b = _chkBoost.Checked ? (_tbBoost.Value / 100.0) : 0.0;
                    _gammaEngine.ApplyCurve(g, b);
                }
                else
                {
                    _gammaEngine.Restore();
                }
            }
        }

        private void RevertPreview()
        {
            _btnTest.Text = "Test Preview";
            _btnTest.BackColor = Color.White;
            _btnTest.ForeColor = SystemColors.ControlText;

            if (_vibrance != null)
            {
                _vibrance.SetLevel(_prevVibranceLevel);
            }

            if (_gammaEngine != null)
            {
                // Reset the LUTs the preview wrote before switching back, so its monitor is not left tinted
                _gammaEngine.Restore();
                _gammaEngine.SetTargetDisplay(_prevTargetDisplay);
                _gammaEngine.RestoreState(in _prevGammaState);
            }
        }

        private void OnSave()
        {
            if (_isTesting)
            {
                RevertPreview();
                _isTesting = false;
            }

            ResultProfile = new GameProfile
            {
                Name = string.IsNullOrEmpty(_profile.Name) ? Path.GetFileNameWithoutExtension(_exeName) : _profile.Name,
                Path = CurrentProfilePath,
                // No UI toggle exists for this, so the in-game Vibrance slider must always take effect
                VibranceEnabled = true,
                Vibrance = _tbVibrance.Value,
                GammaEnabled = _chkGamma.Checked,
                Gamma = Math.Round(_tbGamma.Value / 100.0, 2),
                BoostEnabled = _chkBoost.Checked,
                Boost = _tbBoost.Value,
                Cs2AntiFlashEnabled = _chkAntiFlash?.Checked ?? _profile.Cs2AntiFlashEnabled,
                Cs2AntiSmokeEnabled = false,
                Cs2AntiFlashMaxDim = _tbMaxDim?.Value ?? _profile.Cs2AntiFlashMaxDim
            };

            this.DialogResult = DialogResult.OK;
            this.Close();
        }
    }
}
