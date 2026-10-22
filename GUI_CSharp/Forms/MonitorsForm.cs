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
        private Font? _boldFont;

        public MonitorsForm()
        {
            this.Text = "Monitors Management";
            this.Size = new Size(510, 310);
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
                Size = new Size(470, 250)
            };

            _lvMonitors = new ListView
            {
                Location = new Point(10, 22),
                Size = new Size(450, 140),
                View = View.Details,
                FullRowSelect = true,
                GridLines = true,
                MultiSelect = false,
                BorderStyle = BorderStyle.FixedSingle,
                BackColor = Color.White
            };
            _lvMonitors.Columns.Add("Monitor Name", 140);
            _lvMonitors.Columns.Add("Role", 100);
            _lvMonitors.Columns.Add("Status", 65);
            _lvMonitors.Columns.Add("Device Instance ID", 140);
            _lvMonitors.SelectedIndexChanged += (s, e) => UpdateButtonStates();

            _btnDisableSelected = new Button
            {
                Text = "Disable Selected",
                Location = new Point(10, 170),
                Size = new Size(130, 27),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnDisableSelected.Click += (s, e) => OnToggleSelected(false);

            _btnEnableSelected = new Button
            {
                Text = "Enable Selected",
                Location = new Point(148, 170),
                Size = new Size(130, 27),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnEnableSelected.Click += (s, e) => OnToggleSelected(true);

            _btnRefresh = new Button
            {
                Text = "Refresh",
                Location = new Point(310, 170),
                Size = new Size(150, 27),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRefresh.Click += (s, e) => RefreshMonitors();

            var lblNotice = new Label
            {
                Text = "• Disabling a monitor allows true stretched resolution in Valorant / CS2.\n• Auto-revert guard ensures display recovers safely if needed.",
                Location = new Point(10, 204),
                Size = new Size(450, 36),
                ForeColor = Color.FromArgb(100, 116, 139),
                Font = new Font("Segoe UI", 8.25F, FontStyle.Regular, GraphicsUnit.Point)
            };

            gbList.Controls.Add(_lvMonitors);
            gbList.Controls.Add(_btnDisableSelected);
            gbList.Controls.Add(_btnEnableSelected);
            gbList.Controls.Add(_btnRefresh);
            gbList.Controls.Add(lblNotice);

            this.Controls.Add(gbList);
        }

        private void UpdateButtonStates()
        {
            if (_lvMonitors.SelectedItems.Count == 0)
            {
                _btnDisableSelected.Enabled = false;
                _btnEnableSelected.Enabled = false;
                return;
            }

            var m = _lvMonitors.SelectedItems[0].Tag as MonitorInfo;
            if (m == null)
            {
                _btnDisableSelected.Enabled = false;
                _btnEnableSelected.Enabled = false;
                return;
            }

            _btnDisableSelected.Enabled = m.Enabled && !m.IsPrimary;
            _btnEnableSelected.Enabled = !m.Enabled;
        }

        private void RefreshMonitors()
        {
            _lvMonitors.BeginUpdate();
            _lvMonitors.Items.Clear();

            var list = MonitorEngine.EnumerateMonitors();
            foreach (var m in list)
            {
                var item = new ListViewItem(m.FriendlyName);
                item.SubItems.Add(m.IsPrimary ? "Primary (Main)" : "Secondary");
                item.SubItems.Add(m.Enabled ? "Enabled" : "Disabled");
                item.SubItems.Add(m.InstanceId);
                item.Tag = m;
                if (!m.Enabled)
                {
                    item.ForeColor = Color.DarkRed;
                }
                else if (m.IsPrimary)
                {
                    _boldFont ??= new Font(_lvMonitors.Font, FontStyle.Bold);
                    item.Font = _boldFont;
                }
                _lvMonitors.Items.Add(item);
            }

            _lvMonitors.EndUpdate();
            UpdateButtonStates();
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

            if (!enable && m.IsPrimary)
            {
                MessageBox.Show(this, "The primary display cannot be disabled.", "Action Not Allowed", MessageBoxButtons.OK, MessageBoxIcon.Warning);
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

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _boldFont?.Dispose();
                _boldFont = null;
            }
            base.Dispose(disposing);
        }
    }
}
