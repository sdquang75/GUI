using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using GUI.Config;

namespace GUI.Forms
{
    public class GameConfigForm : Form
    {
        private readonly string _exeName;
        private readonly GameProfile _profile;

        private TrackBar _tbVibrance;
        private Label _lblVibranceVal;

        private CheckBox _chkGamma;
        private TrackBar _tbGamma;
        private Label _lblGammaVal;

        private CheckBox _chkBoost;
        private TrackBar _tbBoost;
        private Label _lblBoostVal;

        private Button _btnSave;
        private Button _btnCancel;

        public GameProfile ResultProfile { get; private set; }

        public GameConfigForm(string exeName, GameProfile? initialProfile = null)
        {
            _exeName = exeName;
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
        }

        private void InitializeComponent()
        {
            this.Text = _exeName;
            this.Size = new Size(360, 420);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            // Group 1: In-game Vibrance Level
            var gbVib = new GroupBox
            {
                Text = "In-game Vibrance Level",
                Location = new Point(14, 10),
                Size = new Size(316, 76),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblVibranceVal = new Label
            {
                Text = "50%",
                Location = new Point(255, 14),
                Size = new Size(50, 20),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _tbVibrance = new TrackBar
            {
                Minimum = 0,
                Maximum = 100,
                Value = 50,
                TickFrequency = 10,
                Location = new Point(10, 32),
                Size = new Size(296, 35)
            };
            _tbVibrance.ValueChanged += (s, e) => _lblVibranceVal.Text = $"{_tbVibrance.Value}%";

            gbVib.Controls.Add(_lblVibranceVal);
            gbVib.Controls.Add(_tbVibrance);

            // Group 2: Gamma Setting
            var gbGamma = new GroupBox
            {
                Text = "Gamma Setting",
                Location = new Point(14, 94),
                Size = new Size(316, 92),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _chkGamma = new CheckBox
            {
                Text = "Enable Gamma",
                Location = new Point(12, 22),
                AutoSize = true
            };

            _lblGammaVal = new Label
            {
                Text = "1.80",
                Location = new Point(255, 22),
                Size = new Size(50, 20),
                TextAlign = ContentAlignment.MiddleRight
            };

            _tbGamma = new TrackBar
            {
                Minimum = 140,
                Maximum = 240,
                Value = 180,
                TickFrequency = 10,
                Location = new Point(10, 48),
                Size = new Size(296, 35)
            };
            _tbGamma.ValueChanged += (s, e) => _lblGammaVal.Text = $"{(_tbGamma.Value / 100.0):0.00}";

            gbGamma.Controls.Add(_chkGamma);
            gbGamma.Controls.Add(_lblGammaVal);
            gbGamma.Controls.Add(_tbGamma);

            // Group 3: Black Boost
            var gbBoost = new GroupBox
            {
                Text = "Black Boost",
                Location = new Point(14, 194),
                Size = new Size(316, 92),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _chkBoost = new CheckBox
            {
                Text = "Enable Black Boost",
                Location = new Point(12, 22),
                AutoSize = true
            };

            _lblBoostVal = new Label
            {
                Text = "0%",
                Location = new Point(255, 22),
                Size = new Size(50, 20),
                TextAlign = ContentAlignment.MiddleRight
            };

            _tbBoost = new TrackBar
            {
                Minimum = 0,
                Maximum = 100,
                Value = 0,
                TickFrequency = 10,
                Location = new Point(10, 48),
                Size = new Size(296, 35)
            };
            _tbBoost.ValueChanged += (s, e) => _lblBoostVal.Text = $"{_tbBoost.Value}%";

            gbBoost.Controls.Add(_chkBoost);
            gbBoost.Controls.Add(_lblBoostVal);
            gbBoost.Controls.Add(_tbBoost);

            // Bottom Buttons
            _btnSave = new Button
            {
                Text = "Save",
                Location = new Point(14, 335),
                Size = new Size(80, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnSave.Click += (s, e) => OnSave();

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(250, 335),
                Size = new Size(80, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnCancel.Click += (s, e) => { this.DialogResult = DialogResult.Cancel; this.Close(); };

            this.Controls.Add(gbVib);
            this.Controls.Add(gbGamma);
            this.Controls.Add(gbBoost);
            this.Controls.Add(_btnSave);
            this.Controls.Add(_btnCancel);

            this.AcceptButton = _btnSave;
            this.CancelButton = _btnCancel;
        }

        private void LoadProfileData()
        {
            _tbVibrance.Value = Math.Clamp(_profile.Vibrance, 0, 100);
            _lblVibranceVal.Text = $"{_tbVibrance.Value}%";

            _chkGamma.Checked = _profile.GammaEnabled;
            int gVal = (int)Math.Round(_profile.Gamma * 100.0);
            _tbGamma.Value = Math.Clamp(gVal, 140, 240);
            _lblGammaVal.Text = $"{(_tbGamma.Value / 100.0):0.00}";

            _chkBoost.Checked = _profile.BoostEnabled;
            _tbBoost.Value = Math.Clamp(_profile.Boost, 0, 100);
            _lblBoostVal.Text = $"{_tbBoost.Value}%";
        }

        private void OnSave()
        {
            ResultProfile = new GameProfile
            {
                Name = string.IsNullOrEmpty(_profile.Name) ? Path.GetFileNameWithoutExtension(_exeName) : _profile.Name,
                Path = _profile.Path,
                VibranceEnabled = true,
                Vibrance = _tbVibrance.Value,
                GammaEnabled = _chkGamma.Checked,
                Gamma = Math.Round(_tbGamma.Value / 100.0, 2),
                BoostEnabled = _chkBoost.Checked,
                Boost = _tbBoost.Value
            };

            this.DialogResult = DialogResult.OK;
            this.Close();
        }
    }
}
