using System;
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
            };

            if (_config.Autostart)
            {
                AutostartHelper.SetAutostart(true);
            }

            // Set initial desktop vibrance
            _vibrance.DesktopLevel = _config.Desktop.Vibrance;
            _vibrance.SetLevel(_config.Desktop.Vibrance);
            Program.Log("MainForm constructor completed");
        }

        private void InitializeComponent()
        {
            this.Text = "GUI";
            this.Size = new Size(380, 530);
            this.FormBorderStyle = FormBorderStyle.FixedSingle;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(240, 240, 240);
            this.Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);

            int pad = 10;
            int clientW = 344;

            // ---------------- 1. Settings Box ----------------
            var gbSettings = new GroupBox
            {
                Text = "Settings",
                Location = new Point(pad, 8),
                Size = new Size(clientW, 118),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
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

            // Sub-box: Windows Vibrance Level
            var gbWinVib = new GroupBox
            {
                Text = "Windows Vibrance Level",
                Location = new Point(10, 46),
                Size = new Size(clientW - 20, 62)
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

            // ---------------- 2. Program Settings Box ----------------
            var gbProg = new GroupBox
            {
                Text = "Program Settings",
                Location = new Point(pad, 132),
                Size = new Size(clientW, 324),
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point)
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
                Size = new Size(clientW - 20, 262),
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

            // ---------------- 3. Bottom Status ----------------
            var lblObserverPrompt = new Label
            {
                Text = "Observer status:",
                Location = new Point(pad, 464),
                AutoSize = true
            };

            _lblObserverStatus = new Label
            {
                Text = $"Running! [{_vibrance.EngineName}]",
                Location = new Point(pad + 95, 464),
                AutoSize = true,
                Font = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(22, 163, 74)
            };

            this.Controls.Add(gbSettings);
            this.Controls.Add(gbProg);
            this.Controls.Add(lblObserverPrompt);
            this.Controls.Add(_lblObserverStatus);

            // System Tray
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

                // Extract or load icon
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

            // 1. Vibrance
            if (prof.VibranceEnabled)
            {
                _vibrance.SetLevel(prof.Vibrance);
            }
            else
            {
                _vibrance.SetLevel(_config.Desktop.Vibrance);
            }

            // 2. Gamma / Black Boost
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
