using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    [StructLayout(LayoutKind.Sequential)]
    public struct NV_DISPLAY_DVC_INFO_EX
    {
        public uint version;
        public int currentLevel;
        public int minLevel;
        public int maxLevel;
        public int defaultLevel;
    }

    public class NvApiEngine : IVibranceEngine
    {
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Ansi)]
        private static extern IntPtr LoadLibrary(string lpFileName);

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Ansi)]
        private static extern IntPtr GetProcAddress(IntPtr hModule, string procName);

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate IntPtr NvAPI_QueryInterfaceDelegate(uint id);

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int NvAPI_InitializeDelegate();

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int NvAPI_EnumNvidiaDisplayHandleDelegate(int thisEnum, ref IntPtr pDispHandle);

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int NvAPI_SetDVCLevelExDelegate(IntPtr pDispHandle, int outputId, ref NV_DISPLAY_DVC_INFO_EX pDVCInfo);

        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int NvAPI_GetDVCInfoExDelegate(IntPtr pDispHandle, int outputId, ref NV_DISPLAY_DVC_INFO_EX pDVCInfo);

        // NvAPI_GetAssociatedNvidiaDisplayName(NvDisplayHandle, NvAPI_ShortString = char[64])
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        private delegate int NvAPI_GetAssociatedNvidiaDisplayNameDelegate(IntPtr pDispHandle, IntPtr szDisplayName);

        private IntPtr _hNvApi = IntPtr.Zero;
        private NvAPI_QueryInterfaceDelegate? _queryInterface;
        private NvAPI_EnumNvidiaDisplayHandleDelegate? _enumDisplayHandle;
        private NvAPI_GetAssociatedNvidiaDisplayNameDelegate? _getDisplayName;
        private NvAPI_SetDVCLevelExDelegate? _setDvcLevelEx;
        private NvAPI_GetDVCInfoExDelegate? _getDvcInfoEx;

        public const int DefaultVibranceLevel = 50;
        private bool _isDisposed = false;
        private bool _apiReady = false;
        private readonly object _lock = new();
        private readonly List<IntPtr> _displayHandles = new();
        // GDI device name ("\\.\DISPLAY2") -> NVIDIA display handle; only outputs wired to the NVIDIA GPU appear here
        private readonly Dictionary<string, IntPtr> _handlesByName = new(StringComparer.OrdinalIgnoreCase);

        public bool IsAvailable => !_isDisposed && _apiReady && _displayHandles.Count > 0;
        public string EngineName => "NVIDIA DVC";
        public int DisplayCount => _displayHandles.Count;
        // True when every NVIDIA handle was mapped to its GDI name, so per-monitor routing is reliable
        public bool HasDisplayNames => _displayHandles.Count > 0 && _handlesByName.Count == _displayHandles.Count;
        public int DesktopLevel { get; set; } = DefaultVibranceLevel;

        public NvApiEngine()
        {
            Initialize();
        }

        private void Initialize()
        {
            try
            {
                _hNvApi = LoadLibrary("nvapi64.dll");
                if (_hNvApi == IntPtr.Zero)
                {
                    _hNvApi = LoadLibrary("nvapi.dll");
                }

                if (_hNvApi == IntPtr.Zero) return;

                IntPtr qiPtr = GetProcAddress(_hNvApi, "nvapi_QueryInterface");
                if (qiPtr == IntPtr.Zero) return;

                _queryInterface = Marshal.GetDelegateForFunctionPointer<NvAPI_QueryInterfaceDelegate>(qiPtr);

                // 0x0150E828: NvAPI_Initialize
                IntPtr initPtr = _queryInterface(0x0150E828);
                if (initPtr == IntPtr.Zero) return;

                var initFn = Marshal.GetDelegateForFunctionPointer<NvAPI_InitializeDelegate>(initPtr);
                if (initFn() != 0) return;

                // 0x9ABDD40D: NvAPI_EnumNvidiaDisplayHandle
                IntPtr enumPtr = _queryInterface(0x9ABDD40D);
                if (enumPtr == IntPtr.Zero) return;
                _enumDisplayHandle = Marshal.GetDelegateForFunctionPointer<NvAPI_EnumNvidiaDisplayHandleDelegate>(enumPtr);

                // 0x4A82C2B1: NvAPI_SetDVCLevelEx
                IntPtr setExPtr = _queryInterface(0x4A82C2B1);
                if (setExPtr != IntPtr.Zero)
                {
                    _setDvcLevelEx = Marshal.GetDelegateForFunctionPointer<NvAPI_SetDVCLevelExDelegate>(setExPtr);
                }

                // 0x0E45002D: NvAPI_GetDVCInfoEx
                IntPtr getExPtr = _queryInterface(0x0E45002D);
                if (getExPtr != IntPtr.Zero)
                {
                    _getDvcInfoEx = Marshal.GetDelegateForFunctionPointer<NvAPI_GetDVCInfoExDelegate>(getExPtr);
                }

                // 0x22A78B05: NvAPI_GetAssociatedNvidiaDisplayName (optional; without it vibrance applies to every NVIDIA output)
                IntPtr namePtr = _queryInterface(0x22A78B05);
                if (namePtr != IntPtr.Zero)
                {
                    _getDisplayName = Marshal.GetDelegateForFunctionPointer<NvAPI_GetAssociatedNvidiaDisplayNameDelegate>(namePtr);
                }

                _apiReady = _setDvcLevelEx != null && _getDvcInfoEx != null;
                RefreshDisplays();
            }
            catch
            {
                _apiReady = false;
            }
        }

        // Re-enumerates NVIDIA outputs. Handles become invalid after a topology change (HDMI plug/unplug on a laptop,
        // MUX / Advanced Optimus switch), so this must run on WM_DISPLAYCHANGE.
        public void RefreshDisplays()
        {
            lock (_lock)
            {
                if (_isDisposed || _enumDisplayHandle == null) return;

                _displayHandles.Clear();
                _handlesByName.Clear();
                IntPtr nameBuf = Marshal.AllocHGlobal(64);
                try
                {
                    for (int idx = 0; ; idx++)
                    {
                        IntPtr handle = IntPtr.Zero;
                        if (_enumDisplayHandle(idx, ref handle) != 0 || handle == IntPtr.Zero) break;
                        _displayHandles.Add(handle);

                        if (_getDisplayName != null)
                        {
                            for (int i = 0; i < 64; i++) Marshal.WriteByte(nameBuf, i, 0);
                            if (_getDisplayName(handle, nameBuf) == 0)
                            {
                                string? name = Marshal.PtrToStringAnsi(nameBuf);
                                if (!string.IsNullOrEmpty(name)) _handlesByName[name] = handle;
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    Program.Log($"NvApiEngine.RefreshDisplays failed: {ex.Message}");
                }
                finally
                {
                    Marshal.FreeHGlobal(nameBuf);
                }

                Program.Log($"NvApiEngine: {_displayHandles.Count} NVIDIA output(s) [{string.Join(", ", _handlesByName.Keys)}]");
            }
        }

        public bool OwnsDisplay(string gdiName)
        {
            lock (_lock)
            {
                return _handlesByName.ContainsKey(gdiName);
            }
        }

        // Sets one NVIDIA output; returns false when the display is not driven by the NVIDIA GPU
        public bool SetLevelFor(string gdiName, int level)
        {
            lock (_lock)
            {
                if (!IsAvailable || !_handlesByName.TryGetValue(gdiName, out var h)) return false;
                return SetHandleLevel(h, Math.Clamp(level, 0, 100));
            }
        }

        private bool SetHandleLevel(IntPtr h, int level)
        {
            var info = new NV_DISPLAY_DVC_INFO_EX
            {
                version = StructVersion
            };

            if (_getDvcInfoEx != null && _getDvcInfoEx(h, 0, ref info) == 0)
            {
                info.currentLevel = MapPercentToHardware(level, info);
            }
            else
            {
                info.currentLevel = level;
            }

            return _setDvcLevelEx!(h, 0, ref info) == 0;
        }

        private static uint StructVersion => (uint)Marshal.SizeOf<NV_DISPLAY_DVC_INFO_EX>() | (1 << 16);

        private static int MapPercentToHardware(int percent, NV_DISPLAY_DVC_INFO_EX info)
        {
            percent = Math.Clamp(percent, 0, 100);
            if (info.maxLevel <= info.minLevel)
                return percent;

            if (percent == 50)
                return info.defaultLevel;

            if (percent < 50)
            {
                if (info.defaultLevel > info.minLevel)
                {
                    int mapped = info.minLevel + (int)Math.Round((percent / 50.0) * (info.defaultLevel - info.minLevel));
                    return Math.Clamp(mapped, info.minLevel, info.defaultLevel);
                }
                return info.defaultLevel;
            }
            else
            {
                if (info.maxLevel > info.defaultLevel)
                {
                    int mapped = info.defaultLevel + (int)Math.Round(((percent - 50) / 50.0) * (info.maxLevel - info.defaultLevel));
                    return Math.Clamp(mapped, info.defaultLevel, info.maxLevel);
                }
                return info.maxLevel;
            }
        }

        private static int MapHardwareToPercent(NV_DISPLAY_DVC_INFO_EX info)
        {
            if (info.maxLevel <= info.minLevel)
                return Math.Clamp(info.currentLevel, 0, 100);

            if (info.currentLevel == info.defaultLevel)
                return 50;

            if (info.currentLevel > info.defaultLevel && info.maxLevel > info.defaultLevel)
            {
                int pct = 50 + (int)Math.Round(((double)(info.currentLevel - info.defaultLevel) / (info.maxLevel - info.defaultLevel)) * 50.0);
                return Math.Clamp(pct, 50, 100);
            }

            if (info.currentLevel < info.defaultLevel && info.defaultLevel > info.minLevel)
            {
                int pct = (int)Math.Round(((double)(info.currentLevel - info.minLevel) / (info.defaultLevel - info.minLevel)) * 50.0);
                return Math.Clamp(pct, 0, 50);
            }

            return 50;
        }

        public int GetLevel()
        {
            lock (_lock)
            {
                if (!IsAvailable || _getDvcInfoEx == null)
                    return DesktopLevel;

                var info = new NV_DISPLAY_DVC_INFO_EX
                {
                    version = StructVersion
                };

                if (_getDvcInfoEx(_displayHandles[0], 0, ref info) == 0)
                {
                    return MapHardwareToPercent(info);
                }

                return DesktopLevel;
            }
        }

        public bool SetLevel(int level)
        {
            lock (_lock)
            {
                if (!IsAvailable || _setDvcLevelEx == null)
                    return false;

                level = Math.Clamp(level, 0, 100);
                bool success = true;

                foreach (var h in _displayHandles)
                {
                    if (!SetHandleLevel(h, level))
                    {
                        success = false;
                    }
                }

                return success;
            }
        }

        public void Restore()
        {
            if (IsAvailable)
            {
                SetLevel(DesktopLevel);
            }
        }

        public void ResetToDefault()
        {
            lock (_lock)
            {
                if (!IsAvailable || _setDvcLevelEx == null)
                    return;

                foreach (var h in _displayHandles)
                {
                    var info = new NV_DISPLAY_DVC_INFO_EX
                    {
                        version = StructVersion
                    };

                    if (_getDvcInfoEx != null && _getDvcInfoEx(h, 0, ref info) == 0)
                    {
                        info.currentLevel = info.defaultLevel;
                    }
                    else
                    {
                        info.currentLevel = DefaultVibranceLevel;
                    }

                    _setDvcLevelEx(h, 0, ref info);
                }
            }
        }

        public void Dispose()
        {
            if (_isDisposed) return;
            ResetToDefault();
            _isDisposed = true;
            // nvapi64.dll is intentionally NOT freed: it is loaded once per process, NVAPI may still own
            // worker threads (no NvAPI_Unload here), and the cached function pointers would dangle.
            // The OS releases the module when the process exits.
        }
    }
}
