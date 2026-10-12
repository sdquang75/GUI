using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using GUI.Config;
using GUI.Engine;
using GUI.Utils;

namespace GUI.Forms
{
    public class MainForm : Form
    {
        private AppConfig _config;
        private UnifiedVibranceEngine _vibrance;
        private GammaEngine _gammaEngine;
        private ActiveWindowObserver _observer;

        // --- Left Panel: Vibrance & Games ---
        private CheckBox _chkAutostart;
        private TrackBar _tbDesktopVib;
        private Label _lblDesktopVibVal;
        private Button _btnAdd;
        private Button _btnAddManual;
        private Button _btnRemove;
        private ListView _lvGames;
        private Label _lblObserverStatus;
        private NotifyIcon _notifyIcon;
        private System.Windows.Forms.Timer? _gammaEnforceTimer;
        private string? _currentGameExe = null;

        // --- Right Panel: Quick Resolutions ---
        private Label _lblCurrentRes;
        private Button _btnRefreshRes;
        private List<Button> _resButtons = new();
        private TextBox _txtCustomRes;
        private Button _btnApplyCustom;
        private Button _btnOpenGpuPanel;
        private Label _lblResStatus;
        private Button _btnOpenUtility;
        private Button _btnOpenMonitors;

        private static readonly (int Width, int Height)[] QuickResolutions = new[]
        {
            (1920, 1080),
            (2560, 1440),
            (1920, 1440),
            (1620, 1080),
            (1568, 1080),
            (1440, 1080),
            (1600, 1080),
            (1280, 1080),
            (1350, 1080),
            (1280, 1024),
            (1080, 1080),
            (1280, 960),
            (1024, 768),
            (800, 1080)
        };

        public MainForm()
        {
            _config = ConfigManager.Load();
            _vibrance = new UnifiedVibranceEngine();
            _gammaEngine = new GammaEngine();
            _observer = new ActiveWindowObserver();

            _gammaEnforceTimer = new System.Windows.Forms.Timer { Interval = 1000 };
            _gammaEnforceTimer.Tick += (s, e) =>
            {
                if (_currentGameExe != null) _gammaEngine.EnforceIfNeeded();
            };

            this.Icon = IconHelper.GetAppIcon();

            InitializeComponent();
            LoadConfigToUi();

            this.Load += (s, e) => Program.Log("MainForm.Load fired");
            this.Shown += (s, e) =>
            {
                Program.Log("MainForm.Shown fired");
                InitObserver();
                RefreshCurrentResolution();
            };

            if (_config.Autostart)
            {
                AutostartHelper.SetAutostart(true);
            }

            _vibrance.DesktopLevel = _config.Desktop.Vibrance;
            _vibrance.SetLevel(_config.Desktop.Vibrance);
            Program.Log("MainForm constructor completed");
        }

        private void InitializeComponent()
        {
            this.Text = "GUI - Vibrance & QuickRes";
            this.ClientSize = new Size(722, 500);
            this.FormBorderStyle = FormBorderStyle.FixedSingle;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);

            int pad = 10;
            int colW = 344;

            // ==========================================
            // 1. LEFT SIDE: Settings & Program Settings
            // ==========================================
            var gbSettings = new GroupBox
            {
                Text = "Settings",
                Location = new Point(pad, 8),
                Size = new Size(colW, 118)
            };

            _chkAutostart = new CheckBox
            {
                Text = "Autostart GUI",
                Location = new Point(12, 20),
                AutoSize = true
            };
            _chkAutostart.CheckedChanged += (s, e) =>
            {
                _config.Autostart = _chkAutostart.Checked;
                ConfigManager.Save(_config);
                AutostartHelper.SetAutostart(_chkAutostart.Checked);
            };

            var gbWinVib = new GroupBox
            {
                Text = "Windows Vibrance Level",
                Location = new Point(10, 46),
                Size = new Size(colW - 20, 62)
            };

            _lblDesktopVibVal = new Label
            {
                Text = "50 %",
                Location = new Point(gbWinVib.Width - 55, 26),
                Size = new Size(45, 20),
                TextAlign = ContentAlignment.MiddleRight
            };

