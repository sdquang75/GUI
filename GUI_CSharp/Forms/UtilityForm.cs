using System;
using System.Drawing;
using System.Windows.Forms;
using GUI.Engine;

namespace GUI.Forms
{
    public class UtilityForm : Form
    {
        private ListView _lvItems;
        private Button _btnSetStretched;
        private Button _btnRevert;
        private Button _btnRefresh;
        private Button _btnRestartGpu;
        private Label _lblStatus;

        public UtilityForm()
        {
            this.Text = "Display Scaling Utility (Laptop Stretched Fix)";
            this.Size = new Size(520, 490);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(245, 245, 245);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            InitializeComponent();
            RefreshItems();
        }

        private void InitializeComponent()
        {
            var gbReg = new GroupBox
            {
                Text = "Registry Scaling (4:3 Stretched Fix)",
                Location = new Point(12, 10),
                Size = new Size(480, 275)
            };

            var lblExplain = new Label
            {
                Text = "Sets display driver scaling to 3 (Stretched / Full Screen) in Windows Registry.\nFixes 4:3 black bars on laptops without MUX switch (Intel/AMD iGPU).",
                Location = new Point(10, 20),
                Size = new Size(460, 36),
                ForeColor = Color.FromArgb(51, 65, 85)
            };

            _lvItems = new ListView
            {
                Location = new Point(10, 58),
                Size = new Size(460, 168),
                View = View.Details,
                FullRowSelect = true,
                GridLines = true,
                BorderStyle = BorderStyle.FixedSingle,
                BackColor = Color.White
            };
            _lvItems.Columns.Add("Type", 110);
            _lvItems.Columns.Add("Adapter / Target", 150);
            _lvItems.Columns.Add("Current Scaling", 180);

            _btnSetStretched = new Button
            {
                Text = "⚡ Set All Scaling to 3 (Stretched)",
                Location = new Point(10, 234),
                Size = new Size(220, 32),
                BackColor = Color.FromArgb(0, 120, 215),
                ForeColor = Color.White,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                FlatStyle = FlatStyle.Flat
            };
            _btnSetStretched.FlatAppearance.BorderSize = 0;
            _btnSetStretched.Click += (s, e) => OnSetStretched();

            _btnRevert = new Button
            {
                Text = "Revert Scaling",
                Location = new Point(238, 234),
                Size = new Size(130, 32),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRevert.Click += (s, e) => OnRevert();

            _btnRefresh = new Button
            {
                Text = "Refresh",
                Location = new Point(376, 234),
                Size = new Size(94, 32),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRefresh.Click += (s, e) => RefreshItems();

            gbReg.Controls.Add(lblExplain);
            gbReg.Controls.Add(_lvItems);
            gbReg.Controls.Add(_btnSetStretched);
            gbReg.Controls.Add(_btnRevert);
            gbReg.Controls.Add(_btnRefresh);

            var gbRestart = new GroupBox
            {
                Text = "Restart GPU Driver",
                Location = new Point(12, 292),
                Size = new Size(480, 115)
            };

            var lblRestartInfo = new Label
            {
                Text = "Reloads the graphics driver without rebooting Windows so registry changes apply immediately.\nThe screen will blink briefly and Windows will chime.",
                Location = new Point(10, 22),
                Size = new Size(460, 34),
                ForeColor = Color.FromArgb(71, 85, 105)
            };

            _btnRestartGpu = new Button
            {
                Text = "🔄 Restart GPU Driver (Win + Ctrl + Shift + B)",
                Location = new Point(10, 62),
                Size = new Size(460, 36),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9.5F, FontStyle.Bold, GraphicsUnit.Point),
                FlatStyle = FlatStyle.System
            };
            _btnRestartGpu.Click += (s, e) => OnRestartGpu();

            gbRestart.Controls.Add(lblRestartInfo);
            gbRestart.Controls.Add(_btnRestartGpu);

            _lblStatus = new Label
            {
                Text = "Ready.",
                Location = new Point(14, 418),
                Size = new Size(476, 26),
                ForeColor = Color.FromArgb(3, 105, 161),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };

            this.Controls.Add(gbReg);
            this.Controls.Add(gbRestart);
            this.Controls.Add(_lblStatus);
        }

        private void RefreshItems()
        {
            _lvItems.BeginUpdate();
            _lvItems.Items.Clear();

            var items = ScalingUtilityEngine.ReadAllScalingItems();
            foreach (var item in items)
            {
                var lvi = new ListViewItem(item.SourceType);
                lvi.SubItems.Add(item.Description);
                lvi.SubItems.Add(item.ValueLabel);
                lvi.Tag = item;
                if (item.CurrentValue == 3)
                {
                    lvi.ForeColor = Color.FromArgb(22, 163, 74);
                }
                else if (item.CurrentValue == 4)
                {
                    lvi.ForeColor = Color.FromArgb(202, 138, 4);
                }
                _lvItems.Items.Add(lvi);
            }

            _lvItems.EndUpdate();
            _lblStatus.Text = $"Detected {items.Count} scaling registry entries.";
            _lblStatus.ForeColor = Color.FromArgb(3, 105, 161);
        }

        private void OnSetStretched()
        {
            var (ok, msg, count) = ScalingUtilityEngine.SetAllScaling(3);
            _lblStatus.Text = msg;
            _lblStatus.ForeColor = ok ? Color.FromArgb(22, 163, 74) : Color.DarkRed;
            RefreshItems();

            if (ok)
            {
                var prompt = MessageBox.Show(this, $"{msg}\n\nWould you like to restart the GPU driver now to apply changes immediately?", "Registry Updated", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                if (prompt == DialogResult.Yes)
                {
                    OnRestartGpu();
                }
            }
        }

        private void OnRevert()
        {
            var (ok, msg, count) = ScalingUtilityEngine.RevertScaling();
            _lblStatus.Text = msg;
            _lblStatus.ForeColor = ok ? Color.FromArgb(22, 163, 74) : Color.DarkRed;
            RefreshItems();

            if (ok)
            {
                var prompt = MessageBox.Show(this, $"{msg}\n\nRestart GPU driver now to apply reverted values?", "Registry Reverted", MessageBoxButtons.YesNo, MessageBoxIcon.Information);
                if (prompt == DialogResult.Yes)
                {
                    OnRestartGpu();
                }
            }
        }

        private void OnRestartGpu()
        {
            _lblStatus.Text = "Sending GPU driver restart signal...";
            ScalingUtilityEngine.RestartGpuDriver();
            _lblStatus.Text = "GPU driver restarted successfully.";
            _lblStatus.ForeColor = Color.FromArgb(22, 163, 74);
        }
    }
}
