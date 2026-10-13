using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using GUI.Engine;

namespace GUI.Forms
{
    public class MonitorsForm : Form
    {
        private ListView _lvMonitors;
        private Button _btnDisableSelected;
        private Button _btnEnableSelected;
        private Button _btnRefresh;

        public MonitorsForm()
        {
            this.Text = "Monitors Management";
            this.Size = new Size(460, 270);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            InitializeComponent();
            RefreshMonitors();
        }

        private void InitializeComponent()
        {
            var gbList = new GroupBox
            {
                Text = "Connected Display Devices",
                Location = new Point(12, 10),
                Size = new Size(420, 210)
            };

            _lvMonitors = new ListView
            {
                Location = new Point(10, 22),
                Size = new Size(400, 145),
                View = View.Details,
                FullRowSelect = true,
                GridLines = true,
                MultiSelect = false,
                BorderStyle = BorderStyle.FixedSingle,
                BackColor = Color.White
            };
            _lvMonitors.Columns.Add("Monitor Name", 140);
            _lvMonitors.Columns.Add("Status", 70);
            _lvMonitors.Columns.Add("Device Instance ID", 170);

            _btnDisableSelected = new Button
            {
                Text = "Disable Selected",
                Location = new Point(10, 173),
                Size = new Size(125, 27),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnDisableSelected.Click += (s, e) => OnToggleSelected(false);

            _btnEnableSelected = new Button
            {
                Text = "Enable Selected",
                Location = new Point(142, 173),
                Size = new Size(125, 27),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnEnableSelected.Click += (s, e) => OnToggleSelected(true);

            _btnRefresh = new Button
            {
                Text = "Refresh",
                Location = new Point(274, 173),
                Size = new Size(136, 27),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRefresh.Click += (s, e) => RefreshMonitors();

            gbList.Controls.Add(_lvMonitors);
            gbList.Controls.Add(_btnDisableSelected);
            gbList.Controls.Add(_btnEnableSelected);
            gbList.Controls.Add(_btnRefresh);

            this.Controls.Add(gbList);
        }

        private void RefreshMonitors()
        {
            _lvMonitors.BeginUpdate();
            _lvMonitors.Items.Clear();

            var list = MonitorEngine.EnumerateMonitors();
            foreach (var m in list)
            {
                var item = new ListViewItem(m.FriendlyName);
                item.SubItems.Add(m.Enabled ? "Enabled" : "Disabled");
                item.SubItems.Add(m.InstanceId);
                item.Tag = m;
                if (!m.Enabled)
                {
                    item.ForeColor = Color.DarkRed;
                }
                _lvMonitors.Items.Add(item);
            }

            _lvMonitors.EndUpdate();
        }

        private void OnToggleSelected(bool enable)
        {
            if (_lvMonitors.SelectedItems.Count == 0)
            {
                MessageBox.Show(this, "Please select a monitor from the list first.", "No Selection", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            var m = _lvMonitors.SelectedItems[0].Tag as MonitorInfo;
            if (m == null) return;

            if (m.Enabled == enable)
            {
                MessageBox.Show(this, $"Monitor is already {(enable ? "enabled" : "disabled")}.", "Notice", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            if (!enable)
            {
                var (ok, msg) = MonitorEngine.SetMonitorEnabled(m.InstanceId, false);
                if (!ok)
                {
                    MessageBox.Show(this, msg, "Disable Failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }

                RefreshMonitors();
                using var guard = new MonitorGuardForm(m.FriendlyName, () =>
                {
                    MonitorEngine.SetMonitorEnabled(m.InstanceId, true);
                    RefreshMonitors();
                });
                guard.ShowDialog(this);
                RefreshMonitors();
            }
            else
            {
                var (ok, msg) = MonitorEngine.SetMonitorEnabled(m.InstanceId, true);
                if (!ok)
                {
                    MessageBox.Show(this, msg, "Enable Failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                RefreshMonitors();
            }
        }
    }
}
