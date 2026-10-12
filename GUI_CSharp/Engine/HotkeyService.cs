using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace GUI.Engine
{
    public class HotkeyService : IMessageFilter, IDisposable
    {
        private const int WM_HOTKEY = 0x0312;
        private const uint MOD_NOREPEAT = 0x4000;
        private const int HOTKEY_ID = 9001;

        [DllImport("user32.dll")]
        private static extern bool RegisterHotKey(IntPtr hWnd, int id, uint fsModifiers, uint vk);

        [DllImport("user32.dll")]
        private static extern bool UnregisterHotKey(IntPtr hWnd, int id);

        public static readonly Dictionary<string, uint> AvailableKeys = new()
        {
            { "F1", 0x70 }, { "F2", 0x71 }, { "F3", 0x72 }, { "F4", 0x73 },
            { "F5", 0x74 }, { "F6", 0x75 }, { "F7", 0x76 }, { "F8", 0x77 },
            { "F9", 0x78 }, { "F10", 0x79 }, { "F11", 0x7A }, { "F12", 0x7B },
            { "Insert", 0x2D }, { "Delete", 0x2E }, { "Home", 0x24 }, { "End", 0x23 },
            { "PageUp", 0x21 }, { "PageDown", 0x22 }, { "~", 0xC0 }
        };

        private readonly IntPtr _windowHandle;
        private bool _isRegistered = false;
        private bool _isFilterAdded = false;

        public bool IsRunning => _isRegistered;
        public bool IsStretched { get; set; } = false;

        public (int Width, int Height) NativeRes { get; set; } = (1920, 1080);
        public (int Width, int Height) StretchedRes { get; set; } = (1440, 1080);

        public event Action<bool, string>? OnToggled;

        public HotkeyService(IntPtr windowHandle)
        {
            _windowHandle = windowHandle;
        }

        public bool Start(string keyName)
        {
            Stop();

            if (!AvailableKeys.TryGetValue(keyName, out uint vk))
            {
                vk = 0x7A; // F11 default
            }

            if (!_isFilterAdded)
            {
                Application.AddMessageFilter(this);
                _isFilterAdded = true;
            }

            _isRegistered = RegisterHotKey(_windowHandle, HOTKEY_ID, MOD_NOREPEAT, vk);
            return _isRegistered;
        }

        public void Stop()
        {
            if (_isRegistered)
            {
                UnregisterHotKey(_windowHandle, HOTKEY_ID);
                _isRegistered = false;
            }
        }

        public bool PreFilterMessage(ref Message m)
        {
            if (m.Msg == WM_HOTKEY && m.WParam.ToInt32() == HOTKEY_ID)
            {
                Toggle();
                return true;
            }
            return false;
        }

        private void Toggle()
        {
            var target = IsStretched ? NativeRes : StretchedRes;
            var (ok, msg) = DisplayEngine.SetResolution(target.Width, target.Height);
            if (ok)
            {
                IsStretched = !IsStretched;
            }
            OnToggled?.Invoke(IsStretched, msg);
        }

        public void Dispose()
        {
            Stop();
            if (_isFilterAdded)
            {
                Application.RemoveMessageFilter(this);
                _isFilterAdded = false;
            }
        }
    }
}
