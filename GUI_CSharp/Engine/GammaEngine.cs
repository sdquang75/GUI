using System;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    [StructLayout(LayoutKind.Sequential)]
    public unsafe struct RAMP
    {
        public fixed ushort Red[256];
        public fixed ushort Green[256];
        public fixed ushort Blue[256];
    }

    public unsafe class GammaEngine : IDisposable
    {
        [DllImport("user32.dll")]
        private static extern IntPtr GetDC(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern int ReleaseDC(IntPtr hWnd, IntPtr hDC);

        [DllImport("gdi32.dll")]
        private static extern bool GetDeviceGammaRamp(IntPtr hDC, void* lpRamp);

        [DllImport("gdi32.dll")]
        private static extern bool SetDeviceGammaRamp(IntPtr hDC, void* lpRamp);

        private RAMP _lastAppliedRamp;
        private bool _isDisposed = false;
        public bool IsCustomActive { get; private set; }

        public GammaEngine()
        {
        }

        public bool ApplyCurve(double gamma = 1.80, double blackBoost = 0.0)
        {
            if (_isDisposed) return false;

            gamma = Math.Clamp(gamma, 1.20, 3.00);
            blackBoost = Math.Clamp(blackBoost, 0.0, 1.0);
            double baseP = gamma / 2.20;

            RAMP newRamp = new RAMP();
            for (int i = 0; i < 256; i++)
            {
                double x = i / 255.0;
                double yGamma = x > 0 ? Math.Pow(x, baseP) : 0.0;
                double lift = (x > 0 && blackBoost > 0)
                    ? Math.Pow(1.0 - x, 2) * Math.Pow(x, 0.6) * 1.5 * blackBoost
                    : 0.0;

                double y = Math.Clamp(yGamma + lift, 0.0, 1.0);
                ushort val = (ushort)Math.Clamp((int)(y * 65535.0 + 0.5), 0, 65535);

                newRamp.Red[i] = val;
                newRamp.Green[i] = val;
                newRamp.Blue[i] = val;
            }

            _lastAppliedRamp = newRamp;
            IsCustomActive = true;

            IntPtr hdc = GetDC(IntPtr.Zero);
            bool success = false;
            if (hdc != IntPtr.Zero)
            {
                success = SetDeviceGammaRamp(hdc, &newRamp);
                ReleaseDC(IntPtr.Zero, hdc);
            }
            return success;
        }

        public bool EnforceIfNeeded()
        {
            if (_isDisposed || !IsCustomActive) return false;

            IntPtr hdc = GetDC(IntPtr.Zero);
            if (hdc == IntPtr.Zero) return false;

            RAMP currentRamp;
            bool success = false;
            if (GetDeviceGammaRamp(hdc, &currentRamp))
            {
                ushort currentMid = currentRamp.Red[128];
                ushort targetMid = _lastAppliedRamp.Red[128];
                if (Math.Abs(currentMid - targetMid) > 800)
                {
                    fixed (RAMP* pRamp = &_lastAppliedRamp)
                    {
                        success = SetDeviceGammaRamp(hdc, pRamp);
                    }
                }
            }
            ReleaseDC(IntPtr.Zero, hdc);
            return success;
        }

        public void ResetToIdentity()
        {
            IsCustomActive = false;
            RAMP identity = new RAMP();
            for (int i = 0; i < 256; i++)
            {
                ushort val = (ushort)Math.Clamp((int)((i / 255.0) * 65535.0 + 0.5), 0, 65535);
                identity.Red[i] = val;
                identity.Green[i] = val;
                identity.Blue[i] = val;
            }

            IntPtr hdc = GetDC(IntPtr.Zero);
            if (hdc != IntPtr.Zero)
            {
                SetDeviceGammaRamp(hdc, &identity);
                ReleaseDC(IntPtr.Zero, hdc);
            }
        }

        public void SaveCurrentState(out bool wasActive, out RAMP ramp)
        {
            wasActive = IsCustomActive;
            ramp = _lastAppliedRamp;
        }

        public void RestoreState(bool wasActive, in RAMP ramp)
        {
            if (_isDisposed) return;

            if (wasActive)
            {
                _lastAppliedRamp = ramp;
                IsCustomActive = true;
                IntPtr hdc = GetDC(IntPtr.Zero);
                if (hdc != IntPtr.Zero)
                {
                    fixed (RAMP* pRamp = &_lastAppliedRamp)
                    {
                        SetDeviceGammaRamp(hdc, pRamp);
                    }
                    ReleaseDC(IntPtr.Zero, hdc);
                }
            }
            else
            {
                ResetToIdentity();
            }
        }

        public void Restore()
        {
            ResetToIdentity();
        }

        public void Dispose()
        {
            if (_isDisposed) return;
            ResetToIdentity();
            _isDisposed = true;
        }
    }
}
