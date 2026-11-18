using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using GUI.Config;
using GUI.Engine;
using GUI.Engine.Audio;
using GUI.Utils;
using Microsoft.Win32;

namespace GUI.Forms
{
    public class MainForm : Form
    {
        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll", SetLastError = true)]
        private static extern bool ChangeWindowMessageFilter(uint message, uint dwFlag);

        private const uint MSGFLT_ADD = 1;

        private AppConfig _config;
        private UnifiedVibranceEngine _vibrance;
        private GammaEngine _gammaEngine;
        private ActiveWindowObserver _observer;
        private Cs2GsiListener _gsiListener;
        private readonly LookaheadDspEngine _lookaheadDspEngine = new();
        private readonly GameVolumeController _gameVolume = new();
        private bool _dspFaulted;
        private EventWaitHandle? _showWindowEvent;
        private RegisteredWaitHandle? _showWindowWaitHandle;

        // --- Left Panel: Vibrance & Games ---
        private CheckBox _chkAutostart;
        private CheckBox _chkGameMonitorOnly;
        private TrackBar _tbDesktopVib;
        private Label _lblDesktopVibVal;
        private Button _btnAdd;
        private Button _btnAddManual;
        private Button _btnRemove;
        private ListView _lvGames;
        private readonly List<Image> _currentTileIcons = new();
        private Label _lblObserverStatus;
        private NotifyIcon _notifyIcon;
        private System.Windows.Forms.Timer? _gammaEnforceTimer;
        private System.Windows.Forms.Timer? _saveConfigDebounceTimer;
        private readonly object _gamesLock = new();
        // Written on the UI thread only; volatile because the GSI threads read it on every packet
        private volatile string? _currentGameExe = null;
        private int _currentGamePid = 0;

        // Game process whose audio output this app routed to the Virtual Cable (0 = none). Only this PID is ever
        // reset, so profiles without DSP never touch the per-app device choice.
        private int _routedGamePid = 0;
        // Window the observer reported for the running game (UI thread only): tells which monitor it is on
        private IntPtr _currentGameHwnd = IntPtr.Zero;
        // Resets to the desktop the moment the current game's process exits (null: polled by _gammaEnforceTimer)
        private ProcessExitWatcher? _gameExitWatcher;
        // Profile in use, re-applied after a display topology change or a change of the monitor scope
        private GameProfile? _currentGameProfile = null;
        // Debounces WM_DISPLAYCHANGE bursts (HDMI plug/unplug, MUX switch) before re-enumerating displays
        private System.Windows.Forms.Timer? _displayChangeTimer;
        // Keeps the observer status line current; runs only while the window is visible (idle in the tray)
        private System.Windows.Forms.Timer? _statusLabelTimer;
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

        private bool _startMinimized = false;

