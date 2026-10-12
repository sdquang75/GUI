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
        private Button _btnDisableSecondary;
        private Button _btnEnableAll;
        private Label _lblNotice;

        public MonitorsForm()
        {
            this.Text = "Monitors Management";
            this.Size = new Size(460, 420);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterParent;
            this.BackColor = Color.FromArgb(245, 245, 245);
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

            var gbActions = new GroupBox
            {
                Text = "Multi-Monitor Stretched Helper",
                Location = new Point(12, 226),
                Size = new Size(420, 145)
            };

            _btnDisableSecondary = new Button
            {
                Text = "Disable Secondary Monitors",
                Location = new Point(10, 22),
                Size = new Size(195, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnDisableSecondary.Click += (s, e) => OnDisableSecondary();

            _btnEnableAll = new Button
            {
                Text = "Enable All Monitors",
                Location = new Point(215, 22),
                Size = new Size(195, 30),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnEnableAll.Click += (s, e) => OnEnableAll();

            _lblNotice = new Label
            {
                Text = "• Stretched resolutions (Valorant/CS) on multi-monitor setups often snap back or get black bars unless secondary monitors are temporarily disabled.\n• Safety Guard: A 12s auto-revert countdown will trigger when disabling a monitor to prevent display lockouts.",
                Location = new Point(10, 58),
                Size = new Size(400, 78),
                ForeColor = Color.FromArgb(71, 85, 105),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };

            gbActions.Controls.Add(_btnDisableSecondary);
            gbActions.Controls.Add(_btnEnableAll);
            gbActions.Controls.Add(_lblNotice);

            this.Controls.Add(gbList);
            this.Controls.Add(gbActions);
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

        private void OnDisableSecondary()
        {
            var monitors = MonitorEngine.EnumerateMonitors();
            if (monitors.Count <= 1)
            {
                MessageBox.Show(this, "Only 1 monitor detected. Nothing to disable.", "Notice", MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }

            var secondaries = monitors.GetRange(1, monitors.Count - 1);
            var confirm = MessageBox.Show(this, $"This will disable {secondaries.Count} secondary monitor(s).\nAre you sure you want to proceed?", "Confirm Disable", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (confirm != DialogResult.Yes) return;

            var disabledList = new List<MonitorInfo>();
            foreach (var m in secondaries)
            {
                if (m.Enabled)
                {
                    var (ok, _) = MonitorEngine.SetMonitorEnabled(m.InstanceId, false);
                    if (ok) disabledList.Add(m);
                }
            }

            RefreshMonitors();

            if (disabledList.Count > 0)
            {
                using var guard = new MonitorGuardForm($"{disabledList.Count} Secondary Monitor(s)", () =>
                {
                    foreach (var m in disabledList)
                    {
                        MonitorEngine.SetMonitorEnabled(m.InstanceId, true);
                    }
                    RefreshMonitors();
                });
                guard.ShowDialog(this);
                RefreshMonitors();
            }
        }

        private void OnEnableAll()
        {
            var monitors = MonitorEngine.EnumerateMonitors();
            foreach (var m in monitors)
            {
                if (!m.Enabled)
                {
                    MonitorEngine.SetMonitorEnabled(m.InstanceId, true);
                }
            }
            RefreshMonitors();
        }
    }
}
