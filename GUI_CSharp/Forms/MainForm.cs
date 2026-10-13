using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;
using GUI.Config;
using GUI.Engine;
using GUI.Utils;
using Microsoft.Win32;

namespace GUI.Forms
{
    public class MainForm : Form
    {
        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool ChangeWindowMessageFilter(uint message, uint dwFlag);

        private const uint MSGFLT_ADD = 1;

        private AppConfig _config;
        private UnifiedVibranceEngine _vibrance;
        private GammaEngine _gammaEngine;
        private ActiveWindowObserver _observer;
        private EventWaitHandle? _showWindowEvent;
        private RegisteredWaitHandle? _showWindowWaitHandle;

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
        private bool _isExplicitExit = false;
        private bool _isCleanedUp = false;

        // --- Right Panel: Quick Resolutions ---
        private Label _lblCurrentRes;
        private Button _btnRefreshRes;
        private FlowLayoutPanel _pnlResGrid;
        private List<Button> _resButtons = new();
        private readonly Font _resBtnFontRegular = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point);
        private readonly Font _resBtnFontBold = new Font("Segoe UI", 9F, FontStyle.Bold, GraphicsUnit.Point);
        private Label _lblResStatus;
        private Button _btnOpenUtility;
        private Button _btnOpenMonitors;

        private bool _startMinimized = false;

