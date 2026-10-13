using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace GUI.Forms
{
    public class ProcessSelectForm : Form
    {
        private ListBox _listBox;
        private Button _btnAdd;
        private Button _btnCancel;
        private Dictionary<string, string> _procMap = new(StringComparer.OrdinalIgnoreCase);

        public string? SelectedExe { get; private set; }
        public string? SelectedPath { get; private set; }

        public ProcessSelectForm()
        {
            InitializeComponent();
            PopulateProcesses();
        }

        private void InitializeComponent()
        {
            this.Text = "Select Running Game";
            this.Size = new Size(380, 420);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.MinimizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
            this.Icon = Utils.IconHelper.GetAppIcon();

            var lblPrompt = new Label
            {
                Text = "Select an active game process:",
                Location = new Point(14, 12),
                AutoSize = true
            };

            _listBox = new ListBox
            {
                Location = new Point(14, 38),
                Size = new Size(336, 290),
                IntegralHeight = false,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _listBox.DoubleClick += (s, e) => OnAddClicked();

            _btnAdd = new Button
            {
                Text = "Add",
                Location = new Point(14, 338),
                Size = new Size(80, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnAdd.Click += (s, e) => OnAddClicked();

            _btnCancel = new Button
            {
                Text = "Cancel",
                Location = new Point(270, 338),
                Size = new Size(80, 28),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnCancel.Click += (s, e) => { this.DialogResult = DialogResult.Cancel; this.Close(); };

            this.Controls.Add(lblPrompt);
            this.Controls.Add(_listBox);
            this.Controls.Add(_btnAdd);
            this.Controls.Add(_btnCancel);
            this.AcceptButton = _btnAdd;
            this.CancelButton = _btnCancel;
        }

        private void PopulateProcesses()
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string[] ignored = { "explorer.exe", "svchost.exe", "gui.exe", "conhost.exe", "taskhostw.exe", "cmd.exe", "powershell.exe", "idle.exe", "system" };

            var procs = Process.GetProcesses();
            try
            {
                Array.Sort(procs, (a, b) =>
                {
                    try { return string.Compare(a.ProcessName, b.ProcessName, StringComparison.OrdinalIgnoreCase); }
                    catch { return 0; }
                });

                foreach (var p in procs)
                {
                    try
                    {
                        string name = (p.ProcessName + ".exe").ToLowerInvariant();
                        if (Array.IndexOf(ignored, name) >= 0) continue;

                        string path = Utils.ProcessHelper.GetProcessPath(p.Id);
                        if (!seen.Contains(name))
                        {
                            seen.Add(name);
                            _procMap[name] = path;
                            _listBox.Items.Add(name);
                        }
                        else if (!string.IsNullOrEmpty(path) && (!_procMap.TryGetValue(name, out var existing) || string.IsNullOrEmpty(existing)))
                        {
                            _procMap[name] = path;
                        }
                    }
                    catch { }
                }
            }
            finally
            {
                foreach (var p in procs)
                {
                    try { p.Dispose(); } catch { }
                }
            }
        }

        private void OnAddClicked()
        {
            if (_listBox.SelectedItem is string sel)
            {
                SelectedExe = sel;
                _procMap.TryGetValue(sel, out var path);
                SelectedPath = path ?? "";
                this.DialogResult = DialogResult.OK;
                this.Close();
            }
        }
    }
}
