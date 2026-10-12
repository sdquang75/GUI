using System;
using System.Drawing;
using System.Windows.Forms;

namespace GUI.Forms
{
    public class MonitorGuardForm : Form
    {
        private readonly string _monitorName;
        private readonly Action _revertAction;
        private int _secondsRemaining = 12;
        private System.Windows.Forms.Timer _timer;

        private Label _lblMsg;
        private Label _lblCountdown;
        private Button _btnKeep;
        private Button _btnRevert;

        public bool KeptDisabled { get; private set; } = false;

        public MonitorGuardForm(string monitorName, Action revertAction)
        {
            _monitorName = monitorName;
            _revertAction = revertAction;

            this.Text = "Monitor Guard - Confirm Display State";
            this.Size = new Size(420, 210);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.TopMost = true;
            this.BackColor = Color.FromArgb(245, 245, 245);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            _lblMsg = new Label
            {
                Text = $"Monitor '{_monitorName}' has been disabled.\nIs your screen working normally?",
                Location = new Point(20, 16),
                Size = new Size(365, 42),
                Font = new Font("Segoe UI", 9.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblCountdown = new Label
            {
                Text = $"Automatically reverting back in {_secondsRemaining}s...",
                Location = new Point(20, 64),
                Size = new Size(365, 28),
                ForeColor = Color.DarkRed,
                Font = new Font("Segoe UI", 10F, FontStyle.Bold, GraphicsUnit.Point)
            };

            _timer = new System.Windows.Forms.Timer { Interval = 1000 };

            _btnKeep = new Button
            {
                Text = "Keep Disabled",
                Location = new Point(60, 110),
                Size = new Size(130, 36),
                BackColor = Color.FromArgb(0, 120, 215),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };
            _btnKeep.FlatAppearance.BorderSize = 0;
            _btnKeep.Click += (s, e) =>
            {
                _timer.Stop();
                KeptDisabled = true;
                this.Close();
            };

            _btnRevert = new Button
            {
                Text = "Revert Now",
                Location = new Point(210, 110),
                Size = new Size(130, 36),
                BackColor = Color.FromArgb(220, 220, 220),
                FlatStyle = FlatStyle.Flat
            };
            _btnRevert.Click += (s, e) =>
            {
                _timer.Stop();
                _revertAction();
                KeptDisabled = false;
                this.Close();
            };

            this.Controls.Add(_lblMsg);
            this.Controls.Add(_lblCountdown);
            this.Controls.Add(_btnKeep);
            this.Controls.Add(_btnRevert);
            _timer.Tick += (s, e) =>
            {
                _secondsRemaining--;
                if (_secondsRemaining <= 0)
                {
                    _timer.Stop();
                    _revertAction();
                    KeptDisabled = false;
                    this.Close();
                }
                else
                {
                    _lblCountdown.Text = $"Automatically reverting back in {_secondsRemaining}s...";
                }
            };
            _timer.Start();

            this.FormClosing += (s, e) =>
            {
                _timer.Stop();
                if (!KeptDisabled)
                {
                    _revertAction();
                }
            };
        }
    }
}