        public MainForm(bool startMinimized = false)
        {
            _startMinimized = startMinimized;
            _config = ConfigManager.Load();
            _vibrance = new UnifiedVibranceEngine();
            _gammaEngine = new GammaEngine();
            _observer = new ActiveWindowObserver();

            DisplayEngine.CaptureInitialResolution();
            _gammaEngine.ResetToIdentity();

            _gammaEnforceTimer = new System.Windows.Forms.Timer { Interval = 1000 };
            _gammaEnforceTimer.Tick += (s, e) =>
            {
                if (!_isCleanedUp && _currentGameExe != null) _gammaEngine.EnforceIfNeeded();
            };

            this.Icon = IconHelper.GetAppIcon();

            InitializeComponent();
            LoadConfigToUi();
            InitSingleInstanceListener();

            SystemEvents.SessionEnding += OnSessionEnding;

            if (_startMinimized)
            {
                this.WindowState = FormWindowState.Minimized;
                this.ShowInTaskbar = false;
            }

            this.Load += (s, e) =>
            {
                Program.Log("MainForm.Load fired");
                InitObserver();
                RefreshCurrentResolution(rebuildButtons: true);
            };
            this.Shown += (s, e) =>
            {
                Program.Log("MainForm.Shown fired");
                if (_startMinimized)
                {
                    _startMinimized = false;
                    this.Hide();
                }
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
            _btnRefreshRes.Click += (s, e) => RefreshCurrentResolution(rebuildButtons: true);

            gbQuickRes.Controls.Add(_lblCurrentRes);
            gbQuickRes.Controls.Add(_btnRefreshRes);

            // Dynamic Grid of Supported Resolution Buttons
            _pnlResGrid = new FlowLayoutPanel
            {
                Location = new Point(8, 48),
                Size = new Size(328, 344),
                AutoScroll = true,
                WrapContents = true,
                FlowDirection = FlowDirection.LeftToRight,
                Padding = new Padding(0),
                Margin = new Padding(0)
            };
            gbQuickRes.Controls.Add(_pnlResGrid);

            _lblResStatus = new Label
            {
                Text = "Click any resolution button to switch instantly.",
                Location = new Point(10, 398),
                Size = new Size(324, 42),
                Font = new Font("Segoe UI", 8.5F, FontStyle.Regular, GraphicsUnit.Point),
                ForeColor = Color.FromArgb(71, 85, 105)
            };
            gbQuickRes.Controls.Add(_lblResStatus);

            this.Controls.Add(gbQuickRes);

            // ==========================================
            // 3. BOTTOM RIGHT: "Utility" & "Monitors" buttons
            // ==========================================
            _btnOpenUtility = new Button
            {
                Text = "Utility",
                Location = new Point(rightX + colW - 246, 462),
                Size = new Size(120, 28),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point),
                FlatStyle = FlatStyle.System
            };
            _btnOpenUtility.Click += (s, e) =>
            {
                using var dlg = new UtilityForm(_config);
                dlg.ShowDialog(this);
            };
            this.Controls.Add(_btnOpenUtility);

            _btnOpenMonitors = new Button
            {
                Text = "Monitors",
                Location = new Point(rightX + colW - 120, 462),
                Size = new Size(120, 28),
                BackColor = Color.White,
                Font = new Font("Segoe UI", 9F, FontStyle.Regular, GraphicsUnit.Point),
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
            trayMenu.Items.Add("Exit", null, (s, e) =>
            {
                _isExplicitExit = true;
                this.Close();
            });

            _notifyIcon = new NotifyIcon
            {
                Text = "GUI",
                Icon = this.Icon,
                ContextMenuStrip = trayMenu,
                Visible = true
            };
            _notifyIcon.DoubleClick += (s, e) => RestoreFromTray();
            _notifyIcon.MouseClick += (s, e) =>
            {
                if (e.Button == MouseButtons.Left)
                {
                    RestoreFromTray();
                }
            };

            this.FormClosing += OnFormClosing;
            this.FormClosed += (s, e) => CleanupAndRestoreDefaults();
        }

        private void InitSingleInstanceListener()
        {
            try
            {
                if (Program.WM_SHOW_EXISTING_INSTANCE != 0)
                {
                    ChangeWindowMessageFilter(Program.WM_SHOW_EXISTING_INSTANCE, MSGFLT_ADD);
                }

                _showWindowEvent = new EventWaitHandle(false, EventResetMode.AutoReset, Program.ShowWindowEventName);
                _showWindowWaitHandle = ThreadPool.RegisterWaitForSingleObject(
                    _showWindowEvent,
                    (state, timedOut) =>
                    {
                        if (!timedOut && !this.IsDisposed && !_isCleanedUp && this.IsHandleCreated)
                        {
                            try
                            {
                                this.BeginInvoke((MethodInvoker)RestoreFromTray);
                            }
                            catch { }
                        }
                    },
                    null,
                    -1,
                    false);
            }
            catch (Exception ex)
            {
                Program.Log($"InitSingleInstanceListener failed: {ex.Message}");
            }
        }

        private const int WM_SYSCOMMAND = 0x0112;
        private const int SC_CLOSE = 0xF060;

        protected override void WndProc(ref Message m)
        {
            if (Program.WM_SHOW_EXISTING_INSTANCE != 0 && m.Msg == Program.WM_SHOW_EXISTING_INSTANCE)
            {
                RestoreFromTray();
                return;
            }

            if (m.Msg == WM_SYSCOMMAND && ((int)m.WParam & 0xFFF0) == SC_CLOSE && !_isExplicitExit)
            {
                this.Hide();
                return;
            }

            base.WndProc(ref m);
        }

        private void PopulateSupportedResolutions()
        {
            _pnlResGrid.SuspendLayout();
            _pnlResGrid.Controls.Clear();
            foreach (var oldBtn in _resButtons)
            {
                try { oldBtn.Dispose(); } catch { }
            }
            _resButtons.Clear();

            var supported = DisplayEngine.GetSupportedResolutions();
            int btnWidth = supported.Count > 16 ? 149 : 158;
            int btnHeight = supported.Count > 16 ? 36 : 38;

            foreach (var (w, h) in supported)
            {
                int resW = w;
                int resH = h;
                var btn = new Button
                {
                    Text = $"{resW} x {resH}",
                    Size = new Size(btnWidth, btnHeight),
                    Margin = new Padding(2),
                    BackColor = Color.White,
                    FlatStyle = FlatStyle.System,
                    Font = _resBtnFontRegular,
                    Tag = (resW, resH)
                };
                btn.Click += (s, e) => ApplyResolution(resW, resH);
                _resButtons.Add(btn);
                _pnlResGrid.Controls.Add(btn);
            }

            _pnlResGrid.ResumeLayout();
        }

        private void RefreshCurrentResolution(bool rebuildButtons = false)
        {
            if (rebuildButtons || _resButtons.Count == 0)
            {
                PopulateSupportedResolutions();
            }

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
                            btn.Font = _resBtnFontBold;
                            btn.Text = $"✓ {res.Item1} x {res.Item2}";
                        }
                        else
                        {
                            btn.Font = _resBtnFontRegular;
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

            var oldImageList = _lvGames.LargeImageList;
            var newImageList = new ImageList
            {
                ColorDepth = ColorDepth.Depth32Bit,
                ImageSize = new Size(48, 48)
            };

            var items = new List<ListViewItem>();
            int index = 0;
            foreach (var kvp in _config.Games)
            {
                string exeKey = kvp.Key.ToLowerInvariant();
                var prof = kvp.Value;

                using var iconImg = IconHelper.GetGameIcon(exeKey, prof.Path, 48);
                newImageList.Images.Add(exeKey, iconImg);

                string label = string.IsNullOrEmpty(prof.Name) ? Path.GetFileNameWithoutExtension(exeKey) : prof.Name;
                var item = new ListViewItem(label)
                {
                    ImageIndex = index,
                    ImageKey = exeKey,
                    Tag = exeKey
                };
                items.Add(item);
                index++;
            }

            _lvGames.LargeImageList = newImageList;
            _lvGames.Items.AddRange(items.ToArray());
            _lvGames.EndUpdate();

            oldImageList?.Dispose();
        }

        private void OnAddFromRunning()
        {
            using var dlg = new ProcessSelectForm();
            if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.SelectedExe))
            {
                string exe = dlg.SelectedExe.ToLowerInvariant();
                GameProfile initialProfile;
                if (_config.Games.TryGetValue(exe, out var existing))
                {
                    initialProfile = new GameProfile
                    {
                        Name = existing.Name,
                        Path = !string.IsNullOrEmpty(dlg.SelectedPath) ? dlg.SelectedPath : existing.Path,
                        VibranceEnabled = existing.VibranceEnabled,
                        Vibrance = existing.Vibrance,
                        GammaEnabled = existing.GammaEnabled,
                        Gamma = existing.Gamma,
                        BoostEnabled = existing.BoostEnabled,
                        Boost = existing.Boost
                    };
                }
                else
                {
                    initialProfile = new GameProfile
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
                }

                using var configDlg = new GameConfigForm(exe, initialProfile, _vibrance, _gammaEngine);
                if (configDlg.ShowDialog(this) == DialogResult.OK)
                {
                    _config.Games[exe] = configDlg.ResultProfile;
                    ConfigManager.Save(_config);
                    RefreshGameTiles();

                    if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                    {
                        ApplyGameProfile(exe, _config.Games[exe]);
                    }
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
                GameProfile initialProfile;
                if (_config.Games.TryGetValue(exe, out var existing))
                {
                    initialProfile = new GameProfile
                    {
                        Name = existing.Name,
                        Path = ofd.FileName,
                        VibranceEnabled = existing.VibranceEnabled,
                        Vibrance = existing.Vibrance,
                        GammaEnabled = existing.GammaEnabled,
                        Gamma = existing.Gamma,
                        BoostEnabled = existing.BoostEnabled,
                        Boost = existing.Boost
                    };
                }
                else
                {
                    initialProfile = new GameProfile
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
                }

                using var configDlg = new GameConfigForm(exe, initialProfile, _vibrance, _gammaEngine);
                if (configDlg.ShowDialog(this) == DialogResult.OK)
                {
                    _config.Games[exe] = configDlg.ResultProfile;
                    ConfigManager.Save(_config);
                    RefreshGameTiles();

                    if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                    {
                        ApplyGameProfile(exe, _config.Games[exe]);
                    }
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

                        if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                        {
                            ApplyGameProfile(exe, _config.Games[exe]);
                        }
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

                    if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                    {
                        ResetToDesktop();
                    }
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
            if (this.IsDisposed || _isCleanedUp || !this.IsHandleCreated) return;

            try
            {
                this.BeginInvoke((MethodInvoker)delegate
                {
                    if (this.IsDisposed || _isCleanedUp) return;
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

                    if (matchedExe != null && !string.Equals(matchedExe, _currentGameExe, StringComparison.OrdinalIgnoreCase))
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
            if (_isCleanedUp) return;
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
            if (_isCleanedUp) return;
            _currentGameExe = null;
            _gammaEnforceTimer?.Stop();
            _vibrance.Restore();
            _gammaEngine.Restore();
            _lblObserverStatus.Text = $"Running! [{_vibrance.EngineName}]";
            _lblObserverStatus.ForeColor = Color.FromArgb(22, 163, 74);
        }

        public void RestoreFromTray()
        {
            if (this.IsDisposed || _isCleanedUp) return;
            _startMinimized = false;
            if (!this.ShowInTaskbar)
            {
                this.ShowInTaskbar = true;
            }
            this.Show();
            if (this.WindowState == FormWindowState.Minimized)
            {
                this.WindowState = FormWindowState.Normal;
            }
            this.BringToFront();
            this.Activate();
            if (this.IsHandleCreated)
            {
                SetForegroundWindow(this.Handle);
            }
        }

        private void OnSessionEnding(object sender, SessionEndingEventArgs e)
        {
            Program.Log($"SystemEvents.SessionEnding triggered ({e.Reason})");
            CleanupAndRestoreDefaults();
        }

        public void CleanupAndRestoreDefaults()
        {
            if (_isCleanedUp) return;
            _isCleanedUp = true;

            Program.Log("CleanupAndRestoreDefaults: Resetting all applied machine values to default...");

            try { SystemEvents.SessionEnding -= OnSessionEnding; } catch { }
            try { _showWindowWaitHandle?.Unregister(null); } catch { }
            try { _showWindowEvent?.Dispose(); } catch { }

            try { _gammaEnforceTimer?.Stop(); } catch { }
            try { _gammaEnforceTimer?.Dispose(); } catch { }
            try { _observer.Stop(); } catch { }

            try
            {
                if (_notifyIcon != null)
                {
                    _notifyIcon.Visible = false;
                    _notifyIcon.Dispose();
                }
            }
            catch { }

            try { _lvGames?.LargeImageList?.Dispose(); } catch { }
            try { _resBtnFontRegular.Dispose(); } catch { }
            try { _resBtnFontBold.Dispose(); } catch { }

            _currentGameExe = null;

            // 1. Reset Vibrance (NVIDIA DVC / Windows DWM) back to default (50%)
            try { _vibrance.ResetToDefault(); } catch { }
            try { _vibrance.Dispose(); } catch { }

            // 2. Reset Gamma & Black Boost back to linear identity default
            try { _gammaEngine.ResetToIdentity(); } catch { }
            try { _gammaEngine.Dispose(); } catch { }

            // 3. Restore default screen resolution if changed during this session
            try { DisplayEngine.RestoreDefaultResolution(); } catch { }

            // 4. Re-enable any secondary monitors disabled during this session
            try { MonitorEngine.RestoreOnExit(); } catch { }

            // 5. Stop GoodbyeDPI if running
            try { GoodbyeDpiEngine.StopOnExit(); } catch { }
        }

        private void OnFormClosing(object? sender, FormClosingEventArgs e)
        {
            if (!_isExplicitExit && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                this.Hide();
                return;
            }

            Program.Log($"MainForm.OnFormClosing triggered: CloseReason = {e.CloseReason}, Cancel = {e.Cancel}");
            CleanupAndRestoreDefaults();
        }
    }
}