            _tbDesktopVib = new TrackBar
            {
                Minimum = 0,
                Maximum = 100,
                Value = 50,
                TickFrequency = 10,
                Location = new Point(8, 20),
                Size = new Size(gbWinVib.Width - 70, 36)
            };
            _tbDesktopVib.ValueChanged += (s, e) =>
            {
                int val = _tbDesktopVib.Value;
                _lblDesktopVibVal.Text = $"{val} %";
                _config.Desktop.Vibrance = val;
                _vibrance.DesktopLevel = val;
                ConfigManager.Save(_config);

                if (_currentGameExe == null)
                {
                    _vibrance.SetLevel(val);
                }
            };

            gbWinVib.Controls.Add(_tbDesktopVib);
            gbWinVib.Controls.Add(_lblDesktopVibVal);
            gbSettings.Controls.Add(_chkAutostart);
            gbSettings.Controls.Add(gbWinVib);

            var gbProg = new GroupBox
            {
                Text = "Program Settings",
                Location = new Point(pad, 132),
                Size = new Size(colW, 324)
            };

            _btnAdd = new Button
            {
                Text = "Add",
                Location = new Point(10, 20),
                Size = new Size(68, 26),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnAdd.Click += (s, e) => OnAddFromRunning();

            _btnAddManual = new Button
            {
                Text = "Add manually",
                Location = new Point(84, 20),
                Size = new Size(95, 26),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnAddManual.Click += (s, e) => OnAddManually();

            _btnRemove = new Button
            {
                Text = "Remove",
                Location = new Point(185, 20),
                Size = new Size(68, 26),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRemove.Click += (s, e) => OnRemoveSelected();

            _lvGames = new ListView
            {
                Location = new Point(10, 52),
                Size = new Size(colW - 20, 262),
                View = View.LargeIcon,
                MultiSelect = false,
                HideSelection = false,
                BorderStyle = BorderStyle.FixedSingle,
                BackColor = Color.White,
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point)
            };
            _lvGames.DoubleClick += (s, e) => OnEditSelected();
            _lvGames.KeyDown += (s, e) =>
            {
                if (e.KeyCode == Keys.Delete) OnRemoveSelected();
            };

            gbProg.Controls.Add(_btnAdd);
            gbProg.Controls.Add(_btnAddManual);
            gbProg.Controls.Add(_btnRemove);
            gbProg.Controls.Add(_lvGames);

            var lblObserverPrompt = new Label
            {
                Text = "Observer status:",
                Location = new Point(pad, 466),
                AutoSize = true
            };

            _lblObserverStatus = new Label
            {
                Text = $"Running! [{_vibrance.EngineName}]",
                Location = new Point(pad + 95, 466),
                AutoSize = true,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(22, 163, 74)
            };

            this.Controls.Add(gbSettings);
            this.Controls.Add(gbProg);
            this.Controls.Add(lblObserverPrompt);
            this.Controls.Add(_lblObserverStatus);

            // ==========================================
            // 2. RIGHT SIDE: Quick Resolutions
            // ==========================================
            int rightX = pad + colW + 14;

            var gbQuickRes = new GroupBox
            {
                Text = "Quick Resolutions",
                Location = new Point(rightX, 8),
                Size = new Size(colW, 448)
            };

            _lblCurrentRes = new Label
            {
                Text = "Detecting resolution...",
                Location = new Point(10, 20),
                Size = new Size(236, 24),
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(30, 41, 59)
            };

            _btnRefreshRes = new Button
            {
                Text = "Refresh",
                Location = new Point(252, 17),
                Size = new Size(82, 26),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnRefreshRes.Click += (s, e) => RefreshCurrentResolution();

            gbQuickRes.Controls.Add(_lblCurrentRes);
            gbQuickRes.Controls.Add(_btnRefreshRes);

            // Grid of Resolution Buttons (2 columns x 7 rows)
            var pnlGrid = new TableLayoutPanel
            {
                Location = new Point(8, 48),
                Size = new Size(328, 276),
                ColumnCount = 2,
                RowCount = 7,
                Padding = new Padding(0),
                Margin = new Padding(0)
            };
            pnlGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            pnlGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));

            for (int i = 0; i < 7; i++)
            {
                pnlGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 100F / 7F));
            }

