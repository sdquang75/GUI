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

        // Sound Lock & Lookahead DSP Controls
        private CheckBox _chkEnableDsp;
        private Label _lblDspHint;
        private Label _lblGameVolPrompt;
        private TrackBar _tbGameVol;
        private Label _lblGameVolVal;
        private Label _lblLimitPrompt;
        private TrackBar _tbLimit;
        private Label _lblLimitVal;
        private CheckBox _chkBalanceApps;
        private CheckBox _chkEq;
        private CheckBox _chkEqRumble;
        private Label _lblEqVal;
        private TrackBar _tbEq;
        private CheckBox _chkShaper;
        private Label _lblShaperVal;
        private TrackBar _tbShaper;
        private Label _lblShaperAttack;
        private NumericUpDown _numShaperAttack;
        private Label _lblShaperAttackUnit;

        // Output Device & Virtual Audio Cable routing controls
        private Label _lblOutputDevicePrompt;
        private ComboBox _cmbOutputDevice;
        private Label _lblCableStatus;
        private Button _btnSetupCable;
        private Button _btnSoundCpl;
        private System.Collections.Generic.List<GUI.Engine.Audio.AudioEndpointInfo> _audioEndpoints = new();

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

            // Symmetrical geometry: 14px outer margins, 328px columns, 16px center gap (Total width = 700)
            const int colWidth = 328;
            const int leftColX = 14;
            const int rightColX = 358;

            // Both columns end at the same Y; the bottom bar sits below them
            const int trackH = 32; // TrackBar ignores Size.Height unless AutoSize = false (default 45px overlaps the next row)
            int soundLockH = isCs2 ? 554 : 478;
            int columnBottom = 12 + soundLockH;
            int separatorY = columnBottom + 10;
            int btnY = separatorY + 10;
            int clientH = btnY + 30 + 12;

            // Scale the hand-placed layout with the system DPI, otherwise larger fonts overlap fixed positions
            this.AutoScaleDimensions = new SizeF(96F, 96F);
            this.AutoScaleMode = AutoScaleMode.Dpi;
            this.ClientSize = new Size(700, clientH);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(243, 244, 246);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            // ==========================================
            // LEFT COLUMN: Video & Visuals
            // ==========================================

            // Group 1: In-game Vibrance Level
            // Left heights add up to soundLockH (8px gaps): CS2 = 92+118+118+174 + 24, others = 100+167+167 + 16
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
            int gammaH = isCs2 ? 118 : 167;
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
                    Size = new Size(colWidth, 174),
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
            // RIGHT COLUMN: Sound Lock & Lookahead DSP
            // ==========================================
            var gbSoundLock = new GroupBox
            {
                Text = "Sound Lock (Game Volume && Ceiling)",
                Location = new Point(rightColX, 12),
                Size = new Size(colWidth, soundLockH),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            // One switch: game volume + lookahead ceiling (the reactive session limiter without a buffer is gone:
            // it can only duck after the gunshot already played)
            _chkEnableDsp = new CheckBox
            {
                Text = "Enable Sound Lock (lookahead, no pop)",
                Location = new Point(12, 24),
                AutoSize = true,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };
            _chkEnableDsp.CheckedChanged += (s, e) => UpdateSoundLockControlsEnabled();

            _lblDspHint = new Label
            {
                Text = "Via Virtual Cable • ~5 ms buffer, ~17 ms release",
                Location = new Point(30, 46),
                Size = new Size(286, 18),
                ForeColor = Color.FromArgb(100, 100, 100),
                Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblGameVolPrompt = new Label
            {
                Text = "Master volume while in game:",
                Location = new Point(12, 72),
                AutoSize = true
            };

            _lblGameVolVal = new Label
            {
                Text = "70%",
                Location = new Point(246, 70),
                Size = new Size(70, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbGameVol = new TrackBar
            {
                Minimum = 10,
                Maximum = 100,
                Value = 70,
                TickFrequency = 10,
                AutoSize = false,
                Location = new Point(10, 92),
                Size = new Size(308, trackH)
            };
            _tbGameVol.ValueChanged += (s, e) =>
            {
                _lblGameVolVal.Text = $"{_tbGameVol.Value}%";
            };

            _lblLimitPrompt = new Label
            {
                Text = "Ceiling (loudest peak allowed):",
                Location = new Point(12, 132),
                AutoSize = true
            };

            _lblLimitVal = new Label
            {
                Text = "40%",
                Location = new Point(246, 130),
                Size = new Size(70, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbLimit = new TrackBar
            {
                Minimum = 5,
                Maximum = 100,
                Value = 40,
                TickFrequency = 10,
                AutoSize = false,
                Location = new Point(10, 152),
                Size = new Size(308, trackH)
            };
            _tbLimit.ValueChanged += (s, e) =>
            {
                _lblLimitVal.Text = $"{_tbLimit.Value}%";
            };

            // Left half: footstep clarity EQ (lifts the 3 kHz footstep band, trims rumble / mud).
            // Right half: reverb cut (transient shaper: shortens every sound's tail, can lift its onset).
            // Side by side so the dialog stays short enough for 1080p at 150 % scaling.
            _chkEq = new CheckBox
            {
                Text = "Footstep EQ",
                Location = new Point(12, 192),
                AutoSize = true
            };
            _chkEq.CheckedChanged += (s, e) => UpdateSoundLockControlsEnabled();

            _lblEqVal = new Label
            {
                Text = "+4 dB",
                Location = new Point(110, 190),
                Size = new Size(50, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbEq = new TrackBar
            {
                Minimum = 0,
                Maximum = 8,
                Value = 4,
                TickFrequency = 1,
                AutoSize = false,
                Location = new Point(10, 212),
                Size = new Size(152, trackH)
            };
            _tbEq.ValueChanged += (s, e) => _lblEqVal.Text = $"+{_tbEq.Value} dB";

            _chkEqRumble = new CheckBox
            {
                Text = "Cut rumble",
                Location = new Point(12, 248),
                AutoSize = true,
                Checked = true
            };

            _chkShaper = new CheckBox
            {
                Text = "Reverb cut",
                Location = new Point(168, 192),
                AutoSize = true
            };
            _chkShaper.CheckedChanged += (s, e) => UpdateSoundLockControlsEnabled();

            _lblShaperVal = new Label
            {
                Text = "-6 dB",
                Location = new Point(266, 190),
                Size = new Size(50, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbShaper = new TrackBar
            {
                Minimum = 0,
                Maximum = (int)GUI.Engine.Audio.TransientShaper.MaxSustainCutDb,
                Value = 6,
                TickFrequency = 2,
                AutoSize = false,
                Location = new Point(166, 212),
                Size = new Size(152, trackH)
            };
            _tbShaper.ValueChanged += (s, e) => _lblShaperVal.Text = $"-{_tbShaper.Value} dB";

            _lblShaperAttack = new Label
            {
                Text = "Onset lift:",
                Location = new Point(168, 250),
                AutoSize = true
            };

            _numShaperAttack = new NumericUpDown
            {
                Minimum = 0,
                Maximum = (decimal)GUI.Engine.Audio.TransientShaper.MaxAttackDb,
                Value = 0,
                Location = new Point(240, 247),
                Size = new Size(46, 23)
            };

            _lblShaperAttackUnit = new Label
            {
                Text = "dB",
                Location = new Point(290, 250),
                AutoSize = true
            };

            _chkBalanceApps = new CheckBox
            {
                Text = "Keep other apps (Discord) at desktop loudness",
                Location = new Point(12, 280),
                Size = new Size(304, 20),
                Checked = true
            };

            // Audio Routing Separator
            var pnlAudioSep = new Panel
            {
                Location = new Point(10, 324),
                Size = new Size(308, 1),
                BackColor = Color.FromArgb(226, 232, 240)
            };

            _lblOutputDevicePrompt = new Label
            {
                Text = "Output Device (DAC / Headset):",
                Location = new Point(12, 332),
                Size = new Size(304, 18),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(30, 58, 138)
            };

            _cmbOutputDevice = new ComboBox
            {
                Location = new Point(10, 352),
                Size = new Size(308, 24),
                DropDownStyle = ComboBoxStyle.DropDownList,
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblCableStatus = new Label
            {
                Text = "Checking Virtual Cable...",
                Location = new Point(12, 384),
                Size = new Size(202, 26),
                TextAlign = ContentAlignment.MiddleLeft,
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _btnSetupCable = new Button
            {
                Text = "Setup Cable",
                Location = new Point(216, 383),
                Size = new Size(102, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point)
            };
            _btnSetupCable.Click += (s, e) => OnSetupCableClick();

            _btnSoundCpl = new Button
            {
                Text = "Sound CPL",
                Size = new Size(76, 22),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 7.5F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _btnSoundCpl.Click += (s, e) => GUI.Engine.Audio.VirtualAudioInstaller.OpenSoundControlPanel();

            gbSoundLock.Controls.Add(_chkEnableDsp);
            gbSoundLock.Controls.Add(_lblDspHint);
            gbSoundLock.Controls.Add(_lblGameVolPrompt);
            gbSoundLock.Controls.Add(_lblGameVolVal);
            gbSoundLock.Controls.Add(_tbGameVol);
            gbSoundLock.Controls.Add(_lblLimitPrompt);
            gbSoundLock.Controls.Add(_lblLimitVal);
            gbSoundLock.Controls.Add(_tbLimit);
            gbSoundLock.Controls.Add(_chkEq);
            gbSoundLock.Controls.Add(_chkEqRumble);
            gbSoundLock.Controls.Add(_lblEqVal);
            gbSoundLock.Controls.Add(_tbEq);
            gbSoundLock.Controls.Add(_chkShaper);
            gbSoundLock.Controls.Add(_lblShaperVal);
            gbSoundLock.Controls.Add(_tbShaper);
            gbSoundLock.Controls.Add(_lblShaperAttack);
            gbSoundLock.Controls.Add(_numShaperAttack);
            gbSoundLock.Controls.Add(_lblShaperAttackUnit);
            gbSoundLock.Controls.Add(_chkBalanceApps);
            gbSoundLock.Controls.Add(pnlAudioSep);
            gbSoundLock.Controls.Add(_lblOutputDevicePrompt);
            gbSoundLock.Controls.Add(_cmbOutputDevice);
            gbSoundLock.Controls.Add(_lblCableStatus);
            gbSoundLock.Controls.Add(_btnSetupCable);

            if (isCs2)
            {
                // CS2 Extra Height: Professional Hardware Protection & Status Panel
                var pnlDacInfo = new Panel
                {
                    Location = new Point(10, 418),
                    Size = new Size(308, 126),
                    BackColor = Color.FromArgb(238, 242, 248),
                    BorderStyle = BorderStyle.FixedSingle
                };

                var lblDacTitle = new Label
                {
                    Text = "Signal path (lookahead mode)",
                    Location = new Point(8, 7),
                    Size = new Size(210, 18),
                    Font = new Font("Segoe UI", 8.5F, FontStyle.Bold, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(30, 58, 138)
                };

                _btnSoundCpl.Location = new Point(226, 5);

                var lblDac1 = new Label
                {
                    Text = "• Game → Virtual Cable → limiter → your DAC",
                    Location = new Point(8, 34),
                    Size = new Size(290, 16),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(51, 65, 85)
                };

                var lblDac2 = new Label
                {
                    Text = "• Gain lowered ~5 ms before a gunshot (no pop)",
                    Location = new Point(8, 51),
                    Size = new Size(290, 16),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(51, 65, 85)
                };

                var lblDac3 = new Label
                {
                    Text = "• Below ceiling: untouched unless EQ / reverb cut",
                    Location = new Point(8, 68),
                    Size = new Size(290, 16),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(51, 65, 85)
                };

                var lblDac4 = new Label
                {
                    Text = "• Master volume: game level, desktop restored",
                    Location = new Point(8, 85),
                    Size = new Size(290, 16),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(51, 65, 85)
                };

                var lblDac5 = new Label
                {
                    Text = "• Added delay ≈ 20–25 ms (cable + buffer)",
                    Location = new Point(8, 102),
                    Size = new Size(290, 16),
                    Font = new Font("Segoe UI", 8F, FontStyle.Regular, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(51, 65, 85)
                };

                pnlDacInfo.Controls.Add(lblDacTitle);
                pnlDacInfo.Controls.Add(_btnSoundCpl);
                pnlDacInfo.Controls.Add(lblDac1);
                pnlDacInfo.Controls.Add(lblDac2);
                pnlDacInfo.Controls.Add(lblDac3);
                pnlDacInfo.Controls.Add(lblDac4);
                pnlDacInfo.Controls.Add(lblDac5);
                gbSoundLock.Controls.Add(pnlDacInfo);
            }
            else
            {
                var pnlDacInfo = new Panel
                {
                    Location = new Point(10, 418),
                    Size = new Size(308, 50),
                    BackColor = Color.FromArgb(238, 242, 248),
                    BorderStyle = BorderStyle.FixedSingle
                };

                var lblDacTitle = new Label
                {
                    Text = "Signal path",
                    Location = new Point(8, 6),
                    Size = new Size(210, 18),
                    Font = new Font("Segoe UI", 8F, FontStyle.Bold, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(30, 58, 138)
                };

                _btnSoundCpl.Location = new Point(226, 4);

                var lblDacDesc = new Label
                {
                    Text = "Game volume on focus • lookahead ceiling, no loudness",
                    Location = new Point(8, 28),
                    Size = new Size(292, 16),
                    Font = new Font("Segoe UI", 7.5F, FontStyle.Regular, GraphicsUnit.Point),
                    ForeColor = Color.FromArgb(51, 65, 85)
                };

                pnlDacInfo.Controls.Add(lblDacTitle);
                pnlDacInfo.Controls.Add(_btnSoundCpl);
                pnlDacInfo.Controls.Add(lblDacDesc);
                gbSoundLock.Controls.Add(pnlDacInfo);
            }

            // ==========================================
            // BOTTOM BAR: Separator & Balanced Action Buttons
            // ==========================================
            var pnlSeparator = new Panel
            {
                Location = new Point(leftColX, separatorY),
                Size = new Size(colWidth * 2 + 16, 1),
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
                Location = new Point(480, btnY),
                Size = new Size(96, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };
            _btnSave.Click += (s, e) => OnSave();

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(590, btnY),
                Size = new Size(96, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _btnCancel.Click += (s, e) => { this.DialogResult = DialogResult.Cancel; this.Close(); };

            this.Controls.Add(gbVib);
            this.Controls.Add(gbGamma);
            this.Controls.Add(gbBoost);
            this.Controls.Add(gbSoundLock);
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

        private void UpdateSoundLockControlsEnabled()
        {
            bool masterOn = _chkEnableDsp.Checked;
            _lblDspHint.Enabled = masterOn;

            _lblGameVolPrompt.Enabled = masterOn;
            _lblGameVolVal.Enabled = masterOn;
            _tbGameVol.Enabled = masterOn;

            _lblLimitPrompt.Enabled = masterOn;
            _lblLimitVal.Enabled = masterOn;
            _tbLimit.Enabled = masterOn;

            _chkBalanceApps.Enabled = masterOn;
            _chkEq.Enabled = masterOn;
            bool eqOn = masterOn && _chkEq.Checked;
            _chkEqRumble.Enabled = eqOn;
            _lblEqVal.Enabled = eqOn;
            _tbEq.Enabled = eqOn;

            _chkShaper.Enabled = masterOn;
            bool shaperOn = masterOn && _chkShaper.Checked;
            _lblShaperVal.Enabled = shaperOn;
            _tbShaper.Enabled = shaperOn;
            _lblShaperAttack.Enabled = shaperOn;
            _numShaperAttack.Enabled = shaperOn;
            _lblShaperAttackUnit.Enabled = shaperOn;

            _lblOutputDevicePrompt.Enabled = masterOn;
            _cmbOutputDevice.Enabled = masterOn;
            _lblCableStatus.Enabled = masterOn;
            _btnSetupCable.Enabled = masterOn;
            _btnSoundCpl.Enabled = masterOn;
        }

        private void RefreshAudioEndpoints()
        {
            _cmbOutputDevice.Items.Clear();
            // First entry follows whatever Windows uses as default output (works on any PC, follows headset switches).
            // The cable's playback side is the DSP input, so it can never be the DSP output (feedback loop).
            var physical = GUI.Engine.Audio.AudioEndpointHelper.GetRenderEndpoints().FindAll(e => !e.IsVirtualCable);
            var current = physical.Find(e => e.IsDefault);
            _audioEndpoints = new System.Collections.Generic.List<GUI.Engine.Audio.AudioEndpointInfo>
            {
                new GUI.Engine.Audio.AudioEndpointInfo
                {
                    Id = "",
                    Name = current != null ? $"Windows default output ({current.Name})" : "Windows default output"
                }
            };
            _audioEndpoints.AddRange(physical);

            int selectedIndex = 0;
            for (int i = 0; i < _audioEndpoints.Count; i++)
            {
                _cmbOutputDevice.Items.Add(_audioEndpoints[i].Name);
                if (i > 0 && !string.IsNullOrEmpty(_profile.SoundLockOutputDeviceId) &&
                    string.Equals(_audioEndpoints[i].Id, _profile.SoundLockOutputDeviceId, StringComparison.OrdinalIgnoreCase))
                {
                    selectedIndex = i;
                }
            }
            _cmbOutputDevice.SelectedIndex = selectedIndex;

            UpdateVirtualCableStatus();
        }

        private void UpdateVirtualCableStatus()
        {
            _btnSetupCable.Text = "Setup";
            bool installed = GUI.Engine.Audio.VirtualAudioInstaller.IsInstalled(out string? cableRenderId, out string? cableCaptureId);
            string? issue = installed ? GUI.Engine.Audio.AudioEndpointHelper.GetCableInputIssue(cableRenderId, cableCaptureId) : null;
            if (!installed)
            {
                _lblCableStatus.Text = "⚠ Not set up: press Setup";
                _lblCableStatus.ForeColor = Color.FromArgb(220, 38, 38);
            }
            else if (issue != null)
            {
                // Something on the cable colors the game before the limiter: Setup repairs it
                _lblCableStatus.Text = "⚠ " + issue;
                _lblCableStatus.ForeColor = Color.FromArgb(217, 119, 6);
            }
            else
            {
                _lblCableStatus.Text = "✓ Ready";
                _lblCableStatus.ForeColor = Color.FromArgb(22, 163, 74);
            }
        }

        private async void OnSetupCableClick()
        {
            bool installed = GUI.Engine.Audio.VirtualAudioInstaller.IsInstalled(out _, out _);
            var confirm = MessageBox.Show(this,
                (installed
                    ? "Setup will check the virtual cable and repair anything that would change the game's sound\n" +
                      "(Loudness EQ / enhancements, cable volume, default devices).\n\n"
                    : "Setup will download and install VB-CABLE (free virtual audio driver by VB-Audio, ~1 MB),\n" +
                      "then prepare it for Sound Lock. Windows will ask for administrator permission and to\n" +
                      "allow the driver.\n\n") +
                "Sound may cut out for a second while the audio service restarts.\n" +
                "Close the game first if it is running.",
                "Sound Lock Setup",
                MessageBoxButtons.OKCancel,
                MessageBoxIcon.Information);
            if (confirm != DialogResult.OK) return;

            _btnSetupCable.Enabled = false;
            _btnSetupCable.Text = "Working...";

            var (success, msg) = await GUI.Engine.Audio.VirtualAudioInstaller.SetupAsync(status =>
            {
                if (!this.IsDisposed && this.IsHandleCreated)
                {
                    this.BeginInvoke(() =>
                    {
                        _lblCableStatus.Text = status;
                        _lblCableStatus.ForeColor = Color.FromArgb(30, 58, 138);
                    });
                }
            });

            if (this.IsDisposed) return;
            RefreshAudioEndpoints();
            _btnSetupCable.Enabled = _chkEnableDsp.Checked;
            MessageBox.Show(this, msg, "Sound Lock Setup", MessageBoxButtons.OK,
                success ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
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

            _chkEnableDsp.Checked = _profile.SoundLockEnabled;
            int gameVol = Math.Clamp(_profile.SoundLockGameVolume > 0 ? _profile.SoundLockGameVolume : 70, 10, 100);
            _tbGameVol.Value = gameVol;
            _lblGameVolVal.Text = $"{gameVol}%";

            int sLimit = _profile.SoundLockThreshold > 0 ? _profile.SoundLockThreshold : 40;
            _tbLimit.Value = Math.Clamp(sLimit, 5, 100);
            _lblLimitVal.Text = $"{_tbLimit.Value}%";

            _chkBalanceApps.Checked = _profile.SoundLockBalanceExternalApps;
            _chkEq.Checked = _profile.SoundLockEqEnabled;
            _chkEqRumble.Checked = _profile.SoundLockEqCutRumble;
            int eqDb = Math.Clamp(_profile.SoundLockEqPresenceDb, 0, 8);
            _tbEq.Value = eqDb;
            _lblEqVal.Text = $"+{eqDb} dB";

            _chkShaper.Checked = _profile.SoundLockShaperEnabled;
            int tailDb = Math.Clamp(_profile.SoundLockShaperSustainDb, _tbShaper.Minimum, _tbShaper.Maximum);
            _tbShaper.Value = tailDb;
            _lblShaperVal.Text = $"-{tailDb} dB";
            _numShaperAttack.Value = Math.Clamp(_profile.SoundLockShaperAttackDb, (int)_numShaperAttack.Minimum, (int)_numShaperAttack.Maximum);

            RefreshAudioEndpoints();
            UpdateSoundLockControlsEnabled();
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

            string? outDevId = null;
            if (_cmbOutputDevice.SelectedIndex > 0 && _cmbOutputDevice.SelectedIndex < _audioEndpoints.Count)
            {
                outDevId = _audioEndpoints[_cmbOutputDevice.SelectedIndex].Id; // index 0 = Windows default -> null
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
                Cs2AntiFlashMaxDim = _tbMaxDim?.Value ?? _profile.Cs2AntiFlashMaxDim,
                SoundLockEnabled = _chkEnableDsp.Checked,
                SoundLockMode = "dsp_lookahead", // the only mode left
                SoundLockBoostDb = 0, // no boost: below the ceiling the game is untouched
                SoundLockGameVolume = _tbGameVol.Value,
                SoundLockThreshold = _tbLimit.Value,
                SoundLockBalanceExternalApps = _chkBalanceApps.Checked,
                SoundLockEqEnabled = _chkEq.Checked,
                SoundLockEqPresenceDb = _tbEq.Value,
                SoundLockEqCutRumble = _chkEqRumble.Checked,
                SoundLockShaperEnabled = _chkShaper.Checked,
                SoundLockShaperSustainDb = _tbShaper.Value,
                SoundLockShaperAttackDb = (int)_numShaperAttack.Value,
                SoundLockFastRecovery = true, // release is fixed at the shortest clean value
                SoundLockLookaheadMs = 5,
                SoundLockOutputDeviceId = outDevId
            };

            this.DialogResult = DialogResult.OK;
            this.Close();
        }
    }
}