        public MainForm(bool startMinimized = false)
        {
            _startMinimized = startMinimized;
            _config = ConfigManager.Load();
            _vibrance = new UnifiedVibranceEngine();
            _gammaEngine = new GammaEngine();
            _observer = new ActiveWindowObserver();
            _gsiListener = new Cs2GsiListener(_config.Cs2GsiPort);
            _gsiListener.StateUpdated += OnCs2GsiStateUpdated;
            _gsiListener.Start();

            DisplayEngine.CaptureInitialResolution();
            _gammaEngine.ResetToIdentity();

            _saveConfigDebounceTimer = new System.Windows.Forms.Timer { Interval = 400 };
            _saveConfigDebounceTimer.Tick += (s, e) =>
            {
                _saveConfigDebounceTimer.Stop();
                ConfigManager.Save(_config);
            };

            _displayChangeTimer = new System.Windows.Forms.Timer { Interval = 500 };
            _displayChangeTimer.Tick += (s, e) =>
            {
                _displayChangeTimer.Stop();
                OnDisplayTopologyChanged();
            };

            _gammaEnforceTimer = new System.Windows.Forms.Timer { Interval = 1000 };
            _gammaEnforceTimer.Tick += (s, e) =>
            {
                if (!_isCleanedUp && _currentGameExe != null)
                {
                    // The exit watcher reports the exit itself; polling the process is only the fallback without one
                    if (_gameExitWatcher == null && !IsCurrentGameRunning())
                    {
                        ResetToDesktop();
                        return;
                    }

                    _gammaEngine.EnforceIfNeeded();
                    _gameVolume.Rescan(); // apps that started playing since the game got focus
                }
            };

            _statusLabelTimer = new System.Windows.Forms.Timer { Interval = 1000 };
            _statusLabelTimer.Tick += (s, e) => UpdateObserverStatus();
            this.VisibleChanged += (s, e) =>
            {
                if (_isCleanedUp) return;
                if (this.Visible)
                {
                    UpdateObserverStatus();
                    _statusLabelTimer.Start();
                }
                else
                {
                    _statusLabelTimer.Stop();
                }
            };

            this.Icon = IconHelper.GetAppIcon();

            InitializeComponent();
            LoadConfigToUi();
            InitSingleInstanceListener();

            SystemEvents.SessionEnded += OnSessionEnded;

            if (_startMinimized)
            {
                this.WindowState = FormWindowState.Minimized;
                this.ShowInTaskbar = false;
            }

            this.Load += (s, e) =>
            {
                Program.Log("MainForm.Load fired");
                ClearStaleAudioRoutingAtStartup();
                _lookaheadDspEngine.Faulted += OnDspFaulted;
                InitObserver();
                RefreshCurrentResolution(rebuildButtons: true);
                NotifyConfigRecovery();
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
            this.Text = "GUI 1.1.4 - Vibrance & QuickRes";
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

            _chkGameMonitorOnly = new CheckBox
            {
                Text = "Affect game monitor only",
                Location = new Point(150, 20),
                AutoSize = true
            };
            new ToolTip().SetToolTip(_chkGameMonitorOnly,
                "When checked, game vibrance, gamma / black boost and CS2 black flash only change the monitor the game window is on;\n" +
                "other screens keep the desktop look. (The Windows DWM fallback on Intel / AMD always covers every screen.)");
            _chkGameMonitorOnly.CheckedChanged += (s, e) =>
            {
                if (_config.AffectGameMonitorOnly == _chkGameMonitorOnly.Checked) return;
                _config.AffectGameMonitorOnly = _chkGameMonitorOnly.Checked;
                ConfigManager.Save(_config);
                // Move the running game's effect to the new scope right away
                var prof = _currentGameProfile;
                if (_currentGameExe != null && prof != null) ApplyGameProfile(_currentGameExe, prof);
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
                _saveConfigDebounceTimer?.Stop();
                _saveConfigDebounceTimer?.Start();

                if (_currentGameExe == null)
                {
                    _vibrance.SetLevel(val);
                }
            };
            _tbDesktopVib.MouseUp += (s, e) =>
            {
                _saveConfigDebounceTimer?.Stop();
                ConfigManager.Save(_config);
            };

            gbWinVib.Controls.Add(_tbDesktopVib);
            gbWinVib.Controls.Add(_lblDesktopVibVal);
            gbSettings.Controls.Add(_chkAutostart);
            gbSettings.Controls.Add(_chkGameMonitorOnly);
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
            // 3. BOTTOM RIGHT: "Utility" button
            // ==========================================
            _btnOpenUtility = new Button
            {
                Text = "Utility",
                Location = new Point(rightX + colW - 120, 462),
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

            // ==========================================
            // System Tray
            // ==========================================
            var trayMenu = new ContextMenuStrip();
            trayMenu.Items.Add("Open GUI 1.1.4", null, (s, e) => RestoreFromTray());
            trayMenu.Items.Add(new ToolStripSeparator());
            trayMenu.Items.Add("Exit", null, (s, e) =>
            {
                _isExplicitExit = true;
                this.Close();
            });

            _notifyIcon = new NotifyIcon
            {
                Text = "GUI 1.1.4",
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

        // Tray balloon instead of a dialog: also shows when started minimized by autostart
        private void NotifyConfigRecovery()
        {
            string? text = null;
            if (ConfigManager.IsSaveBlocked)
            {
                text = "Your config.json could not be read or backed up. Defaults are used and changes will NOT be saved this session.";
            }
            else if (ConfigManager.RecoveredBackupPath != null)
            {
                text = $"Your config.json could not be read. Defaults are used; the old file was kept as {Path.GetFileName(ConfigManager.RecoveredBackupPath)}.";
            }
            if (text == null) return;

            try { _notifyIcon.ShowBalloonTip(10000, "GUI: settings reset", text, ToolTipIcon.Warning); } catch { }
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
        private const int WM_DISPLAYCHANGE = 0x007E;

        protected override void WndProc(ref Message m)
        {
            if (Program.WM_SHOW_EXISTING_INSTANCE != 0 && m.Msg == Program.WM_SHOW_EXISTING_INSTANCE)
            {
                RestoreFromTray();
                return;
            }

            if (m.Msg == WM_DISPLAYCHANGE)
            {
                DisplayTopology.Invalidate();
                _displayChangeTimer?.Stop();
                _displayChangeTimer?.Start();
                if (this.Visible && !_startMinimized)
                {
                    try { RefreshCurrentResolution(); } catch { }
                }
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
            _chkGameMonitorOnly.Checked = _config.AffectGameMonitorOnly;

            int deskVib = Math.Clamp(_config.Desktop.Vibrance, 0, 100);
            _tbDesktopVib.Value = deskVib;
            _lblDesktopVibVal.Text = $"{deskVib} %";

            RefreshGameTiles();
        }

        private void RefreshGameTiles(string? selectedExe = null)
        {
            _lvGames.BeginUpdate();
            try
            {
                _lvGames.Items.Clear();

                var newImageList = new ImageList
                {
                    ColorDepth = ColorDepth.Depth32Bit,
                    ImageSize = new Size(48, 48)
                };

                var oldIcons = new List<Image>(_currentTileIcons);
                _currentTileIcons.Clear();

                var items = new List<ListViewItem>();
                int index = 0;
                List<KeyValuePair<string, GameProfile>> gamesSnapshot;
                lock (_gamesLock)
                {
                    gamesSnapshot = new List<KeyValuePair<string, GameProfile>>(_config.Games);
                }

                foreach (var kvp in gamesSnapshot)
                {
                    string exeKey = kvp.Key.ToLowerInvariant();
                    var prof = kvp.Value;

                    Image iconImg;
                    try
                    {
                        iconImg = IconHelper.GetGameIcon(exeKey, prof.Path, 48);
                    }
                    catch
                    {
                        iconImg = IconHelper.CreateBadge(exeKey, 48);
                    }

                    _currentTileIcons.Add(iconImg);
                    newImageList.Images.Add(exeKey, iconImg);

                    string label = string.IsNullOrEmpty(prof.Name) ? Path.GetFileNameWithoutExtension(exeKey) : prof.Name;
                    var item = new ListViewItem(label)
                    {
                        ImageIndex = index,
                        ImageKey = exeKey,
                        Tag = exeKey
                    };

                    if (!string.IsNullOrEmpty(selectedExe) && string.Equals(exeKey, selectedExe, StringComparison.OrdinalIgnoreCase))
                    {
                        item.Selected = true;
                        item.Focused = true;
                    }

                    items.Add(item);
                    index++;
                }

                var oldImageList = _lvGames.LargeImageList;
                _lvGames.LargeImageList = newImageList;
                _lvGames.Items.AddRange(items.ToArray());

                oldImageList?.Dispose();
                foreach (var oldImg in oldIcons)
                {
                    try { oldImg.Dispose(); } catch { }
                }
            }
            catch (Exception ex)
            {
                Program.Log($"RefreshGameTiles failed: {ex}");
            }
            finally
            {
                _lvGames.EndUpdate();
            }
        }

        private void OnAddFromRunning()
        {
            using var dlg = new ProcessSelectForm();
            if (dlg.ShowDialog(this) == DialogResult.OK && !string.IsNullOrEmpty(dlg.SelectedExe))
            {
                string exe = dlg.SelectedExe.ToLowerInvariant();
                GameProfile initialProfile;
                GameProfile? existing = null;
                lock (_gamesLock)
                {
                    _config.Games.TryGetValue(exe, out existing);
                }

                if (existing != null)
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
                        Boost = existing.Boost,
                        Cs2AntiFlashEnabled = existing.Cs2AntiFlashEnabled,
                        Cs2AntiSmokeEnabled = existing.Cs2AntiSmokeEnabled,
                        Cs2AntiFlashMaxDim = existing.Cs2AntiFlashMaxDim
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

                using var configDlg = new GameConfigForm(exe, initialProfile, _vibrance, _gammaEngine, _gsiListener, _config.AffectGameMonitorOnly);
                if (configDlg.ShowDialog(this) == DialogResult.OK)
                {
                    lock (_gamesLock)
                    {
                        _config.Games[exe] = configDlg.ResultProfile;
                    }
                    ConfigManager.Save(_config);
                    RefreshGameTiles(exe);

                    if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                    {
                        ApplyGameProfile(exe, configDlg.ResultProfile);
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
                GameProfile? existing = null;
                lock (_gamesLock)
                {
                    _config.Games.TryGetValue(exe, out existing);
                }

                if (existing != null)
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
                        Boost = existing.Boost,
                        Cs2AntiFlashEnabled = existing.Cs2AntiFlashEnabled,
                        Cs2AntiSmokeEnabled = existing.Cs2AntiSmokeEnabled,
                        Cs2AntiFlashMaxDim = existing.Cs2AntiFlashMaxDim
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

                using var configDlg = new GameConfigForm(exe, initialProfile, _vibrance, _gammaEngine, _gsiListener, _config.AffectGameMonitorOnly);
                if (configDlg.ShowDialog(this) == DialogResult.OK)
                {
                    lock (_gamesLock)
                    {
                        _config.Games[exe] = configDlg.ResultProfile;
                    }
                    ConfigManager.Save(_config);
                    RefreshGameTiles(exe);

                    if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                    {
                        ApplyGameProfile(exe, configDlg.ResultProfile);
                    }
                }
            }
        }

        private void OnEditSelected()
        {
            if (_lvGames.SelectedItems.Count > 0)
            {
                string? exe = _lvGames.SelectedItems[0].Tag as string;
                GameProfile? prof = null;
                if (!string.IsNullOrEmpty(exe))
                {
                    lock (_gamesLock)
                    {
                        _config.Games.TryGetValue(exe, out prof);
                    }
                }

                if (!string.IsNullOrEmpty(exe) && prof != null)
                {
                    using var configDlg = new GameConfigForm(exe, prof, _vibrance, _gammaEngine, _gsiListener, _config.AffectGameMonitorOnly);
                    if (configDlg.ShowDialog(this) == DialogResult.OK)
                    {
                        lock (_gamesLock)
                        {
                            _config.Games[exe] = configDlg.ResultProfile;
                        }
                        ConfigManager.Save(_config);
                        RefreshGameTiles(exe);

                        if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                        {
                            ApplyGameProfile(exe, configDlg.ResultProfile);
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
                if (!string.IsNullOrEmpty(exe))
                {
                    bool removed = false;
                    lock (_gamesLock)
                    {
                        if (_config.Games.ContainsKey(exe))
                        {
                            _config.Games.Remove(exe);
                            removed = true;
                        }
                    }

                    if (removed)
                    {
                        ConfigManager.Save(_config);
                        RefreshGameTiles();

                        if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                        {
                            ResetToDesktop();
                        }
                    }
                }
            }
        }

        private void InitObserver()
        {
            _observer.ForegroundAppChanged += OnForegroundAppChanged;
            _observer.Start();
        }

        private void OnForegroundAppChanged(string? exeName, int pid, IntPtr hwnd)
        {
            if (this.IsDisposed || _isCleanedUp || !this.IsHandleCreated) return;

            try
            {
                this.BeginInvoke((MethodInvoker)delegate
                {
                    if (this.IsDisposed || _isCleanedUp) return;
                    string? matchedExe = null;
                    GameProfile? matchedProf = null;
                    if (!string.IsNullOrEmpty(exeName))
                    {
                        lock (_gamesLock)
                        {
                            if (_config.Games.TryGetValue(exeName, out matchedProf))
                            {
                                matchedExe = exeName;
                            }
                        }
                    }

                    if (matchedExe != null && matchedProf != null)
                    {
                        ApplyGameProfile(matchedExe, matchedProf, pid, hwnd);
                    }
                    else
                    {
                        if (_currentGameExe != null)
                        {
                            ResetToDesktop();
                        }
                        // Routed by an earlier run (crash / game exit) and its profile is gone: give it its sound back
                        ClearStaleAudioRouting(exeName, pid);
                    }
                });
            }
            catch { }
        }

        private void ApplyGameProfile(string exe, GameProfile prof, int pid = 0, IntPtr hwnd = default)
        {
            if (_isCleanedUp) return;
            _currentGameExe = exe;
            _currentGameProfile = prof;
            if (pid > 0)
            {
                _currentGamePid = pid;
            }
            if (hwnd != IntPtr.Zero)
            {
                _currentGameHwnd = hwnd;
            }
            WatchGameProcess(exe, _currentGamePid);

            // Pick the target first (the game's monitor or every monitor) so gamma, black flash (GSI ApplyContrast)
            // and vibrance all land on it
            string? target = EffectTarget(GameWindow());
            _gammaEngine.SetTargetDisplay(target);
            _vibrance.Apply(prof.VibranceEnabled ? prof.Vibrance : _config.Desktop.Vibrance, target);

            double g = prof.GammaEnabled ? prof.Gamma : 2.20;
            double b = prof.BoostEnabled ? (prof.Boost / 100.0) : 0.0;
            if (string.Equals(exe, "cs2.exe", StringComparison.OrdinalIgnoreCase))
            {
                // Re-applied mid-flash (focus / display change): keep the black flash dim instead of resetting it
                // (no flash: contrast is 1.0 and this equals ApplyCurve / Restore)
                _gammaEngine.ApplyCurveKeepingContrast(g, b);
            }
            else if (prof.GammaEnabled || prof.BoostEnabled)
            {
                _gammaEngine.ApplyCurve(g, b);
            }
            else
            {
                _gammaEngine.Restore();
            }

            // Always start watchdog timer while game profile is active to enforce process liveness & gamma
            _gammaEnforceTimer?.Start();

            ApplySoundLock(exe, prof);

            // Counteract DirectX / GPU driver swapchain reset when Alt-Tabbing back into exclusive fullscreen
            ScheduleReapply(exe, prof, 150);
            ScheduleReapply(exe, prof, 350);

            UpdateObserverStatus();
        }

        // Sound Lock: a ceiling on the game's peaks through a short lookahead buffer (game -> Virtual Cable -> limiter
        // -> DAC), so the gain is already down when a gunshot plays, plus the game master volume while it is in front.
        // Nothing else: no boost, no EQ, no loudness.
        //
        // - The limiter and the routing live as long as the game PROCESS (alt-tab only switches the volume), so the
        //   game's audio stream is not moved back and forth on every focus change.
        // - The game volume is only raised once the game's stream is confirmed on the cable. A raised volume without the
        //   ceiling in the path (game set to another device in its own settings, routing lost) would be ear-splitting,
        //   so that case falls back to the desktop volume at once.
        private sealed record LimiterSession(string Exe, int Pid, string CableRenderId, string OutputId);
        private sealed record GameVolumeTarget(float Volume, bool KeepOthers);

        private LimiterSession? _limiterSession;
        private ProcessExitWatcher? _limiterExitWatcher;
        private GameVolumeTarget? _gameVolumeTarget;   // set while the game is in front
        private bool _gameVolumeDirty;
        private bool _bypassRerouteTried;
        private bool _bypassLogged;
        private long _routedAtTick;
        private int _pathCheckCount;
        private System.Windows.Forms.Timer? _audioPathTimer;

        private void ApplySoundLock(string exe, GameProfile prof)
        {
            int pid = _currentGamePid;
            if (!prof.SoundLockEnabled || pid <= 0)
            {
                StopSoundLock(keepRoutingMarker: false);
                ClearStaleAudioRouting(exe, pid);
                return;
            }

            float ceiling = Math.Clamp(prof.SoundLockThreshold > 0 ? prof.SoundLockThreshold : 40, 5, 100) / 100.0f;
            float gameVolume = Math.Clamp(prof.SoundLockGameVolume > 0 ? prof.SoundLockGameVolume : 70, 1, 100) / 100.0f;
            int lookaheadMs = prof.SoundLockLookaheadMs > 0 ? prof.SoundLockLookaheadMs : 5;

            if (!EnsureLimiter(exe, pid, prof.SoundLockOutputDeviceId, ceiling, lookaheadMs))
            {
                // No ceiling possible -> no raised volume either; the game plays as if Sound Lock were off
                StopSoundLock(keepRoutingMarker: false);
                ClearStaleAudioRouting(exe, pid);
                return;
            }

            _gameVolumeTarget = new GameVolumeTarget(gameVolume, prof.SoundLockBalanceExternalApps);
            _gameVolumeDirty = true;
            CheckGameAudioPath();
        }

        private bool EnsureLimiter(string exe, int pid, string? preferredOutputId, float ceiling, int lookaheadMs)
        {
            if (_dspFaulted) return false;

            var (cableRenderId, cableCaptureId) = AudioEndpointHelper.GetVirtualCableEndpoints();
            string? outputId = string.IsNullOrEmpty(cableRenderId) || string.IsNullOrEmpty(cableCaptureId)
                ? null
                : AudioEndpointHelper.ResolveDspOutputDeviceId(preferredOutputId, cableRenderId);
            if (outputId == null)
            {
                Program.Log("[AudioRouting] Virtual Cable or physical output missing -> Sound Lock inactive.");
                return false;
            }

            string? cableIssue = AudioEndpointHelper.GetCableInputIssue(cableRenderId);
            if (cableIssue != null) Program.Log($"[AudioRouting] Warning: {cableIssue}");

            // Pipeline first, routing second: the game must never land in a cable nobody reads.
            // Same devices -> only the ceiling is updated live, no restart.
            if (!_lookaheadDspEngine.Start(cableCaptureId!, outputId, ceiling, lookaheadMs))
            {
                Program.Log("[AudioRouting] Lookahead pipeline could not start -> Sound Lock inactive.");
                return false;
            }
            if (!RouteGameAudioToCable(exe, pid, cableRenderId!))
            {
                Program.Log("[AudioRouting] Routing the game to the Virtual Cable failed -> Sound Lock inactive.");
                _lookaheadDspEngine.Stop();
                return false;
            }

            if (_limiterSession?.Pid != pid)
            {
                // Follows the game while it is in the background too: its exit ends the limiter and the routing
                _limiterExitWatcher?.Dispose();
                _limiterExitWatcher = ProcessExitWatcher.TryStart(pid, exe, () => OnLimitedGameExited(pid));
                _bypassRerouteTried = false;
                _bypassLogged = false;
            }
            _limiterSession = new LimiterSession(exe, pid, cableRenderId!, outputId);

            if (_audioPathTimer == null)
            {
                _audioPathTimer = new System.Windows.Forms.Timer { Interval = 250 };
                _audioPathTimer.Tick += (s, e) => { if (!_isCleanedUp) CheckGameAudioPath(); };
            }
            _audioPathTimer.Start();
            return true;
        }

        // Where is the game's audio stream actually playing? Runs on focus and 4x per second while a limiter is up.
        private void CheckGameAudioPath()
        {
            var lim = _limiterSession;
            if (lim == null)
            {
                _audioPathTimer?.Stop();
                return;
            }

            // Without an exit watcher (process could not be opened) poll the process about once a second
            if (_limiterExitWatcher == null && (++_pathCheckCount % 4) == 0 &&
                !string.Equals(ProcessHelper.GetProcessExeName(lim.Pid), lim.Exe, StringComparison.OrdinalIgnoreCase))
            {
                Program.Log($"[AudioRouting] {lim.Exe} (PID {lim.Pid}) is gone -> Sound Lock off");
                StopSoundLock(keepRoutingMarker: false);
                return;
            }

            var devices = AudioEndpointHelper.GetDevicesPlayingFor(lim.Pid);
            bool onCable = devices.Exists(d => string.Equals(d, lim.CableRenderId, StringComparison.OrdinalIgnoreCase));
            bool elsewhere = devices.Exists(d => !string.Equals(d, lim.CableRenderId, StringComparison.OrdinalIgnoreCase));

            if (onCable)
            {
                _bypassRerouteTried = false;
                _bypassLogged = false;
                var target = _gameVolumeTarget;
                if (target != null && (_gameVolumeDirty || !_gameVolume.IsActive))
                {
                    _gameVolume.Apply(lim.OutputId, target.Volume, target.KeepOthers, lim.Pid);
                    _gameVolumeDirty = false;
                }
            }
            else if (elsewhere)
            {
                // The game plays straight to a device: no ceiling in the path, so no raised volume
                if (_gameVolume.IsActive)
                {
                    _gameVolume.Restore();
                    _gameVolumeDirty = true;
                }
                if (!_bypassLogged)
                {
                    _bypassLogged = true;
                    Program.Log($"[AudioRouting] {lim.Exe} plays outside the limiter ({string.Join(", ", devices)}) -> desktop volume");
                }

                // Right after routing the stream may still be moving; after that, try routing once more (a device
                // picked in the game's own settings wins over this, then it simply stays at desktop volume)
                if (!_bypassRerouteTried && Environment.TickCount64 - _routedAtTick > 1500)
                {
                    _bypassRerouteTried = true;
                    _routedGamePid = 0;
                    RouteGameAudioToCable(lim.Exe, lim.Pid, lim.CableRenderId);
                }
            }
            // Neither: the game has no running stream yet -> nothing to raise
        }

        // Focus left the game but it keeps running: desktop volume, limiter and routing stay
        private void LeaveGameFocus()
        {
            _gameVolumeTarget = null;
            _gameVolume.Restore();
        }

        // Exit watcher of the limited game (thread pool)
        private void OnLimitedGameExited(int pid)
        {
            if (this.IsDisposed || _isCleanedUp || !this.IsHandleCreated) return;
            try
            {
                this.BeginInvoke((MethodInvoker)delegate
                {
                    if (this.IsDisposed || _isCleanedUp || _limiterSession?.Pid != pid) return;
                    Program.Log($"[AudioRouting] Limited game (PID {pid}) exited -> Sound Lock off");
                    StopSoundLock(keepRoutingMarker: false);
                });
            }
            catch { }
        }

        private void StopSoundLock(bool keepRoutingMarker)
        {
            _audioPathTimer?.Stop();
            _limiterExitWatcher?.Dispose();
            _limiterExitWatcher = null;
            _limiterSession = null;
            _gameVolumeTarget = null;
            _lookaheadDspEngine.Stop();
            ClearGameAudioRouting(keepRoutingMarker);
            _gameVolume.Restore();
        }

        // DSP thread: the pipeline died (DAC unplugged, audio service restarted...). Put the game back on its normal
        // output at desktop volume until the game is focused again.
        private void OnDspFaulted()
        {
            if (this.IsDisposed || _isCleanedUp || !this.IsHandleCreated) return;
            try
            {
                this.BeginInvoke((MethodInvoker)delegate
                {
                    if (this.IsDisposed || _isCleanedUp) return;
                    Program.Log("[AudioRouting] Lookahead pipeline failed -> Sound Lock off until the next focus");
                    _dspFaulted = true;
                    StopSoundLock(keepRoutingMarker: false);
                });
            }
            catch { }
        }

        private bool RouteGameAudioToCable(string exe, int pid, string cableRenderId)
        {
            if (_routedGamePid == pid) return true;
            ClearGameAudioRouting(); // another game (or an older instance) was routed

            // Marked BEFORE the call: if this app dies right after, the next run still knows to undo it
            SetAudioRoutedExe(exe);
            if (!AudioPolicyConfig.SetAppOutputEndpoint((uint)pid, cableRenderId))
            {
                // Undo a half-applied call (one of the two roles may have been set)
                if (AudioPolicyConfig.ResetAppOutputEndpoint((uint)pid)) SetAudioRoutedExe(null);
                return false;
            }

            _routedGamePid = pid;
            _routedAtTick = Environment.TickCount64;
            Program.Log($"[AudioRouting] Routed PID {pid} ({exe}) to Virtual Cable: {cableRenderId}");
            return true;
        }

        // Puts the routed game back on the default output. Only possible while that process is alive; if it already
        // exited the marker stays and the reset happens the next time the exe is seen.
        // keepMarker: alt-tab away re-routes on the way back, so skip the two config writes per alt-tab; a marker
        // left set while the routing is already undone only costs one harmless extra reset later.
        private void ClearGameAudioRouting(bool keepMarker = false)
        {
            int pid = _routedGamePid;
            if (pid == 0) return;
            _routedGamePid = 0;

            if (AudioPolicyConfig.ResetAppOutputEndpoint((uint)pid))
            {
                Program.Log($"[AudioRouting] PID {pid} back on the default output");
                if (!keepMarker) SetAudioRoutedExe(null);
            }
            else
            {
                Program.Log($"[AudioRouting] Could not reset PID {pid} (exited?) -> retry when {_config.AudioRoutedExe} runs again");
            }
        }

        // A routing left behind by an earlier run or by a game that exited while routed: reset it through any live
        // instance of that exe
        private void ClearStaleAudioRouting(string? exe, int pid)
        {
            string? routedExe = _config.AudioRoutedExe;
            if (pid <= 0 || pid == _routedGamePid || string.IsNullOrEmpty(routedExe) ||
                !string.Equals(exe, routedExe, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            if (AudioPolicyConfig.ResetAppOutputEndpoint((uint)pid))
            {
                Program.Log($"[AudioRouting] Cleared stale Virtual Cable routing of {routedExe} (PID {pid})");
                SetAudioRoutedExe(null);
            }
        }

        private void ClearStaleAudioRoutingAtStartup()
        {
            string? routedExe = _config.AudioRoutedExe;
            if (string.IsNullOrEmpty(routedExe)) return;

            Process[] procs = Array.Empty<Process>();
            try
            {
                procs = Process.GetProcessesByName(Path.GetFileNameWithoutExtension(routedExe));
                foreach (var p in procs)
                {
                    ClearStaleAudioRouting(routedExe, p.Id);
                    if (_config.AudioRoutedExe == null) break;
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[AudioRouting] Startup cleanup failed: {ex.Message}");
            }
            finally
            {
                foreach (var p in procs) p.Dispose();
            }
        }

        private void SetAudioRoutedExe(string? exe)
        {
            if (string.Equals(_config.AudioRoutedExe, exe, StringComparison.OrdinalIgnoreCase)) return;
            _config.AudioRoutedExe = exe;
            try { ConfigManager.Save(_config); } catch (Exception ex) { Program.Log($"[AudioRouting] Config save failed: {ex.Message}"); }
        }

        private bool IsCurrentGameRunning()
        {
            string? exe = _currentGameExe;
            if (exe == null) return false;

            if (_currentGamePid > 0)
            {
                try
                {
                    using var proc = Process.GetProcessById(_currentGamePid);
                    string expectedName = Path.GetFileNameWithoutExtension(exe);
                    return !proc.HasExited && string.Equals(proc.ProcessName, expectedName, StringComparison.OrdinalIgnoreCase);
                }
                catch
                {
                    return false;
                }
            }

            var procs = Process.GetProcessesByName(Path.GetFileNameWithoutExtension(exe));
            bool running = procs.Length > 0;
            foreach (var p in procs) { try { p.Dispose(); } catch { } }
            return running;
        }

        // One watcher per game process: re-applying the same profile (focus / display change) keeps it
        private void WatchGameProcess(string exe, int pid)
        {
            if (_gameExitWatcher != null && _gameExitWatcher.Pid == pid) return;
            StopWatchingGameProcess();
            if (pid <= 0) return;

            _gameExitWatcher = ProcessExitWatcher.TryStart(pid, exe, () => OnGameProcessExited(pid));
            if (_gameExitWatcher == null)
            {
                Program.Log($"Game exit watcher unavailable for {exe} (PID {pid}) -> polling every second.");
            }
        }

        private void StopWatchingGameProcess()
        {
            _gameExitWatcher?.Dispose();
            _gameExitWatcher = null;
        }

        // Thread pool callback of the exit watcher
        private void OnGameProcessExited(int pid)
        {
            if (this.IsDisposed || _isCleanedUp || !this.IsHandleCreated) return;
            try
            {
                this.BeginInvoke((MethodInvoker)delegate
                {
                    if (this.IsDisposed || _isCleanedUp) return;
                    // Another game (or the desktop) may have taken over meanwhile
                    if (_currentGameExe == null || _currentGamePid != pid) return;
                    Program.Log($"{_currentGameExe} (PID {pid}) exited -> desktop settings.");
                    ResetToDesktop();
                });
            }
            catch { }
        }

        // Monitor the game effects go to: the one `gameWindow` is on with "Affect game monitor only" (primary when the
        // window is unknown), else every monitor (null)
        private string? EffectTarget(IntPtr gameWindow)
        {
            if (!_config.AffectGameMonitorOnly) return null;
            return DisplayTopology.GetMonitorOfWindow(gameWindow) ?? DisplayTopology.GetPrimaryName();
        }

        // Window of the running game: the foreground window when it belongs to the game (it follows a window the game
        // recreated, e.g. after a mode change), else the one the observer last reported
        private IntPtr GameWindow()
        {
            IntPtr fg = GetForegroundWindow();
            if (fg != IntPtr.Zero && _currentGamePid > 0)
            {
                GetWindowThreadProcessId(fg, out uint pid);
                if (pid == (uint)_currentGamePid) _currentGameHwnd = fg;
            }
            return _currentGameHwnd;
        }

        // "CS2 GSI Active" follows the live connection. It used to be set only when a profile was applied, which is
        // usually before CS2 sends its first packet, so it rarely showed. Refreshed by _statusLabelTimer while visible.
        private void UpdateObserverStatus()
        {
            if (this.IsDisposed || _isCleanedUp) return;
            string text = $"Running! [{_vibrance.EngineName}]";
            if (_gsiListener.IsCs2Connected) text += " • CS2 GSI Active";
            if (_lblObserverStatus.Text != text) _lblObserverStatus.Text = text;
            _lblObserverStatus.ForeColor = Color.FromArgb(22, 163, 74);
        }

        private double _lastLoggedFlashedFraction = -1.0;

        private void OnCs2GsiStateUpdated(double flashed, double smoked, bool isPlayerActive, string? rawFlashed)
        {
            if (this.IsDisposed || _isCleanedUp) return;

            // Check if CS2 is active: either tracked as current game, or foreground window belongs to cs2.exe.
            // Runs on the GSI threads: only reads _currentGameExe (owned by the UI thread), never writes it, so a
            // packet racing an Alt-Tab cannot mark CS2 as the current game after ResetToDesktop and dim the desktop.
            bool isCs2Active = string.Equals(_currentGameExe, "cs2.exe", StringComparison.OrdinalIgnoreCase)
                || IsForegroundCs2();

            // Only apply anti-flash if CS2 is focused
            if (!isCs2Active)
            {
                LogGsiGate($"skipped: CS2 not active (current game '{_currentGameExe ?? "none"}', foreground is not cs2.exe)");
                // CRITICAL FAILSAFE: If screen was dimmed while in CS2, but user Alt-Tabbed out or CS2 closed/crashed,
                // restore contrast immediately so desktop / other apps are never stuck dimmed!
                if (_gammaEngine.CurrentContrast < 0.999)
                {
                    _gammaEngine.ApplyContrast(1.0);
                }
                return;
            }

            GameProfile? cs2Prof;
            lock (_gamesLock)
            {
                if (!_config.Games.TryGetValue("cs2.exe", out cs2Prof))
                {
                    cs2Prof = new GameProfile { Cs2AntiFlashEnabled = true, Cs2AntiFlashMaxDim = 100 };
                }
            }

            if (!cs2Prof.Cs2AntiFlashEnabled)
            {
                LogGsiGate("skipped: anti-flash disabled in the cs2.exe profile");
                if (_gammaEngine.CurrentContrast < 0.999)
                {
                    _gammaEngine.ApplyContrast(1.0);
                }
                return;
            }

            // CS2 detected without its profile applied (no saved profile, or GSI ahead of the focus event): with
            // "Affect game monitor only", dim the monitor of the focused CS2 window. An applied profile has set the target.
            if (_config.AffectGameMonitorOnly && _gammaEngine.TargetDisplay == null)
            {
                _gammaEngine.SetTargetDisplay(EffectTarget(GetForegroundWindow()));
            }

            double f = flashed;
            double contrast = (1.0 - f) / (1.0 + f);

            // Apply Max Dimming ratio
            double maxDim = Math.Clamp(cs2Prof.Cs2AntiFlashMaxDim, 20, 100) / 100.0;
            double effectiveContrast = 1.0 - (1.0 - contrast) * maxDim;

            // 1. CRITICAL PATH: Apply contrast immediately to GPU hardware (0.00ms delay!)
            bool applied = _gammaEngine.ApplyContrast(effectiveContrast);
            LogGsiGate("applying");

            // 2. NON-CRITICAL PATH: Log state changes asynchronously (only if debug logging is explicitly enabled)
            bool shouldLog = (f <= 0.01 && _lastLoggedFlashedFraction > 0.01) || Math.Abs(f - _lastLoggedFlashedFraction) > 0.05;
            if (Program.EnableLogging && shouldLog)
            {
                _lastLoggedFlashedFraction = f;
                string path = $"ok={applied}, mode={_gammaEngine.ActiveMode}, target={_gammaEngine.TargetDisplay ?? "all"}";
                ThreadPool.QueueUserWorkItem(_ =>
                {
                    if (f > 0.02)
                    {
                        Program.Log($"[CS2 Flash] Flashed={f * 100:0.0}% (raw='{rawFlashed}') -> Dimming screen (contrast={effectiveContrast:0.00}, {path})");
                    }
                    else
                    {
                        Program.Log($"[CS2 Flash] Flash ended (raw='{rawFlashed}') -> Screen restored ({path}).");
                    }
                });
            }
        }

        // Why GSI packets are (not) acted on, logged when it changes: a skipped packet otherwise leaves no trace
        private string? _lastGsiGate;

        private void LogGsiGate(string gate)
        {
            if (!Program.EnableLogging || gate == _lastGsiGate) return;
            _lastGsiGate = gate;
            Program.Log($"[CS2 GSI] {gate}");
        }

        // Last foreground window checked by IsForegroundCs2, so packets while the same window stays focused skip the
        // process name lookup. Replaced as a whole (immutable), since the GSI apply and watchdog threads both read it.
        private sealed record ForegroundSample(IntPtr Hwnd, uint Pid, bool IsCs2);
        private volatile ForegroundSample? _lastForegroundSample;

        private bool IsForegroundCs2()
        {
            try
            {
                IntPtr fg = GetForegroundWindow();
                if (fg == IntPtr.Zero) return false;
                GetWindowThreadProcessId(fg, out uint pid);
                if (pid == 0) return false;

                var cached = _lastForegroundSample;
                if (cached != null && cached.Hwnd == fg && cached.Pid == pid) return cached.IsCs2;

                bool isCs2 = string.Equals(ProcessHelper.GetProcessExeName((int)pid), "cs2.exe", StringComparison.OrdinalIgnoreCase);
                _lastForegroundSample = new ForegroundSample(fg, pid, isCs2);
                return isCs2;
            }
            catch
            {
                return false;
            }
        }

        private void ScheduleReapply(string exe, GameProfile prof, int delayMs)
        {
            Task.Delay(delayMs).ContinueWith(_ =>
            {
                if (this.IsDisposed || _isCleanedUp || !this.IsHandleCreated) return;
                try
                {
                    this.BeginInvoke((MethodInvoker)delegate
                    {
                        if (this.IsDisposed || _isCleanedUp) return;
                        if (string.Equals(_currentGameExe, exe, StringComparison.OrdinalIgnoreCase))
                        {
                            if (prof.VibranceEnabled)
                            {
                                _vibrance.Apply(prof.Vibrance, _gammaEngine.TargetDisplay);
                            }
                            if (prof.GammaEnabled || prof.BoostEnabled)
                            {
                                _gammaEngine.EnforceIfNeeded();
                            }
                        }
                    });
                }
                catch { }
            });
        }

        private void ResetToDesktop()
        {
            if (_isCleanedUp) return;
            // Alt-tab / another app in front: desktop volume; the limiter keeps running for the game in the background
            // and stops on its own when the game exits
            LeaveGameFocus();
            _dspFaulted = false;
            _currentGameExe = null;
            _currentGamePid = 0;
            _currentGameHwnd = IntPtr.Zero;
            _currentGameProfile = null;
            StopWatchingGameProcess();
            _gammaEnforceTimer?.Stop();
            _vibrance.Restore();
            // Reset every LUT first, then drop the target, so the game curve never spreads to all monitors
            _gammaEngine.Restore();
            _gammaEngine.SetTargetDisplay(null);
            UpdateObserverStatus();
        }

        // HDMI plugged / unplugged, MUX or Advanced Optimus switch, resolution change...
        // NVIDIA handles go stale and a newly attached monitor starts with a default LUT, so re-enumerate and re-apply.
        private void OnDisplayTopologyChanged()
        {
            if (this.IsDisposed || _isCleanedUp) return;
            try
            {
                DisplayTopology.Invalidate();
                _vibrance.RefreshDisplays();

                var prof = _currentGameProfile;
                if (_currentGameExe != null && prof != null)
                {
                    ApplyGameProfile(_currentGameExe, prof);
                }
                else
                {
                    _vibrance.Restore();
                    UpdateObserverStatus();
                }
            }
            catch (Exception ex)
            {
                Program.Log($"OnDisplayTopologyChanged failed: {ex.Message}");
            }
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

        // SessionEnded (WM_ENDSESSION) only fires once logoff/shutdown is final.
        // SessionEnding (WM_QUERYENDSESSION) can still be cancelled, which would leave a dead app holding the mutex.
        private void OnSessionEnded(object sender, SessionEndedEventArgs e)
        {
            Program.Log($"SystemEvents.SessionEnded triggered ({e.Reason})");
            CleanupAndRestoreDefaults();
        }

        public void CleanupAndRestoreDefaults()
        {
            if (_isCleanedUp) return;
            _isCleanedUp = true;

            Program.Log("CleanupAndRestoreDefaults: Resetting all applied machine values to default...");

            try { SystemEvents.SessionEnded -= OnSessionEnded; } catch { }
            try { _showWindowWaitHandle?.Unregister(null); } catch { }
            try { _showWindowEvent?.Dispose(); } catch { }

            Action uiCleanup = () =>
            {
                try { _gammaEnforceTimer?.Stop(); } catch { }
                try { _gammaEnforceTimer?.Dispose(); } catch { }
                try { StopWatchingGameProcess(); } catch { }
                try { StopSoundLock(keepRoutingMarker: false); } catch { }
                try { _lookaheadDspEngine.Dispose(); } catch { }
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
                try
                {
                    foreach (var img in _currentTileIcons)
                    {
                        try { img.Dispose(); } catch { }
                    }
                    _currentTileIcons.Clear();
                }
                catch { }
                try
                {
                    // Flush a debounced Desktop Vibrance change that has not been written yet
                    if (_saveConfigDebounceTimer != null && _saveConfigDebounceTimer.Enabled)
                    {
                        _saveConfigDebounceTimer.Stop();
                        ConfigManager.Save(_config);
                    }
                }
                catch { }
                try { _saveConfigDebounceTimer?.Stop(); } catch { }
                try { _saveConfigDebounceTimer?.Dispose(); } catch { }
                try { _displayChangeTimer?.Stop(); } catch { }
                try { _displayChangeTimer?.Dispose(); } catch { }
                try { _statusLabelTimer?.Stop(); } catch { }
                try { _statusLabelTimer?.Dispose(); } catch { }
                try { _audioPathTimer?.Dispose(); } catch { }
                try
                {
                    _pnlResGrid?.Controls.Clear();
                    _resButtons.Clear();
                }
                catch { }
                try { _resBtnFontRegular?.Dispose(); } catch { }
                try { _resBtnFontBold?.Dispose(); } catch { }
            };

            if (!Environment.HasShutdownStarted && this.IsHandleCreated && !this.IsDisposed)
            {
                if (this.InvokeRequired)
                {
                    try
                    {
                        var asyncResult = this.BeginInvoke(uiCleanup);
                        if (!asyncResult.AsyncWaitHandle.WaitOne(300))
                        {
                            uiCleanup();
                        }
                    }
                    catch
                    {
                        uiCleanup();
                    }
                }
                else
                {
                    uiCleanup();
                }
            }
            else
            {
                uiCleanup();
            }

            _currentGameExe = null;
            _currentGamePid = 0;

            // 1. Reset Vibrance (NVIDIA DVC / Windows DWM) back to default (50%)
            try { _vibrance.ResetToDefault(); } catch { }
            try { _vibrance.Dispose(); } catch { }

            // 2. Reset Gamma & Black Boost back to linear identity default
            try { _gammaEngine.ResetToIdentity(); } catch { }
            try { _gammaEngine.Dispose(); } catch { }
            try { DwmShaderColorEngine.Uninitialize(); } catch { }

            // 3. Restore default screen resolution if changed during this session
            try { DisplayEngine.RestoreDefaultResolution(); } catch { }

            // 4. Stop GoodbyeDPI if running
            try { GoodbyeDpiEngine.StopOnExit(); } catch { }

            // 5. Stop CS2 GSI Listener
            try { _gsiListener.Dispose(); } catch { }
        }

        private void OnFormClosing(object? sender, FormClosingEventArgs e)
        {
            if (!_isExplicitExit && e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                this.Hide();
                return;
            }

            if (e.CloseReason == CloseReason.WindowsShutDown)
            {
                // Raised on WM_QUERYENDSESSION: shutdown may still be cancelled, so keep running.
                // Cleanup happens in FormClosed / SessionEnded once the session really ends.
                Program.Log("MainForm.OnFormClosing: WindowsShutDown query, deferring cleanup until session ends");
                return;
            }

            Program.Log($"MainForm.OnFormClosing triggered: CloseReason = {e.CloseReason}, Cancel = {e.Cancel}");
            CleanupAndRestoreDefaults();
        }
    }
}
