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
        private readonly System.Windows.Forms.Timer _timer;

        private Label _lblMsg;
        private Label _lblCountdown;
        private Button _btnKeep;
        private Button _btnRevert;

        public bool KeptDisabled { get; private set; } = false;

        public MonitorGuardForm(string monitorName, Action revertAction)
        {
            _monitorName = monitorName;
            _revertAction = revertAction;

            this.Text = "Confirm Display State";
            this.ClientSize = new Size(384, 168);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.TopMost = true;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            var gbState = new GroupBox
            {
                Text = "Display State Confirmation",
                Location = new Point(12, 10),
                Size = new Size(360, 108),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            _lblMsg = new Label
            {
                Text = $"Monitor '{_monitorName}' has been disabled.\nDo you want to keep these display settings?",
                Location = new Point(12, 24),
                Size = new Size(336, 40),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(30, 41, 59)
            };

            _lblCountdown = new Label
            {
                Text = $"Reverting to previous settings in {_secondsRemaining}s...",
                Location = new Point(12, 70),
                Size = new Size(336, 24),
                ForeColor = Color.FromArgb(180, 83, 9),
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point)
            };

            gbState.Controls.Add(_lblMsg);
            gbState.Controls.Add(_lblCountdown);

            _timer = new System.Windows.Forms.Timer { Interval = 1000 };

            _btnKeep = new Button
            {
                Text = "Keep changes",
                Location = new Point(144, 128),
                Size = new Size(110, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnKeep.Click += (s, e) =>
            {
                _timer.Stop();
                KeptDisabled = true;
                this.Close();
            };

            _btnRevert = new Button
            {
                Text = "Revert",
                Location = new Point(262, 128),
                Size = new Size(110, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRevert.Click += (s, e) =>
            {
                _timer.Stop();
                KeptDisabled = false;
                this.Close();
            };

            this.Controls.Add(gbState);
            this.Controls.Add(_btnKeep);
            this.Controls.Add(_btnRevert);

            this.AcceptButton = _btnKeep;
            this.CancelButton = _btnRevert;
            _timer.Tick += (s, e) =>
            {
                _secondsRemaining--;
                if (_secondsRemaining <= 0)
                {
                    _timer.Stop();
                    KeptDisabled = false;
                    this.Close();
                }
                else
                {
                    _lblCountdown.Text = $"Reverting to previous settings in {_secondsRemaining}s...";
                }
            };
            _timer.Start();

            bool hasReverted = false;
            this.FormClosing += (s, e) =>
            {
                try { _timer.Stop(); } catch { }
                try { _timer.Dispose(); } catch { }
                if (!KeptDisabled && !hasReverted)
                {
                    hasReverted = true;
                    try
                    {
                        _revertAction?.Invoke();
                    }
                    catch (Exception ex)
                    {
                        Program.Log($"MonitorGuardForm revert action failed: {ex.Message}");
                    }
                }
            };
        }
    }
}