            _resButtons.Clear();
            foreach (var (w, h) in QuickResolutions)
            {
                var btn = new Button
                {
                    Text = $"{w} x {h}",
                    Dock = DockStyle.Fill,
                    Margin = new Padding(2),
                    BackColor = Color.White,
                    FlatStyle = FlatStyle.System,
                    Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point),
                    Tag = (w, h)
                };
                btn.Click += (s, e) => ApplyResolution(w, h);
                _resButtons.Add(btn);
                pnlGrid.Controls.Add(btn);
            }
            gbQuickRes.Controls.Add(pnlGrid);

            // Custom Resolution Row
            var lblCustomPrompt = new Label
            {
                Text = "Custom:",
                Location = new Point(10, 332),
                AutoSize = true
            };

            _txtCustomRes = new TextBox
            {
                Text = "1440x1080",
                Location = new Point(62, 329),
                Size = new Size(110, 24)
            };

            _btnApplyCustom = new Button
            {
                Text = "Apply",
                Location = new Point(178, 328),
                Size = new Size(72, 26),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                FlatStyle = FlatStyle.System
            };
            _btnApplyCustom.Click += (s, e) =>
            {
                var parsed = ParseRes(_txtCustomRes.Text);
                if (parsed.HasValue)
                {
                    ApplyResolution(parsed.Value.Width, parsed.Value.Height);
                }
                else
                {
                    MessageBox.Show(this, "Please enter a valid resolution, e.g. 1440x1080.", "Invalid Resolution", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            };

            _btnOpenGpuPanel = new Button
            {
                Text = "GPU Panel",
                Location = new Point(254, 328),
                Size = new Size(80, 26),
                BackColor = Color.White,
                FlatStyle = FlatStyle.System
            };
            _btnOpenGpuPanel.Click += (s, e) => DisplayEngine.OpenGpuControlPanel();

            _lblResStatus = new Label
            {
                Text = "Click any resolution button to switch instantly.",
                Location = new Point(10, 365),
                Size = new Size(324, 72),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(71, 85, 105)
            };

            gbQuickRes.Controls.Add(lblCustomPrompt);
            gbQuickRes.Controls.Add(_txtCustomRes);
            gbQuickRes.Controls.Add(_btnApplyCustom);
            gbQuickRes.Controls.Add(_btnOpenGpuPanel);
            gbQuickRes.Controls.Add(_lblResStatus);

            this.Controls.Add(gbQuickRes);

            // ==========================================
            // 3. BOTTOM RIGHT: "Utility" and "Monitors" buttons
            // ==========================================
            _btnOpenUtility = new Button
            {
                Text = "🛠️ Utility",
                Location = new Point(rightX + colW - 248, 462),
                Size = new Size(118, 28),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                FlatStyle = FlatStyle.System
            };
            _btnOpenUtility.Click += (s, e) =>
            {
                using var dlg = new UtilityForm();
                dlg.ShowDialog(this);
                RefreshCurrentResolution();
            };
            this.Controls.Add(_btnOpenUtility);

            _btnOpenMonitors = new Button
            {
                Text = "🖥️ Monitors",
                Location = new Point(rightX + colW - 122, 462),
                Size = new Size(122, 28),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                FlatStyle = FlatStyle.System
            };
            _btnOpenMonitors.Click += (s, e) =>
            {
                using var dlg = new MonitorsForm();
                dlg.ShowDialog(this);
            };
            this.Controls.Add(_btnOpenMonitors);

            // ==========================================
            // System Tray
            // ==========================================
            var trayMenu = new ContextMenuStrip();
            trayMenu.Items.Add("Open GUI", null, (s, e) => RestoreFromTray());
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add("Exit", null, (s, e) => { this.Close(); });

            _notifyIcon = new NotifyIcon
            {
                Text = "GUI",
                Icon = this.Icon,
                ContextMenuStrip = trayMenu,
                Visible = true
            };
            _notifyIcon.DoubleClick += (s, e) => RestoreFromTray();

            this.Resize += (s, e) =>
            {
                if (this.WindowState == FormWindowState.Minimized)
                {
                    this.Hide();
                }
            };

            this.FormClosing += OnFormClosing;
        }

        private void RefreshCurrentResolution()
        {
            var cur = DisplayEngine.GetCurrentResolution();
            if (cur.HasValue)
            {
                string ratio = DisplayEngine.GetAspectRatioLabel(cur.Value.Width, cur.Value.Height);
                _lblCurrentRes.Text = $"{cur.Value.Width} x {cur.Value.Height} @ {cur.Value.Frequency}Hz ({ratio})";

                foreach (var btn in _resButtons)
                {
                    if (btn.Tag is ValueTuple<int, int> res)
                    {
                        if (res.Item1 == cur.Value.Width && res.Item2 == cur.Value.Height)
                        {
                            btn.Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
                            btn.Text = $"✓ {res.Item1} x {res.Item2}";
                        }
                        else
                        {
                            btn.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
                            btn.Text = $"{res.Item1} x {res.Item2}";
                        }
                    }
                }
            }
            else
            {
                _lblCurrentRes.Text = "Unknown resolution";
            }
        }

        private void ApplyResolution(int width, int height)
        {
            var (ok, msg) = DisplayEngine.SetResolution(width, height);
            _lblResStatus.Text = msg;
            _lblResStatus.ForeColor = ok ? Color.FromArgb(22, 163, 74) : Color.FromArgb(220, 38, 38);
            RefreshCurrentResolution();
        }

        private (int Width, int Height)? ParseRes(string input)
        {
            if (string.IsNullOrWhiteSpace(input)) return null;
            var parts = input.ToLower().Split(new[] { 'x', '*', ' ', ',' }, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 2 && int.TryParse(parts[0], out int w) && int.TryParse(parts[1], out int h))
            {
                if (w >= 640 && w <= 7680 && h >= 480 && h <= 4320)
                    return (w, h);
            }
            return null;
        }

        private void LoadConfigToUi()
        {
            _chkAutostart.Checked = _config.Autostart;

            int deskVib = Math.Clamp(_config.Desktop.Vibrance, 0, 100);
            _tbDesktopVib.Value = deskVib;
            _lblDesktopVibVal.Text = $"{deskVib} %";

            RefreshGameTiles();
        }

        private void RefreshGameTiles()
        {
            _lvGames.BeginUpdate();
            _lvGames.Items.Clear();

            var newImageList = new ImageList
            {
                ImageSize = new Size(48, 48),
                ColorDepth = ColorDepth.Depth32Bit
            };

            int index = 0;
            foreach (var kvp in _config.Games)
            {
                string exeKey = kvp.Key.ToLowerInvariant();
                var prof = kvp.Value;

                var iconImg = IconHelper.GetGameIcon(exeKey, prof.Path, 48);
                newImageList.Images.Add(exeKey, iconImg);

                string label = string.IsNullOrEmpty(prof.Name) ? Path.GetFileNameWithoutExtension(exeKey) : prof.Name;
                var item = new ListViewItem(label)
                {
                    ImageIndex = index,
                    Tag = exeKey
                };
                _lvGames.Items.Add(item);
                index++;
            }

            _lvGames.LargeImageList = newImageList;
            _lvGames.EndUpdate();
        }

        private void OnAddFromRunning()
        {
            using var dlg = new ProcessSelectForm();
            if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.SelectedExe))
            {
                string exe = dlg.SelectedExe.ToLowerInvariant();
                var initialProfile = new GameProfile
                {
                    Name = Path.GetFileNameWithoutExtension(exe),
                    Path = dlg.SelectedPath ?? "",
                    VibranceEnabled = true,
                    Vibrance = 50,
                    GammaEnabled = false,
                    Gamma = 1.80,
                    BoostEnabled = false,
                    Boost = 0
                };

                using var configDlg = new GameConfigForm(exe, initialProfile, _vibrance, _gammaEngine);
                if (configDlg.ShowDialog(this) == DialogResult.OK)
                {
                    _config.Games[exe] = configDlg.ResultProfile;
                    ConfigManager.Save(_config);
                    RefreshGameTiles();
                }
            }
        }

        private void OnAddManually()
        {
            using var ofd = new OpenFileDialog
            {
                Title = "Select Game",
                Filter = "Executable Files (*.exe)|*.exe|All Files (*.*)|*.*",
                InitialDirectory = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles)
            };

            if (ofd.ShowDialog(this) == DialogResult.OK)
            {
                string exe = Path.GetFileName(ofd.FileName).ToLowerInvariant();
                var initialProfile = new GameProfile
                {
                    Name = Path.GetFileNameWithoutExtension(exe),
                    Path = ofd.FileName,
                    VibranceEnabled = true,
                    Vibrance = 50,
                    GammaEnabled = false,
                    Gamma = 1.80,
                    BoostEnabled = false,
                    Boost = 0
                };

                using var configDlg = new GameConfigForm(exe, initialProfile, _vibrance, _gammaEngine);
                if (configDlg.ShowDialog(this) == DialogResult.OK)
                {
                    _config.Games[exe] = configDlg.ResultProfile;
                    ConfigManager.Save(_config);
                    RefreshGameTiles();
                }
            }
        }

        private void OnEditSelected()
        {
            if (_lvGames.SelectedItems.Count > 0)
            {
                string? exe = _lvGames.SelectedItems[0].Tag as string;
                if (!string.IsNullOrEmpty(exe) && _config.Games.TryGetValue(exe, out var prof))
                {
                    using var configDlg = new GameConfigForm(exe, prof, _vibrance, _gammaEngine);
                    if (configDlg.ShowDialog(this) == DialogResult.OK)
                    {
                        _config.Games[exe] = configDlg.ResultProfile;
                        ConfigManager.Save(_config);
                        RefreshGameTiles();
                    }
                }
            }
        }

        private void OnRemoveSelected()
        {
            if (_lvGames.SelectedItems.Count > 0)
            {
                string? exe = _lvGames.SelectedItems[0].Tag as string;
                if (!string.IsNullOrEmpty(exe) && _config.Games.ContainsKey(exe))
                {
                    _config.Games.Remove(exe);
                    ConfigManager.Save(_config);
                    RefreshGameTiles();
                }
            }
        }

        private void InitObserver()
        {
            _observer.ForegroundAppChanged += OnForegroundAppChanged;
            _observer.Start();
        }

        private void OnForegroundAppChanged(string? exeName)
        {
            if (this.IsDisposed || !this.IsHandleCreated) return;

            try
            {
                this.BeginInvoke((MethodInvoker)delegate
                {
                    if (this.IsDisposed) return;
                    string? matchedExe = null;
                    if (!string.IsNullOrEmpty(exeName))
                    {
                        foreach (var key in _config.Games.Keys)
                        {
                            if (string.Equals(key, exeName, StringComparison.OrdinalIgnoreCase))
                            {
                                matchedExe = key;
                                break;
                            }
                        }
                    }

                    if (matchedExe != null && matchedExe != _currentGameExe)
                    {
                        ApplyGameProfile(matchedExe, _config.Games[matchedExe]);
                    }
                    else if (matchedExe == null && _currentGameExe != null)
                    {
                        ResetToDesktop();
                    }
                });
            }
            catch { }
        }

        private void ApplyGameProfile(string exe, GameProfile prof)
        {
            _currentGameExe = exe;

            if (prof.VibranceEnabled)
            {
                _vibrance.SetLevel(prof.Vibrance);
            }
            else
            {
                _vibrance.SetLevel(_config.Desktop.Vibrance);
            }

            if (prof.GammaEnabled || prof.BoostEnabled)
            {
                double g = prof.GammaEnabled ? prof.Gamma : 2.20;
                double b = prof.BoostEnabled ? (prof.Boost / 100.0) : 0.0;
                _gammaEngine.ApplyCurve(g, b);
                _gammaEnforceTimer?.Start();
            }
            else
            {
                _gammaEngine.Restore();
                _gammaEnforceTimer?.Stop();
            }

            _lblObserverStatus.Text = $"Running! [{_vibrance.EngineName}]";
            _lblObserverStatus.ForeColor = Color.FromArgb(22, 163, 74);
        }

        private void ResetToDesktop()
        {
            _currentGameExe = null;
            _gammaEnforceTimer?.Stop();
            _vibrance.Restore();
            _gammaEngine.Restore();
            _lblObserverStatus.Text = $"Running! [{_vibrance.EngineName}]";
            _lblObserverStatus.ForeColor = Color.FromArgb(22, 163, 74);
        }

        private void RestoreFromTray()
        {
            this.Show();
            this.WindowState = FormWindowState.Normal;
            this.Activate();
        }

        private void OnFormClosing(object? sender, FormClosingEventArgs e)
        {
            Program.Log($"MainForm.OnFormClosing triggered: CloseReason = {e.CloseReason}, Cancel = {e.Cancel}");
            _gammaEnforceTimer?.Stop();
            _gammaEnforceTimer?.Dispose();
            _notifyIcon.Visible = false;
            _notifyIcon.Dispose();
            _observer.Stop();
            ResetToDesktop();
            _vibrance.Dispose();
            _gammaEngine.Dispose();
        }
    }
}
