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

        private IntPtr _hNvApi = IntPtr.Zero;
        private NvAPI_QueryInterfaceDelegate? _queryInterface;
        private NvAPI_SetDVCLevelExDelegate? _setDvcLevelEx;
        private NvAPI_GetDVCInfoExDelegate? _getDvcInfoEx;

        private readonly List<IntPtr> _displayHandles = new();
        public bool IsAvailable { get; private set; }
        public string EngineName => "NVIDIA DVC";
        public int DisplayCount => _displayHandles.Count;
        public int DesktopLevel { get; set; } = 50;

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

                if (_hNvApi == IntPtr.Zero)
                {
                    IsAvailable = false;
                    return;
                }

                IntPtr qiPtr = GetProcAddress(_hNvApi, "nvapi_QueryInterface");
                if (qiPtr == IntPtr.Zero)
                {
                    IsAvailable = false;
                    return;
                }

                _queryInterface = Marshal.GetDelegateForFunctionPointer<NvAPI_QueryInterfaceDelegate>(qiPtr);

                // 0x0150E828: NvAPI_Initialize
                IntPtr initPtr = _queryInterface(0x0150E828);
                if (initPtr == IntPtr.Zero)
                {
                    IsAvailable = false;
                    return;
                }

                var initFn = Marshal.GetDelegateForFunctionPointer<NvAPI_InitializeDelegate>(initPtr);
                if (initFn() != 0)
                {
                    IsAvailable = false;
                    return;
                }

                // 0x9ABDD40D: NvAPI_EnumNvidiaDisplayHandle
                IntPtr enumPtr = _queryInterface(0x9ABDD40D);
                if (enumPtr == IntPtr.Zero)
                {
                    IsAvailable = false;
                    return;
                }

                var enumFn = Marshal.GetDelegateForFunctionPointer<NvAPI_EnumNvidiaDisplayHandleDelegate>(enumPtr);
                int idx = 0;
                while (true)
                {
                    IntPtr handle = IntPtr.Zero;
                    if (enumFn(idx, ref handle) != 0 || handle == IntPtr.Zero)
                    {
                        break;
                    }
                    _displayHandles.Add(handle);
                    idx++;
                }

                if (_displayHandles.Count > 0)
                {
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

                    if (_setDvcLevelEx != null && _getDvcInfoEx != null)
                    {
                        IsAvailable = true;
                    }
                }
            }
            catch
            {
                IsAvailable = false;
            }
        }

        public int GetLevel()
        {
            if (!IsAvailable || _getDvcInfoEx == null || _displayHandles.Count == 0)
                return DesktopLevel;

            var info = new NV_DISPLAY_DVC_INFO_EX
            {
                version = (uint)Marshal.SizeOf<NV_DISPLAY_DVC_INFO_EX>() | (1 << 16)
            };

            if (_getDvcInfoEx(_displayHandles[0], 0, ref info) == 0)
            {
                return info.currentLevel;
            }

            return DesktopLevel;
        }

        public bool SetLevel(int level)
        {
            if (!IsAvailable || _setDvcLevelEx == null || _displayHandles.Count == 0)
                return false;

            level = Math.Clamp(level, 0, 100);
            bool success = true;

            var info = new NV_DISPLAY_DVC_INFO_EX
            {
                version = (uint)Marshal.SizeOf<NV_DISPLAY_DVC_INFO_EX>() | (1 << 16),
                currentLevel = level
            };

            foreach (var h in _displayHandles)
            {
                if (_setDvcLevelEx(h, 0, ref info) != 0)
                {
                    success = false;
                }
            }

            return success;
        }

        public void Restore()
        {
            if (IsAvailable)
            {
                SetLevel(DesktopLevel);
            }
        }

        public void Dispose()
        {
            Restore();
        }
    }
}
