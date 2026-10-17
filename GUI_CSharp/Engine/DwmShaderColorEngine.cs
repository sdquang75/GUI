using System;
using System.Runtime.InteropServices;

namespace GUI.Engine
{
    [StructLayout(LayoutKind.Sequential)]
    public struct MAGCOLOREFFECT
    {
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 25)]
        public float[] transform;
    }

    public static class DwmShaderColorEngine
    {
        [DllImport("Magnification.dll", CallingConvention = CallingConvention.Winapi)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MagInitialize();

        [DllImport("Magnification.dll", CallingConvention = CallingConvention.Winapi)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MagUninitialize();

        [DllImport("Magnification.dll", CallingConvention = CallingConvention.Winapi)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool MagSetFullscreenColorEffect(ref MAGCOLOREFFECT pEffect);

        private static bool _isInitialized = false;
        private static readonly object _lock = new();

        public static int CurrentVibrance { get; private set; } = 50;
        public static double CurrentGamma { get; private set; } = 2.20;
        public static double CurrentBlackBoost { get; private set; } = 0.0;
        public static double CurrentContrast { get; private set; } = 1.0;
        public static bool IsGammaBoostActive { get; private set; } = false;
        public static bool IsVibranceActive { get; private set; } = false;

        public static bool EnsureInitialized()
        {
            lock (_lock)
            {
                if (_isInitialized) return true;
                try
                {
                    if (MagInitialize())
                    {
                        _isInitialized = true;
                        return true;
                    }
                }
                catch (Exception ex)
                {
                    Program.Log($"DwmShaderColorEngine.MagInitialize failed: {ex.Message}");
                }
                return false;
            }
        }

        public static bool SetGammaAndBoost(double gamma, double blackBoost, double contrast = 1.0)
        {
            lock (_lock)
            {
                CurrentGamma = Math.Clamp(gamma, 1.20, 3.00);
                CurrentBlackBoost = Math.Clamp(blackBoost, 0.0, 1.0);
                CurrentContrast = Math.Clamp(contrast, 0.0, 1.0);
                IsGammaBoostActive = (Math.Abs(CurrentGamma - 2.20) > 0.01) || (CurrentBlackBoost > 0.001) || (Math.Abs(CurrentContrast - 1.0) > 0.001);
                return UpdateEffectInternal();
            }
        }

        public static bool ResetGammaAndBoost()
        {
            lock (_lock)
            {
                CurrentGamma = 2.20;
                CurrentBlackBoost = 0.0;
                CurrentContrast = 1.0;
                IsGammaBoostActive = false;
                return UpdateEffectInternal();
            }
        }

        public static bool SetVibrance(int level)
        {
            lock (_lock)
            {
                CurrentVibrance = Math.Clamp(level, 0, 100);
                IsVibranceActive = (CurrentVibrance != 50);
                return UpdateEffectInternal();
            }
        }

        public static bool ResetVibrance()
        {
            lock (_lock)
            {
                CurrentVibrance = 50;
                IsVibranceActive = false;
                return UpdateEffectInternal();
            }
        }

        public static void ResetAll()
        {
            lock (_lock)
            {
                CurrentVibrance = 50;
                CurrentGamma = 2.20;
                CurrentBlackBoost = 0.0;
                CurrentContrast = 1.0;
                IsGammaBoostActive = false;
                IsVibranceActive = false;
                UpdateEffectInternal();
            }
        }

        private static bool UpdateEffectInternal()
        {
            if (!EnsureInitialized()) return false;

            try
            {
                // If neither is active, apply standard identity matrix
                if (!IsGammaBoostActive && !IsVibranceActive)
                {
                    var identity = new MAGCOLOREFFECT
                    {
                        transform = new float[25]
                        {
                            1f, 0f, 0f, 0f, 0f,
                            0f, 1f, 0f, 0f, 0f,
                            0f, 0f, 1f, 0f, 0f,
                            0f, 0f, 0f, 1f, 0f,
                            0f, 0f, 0f, 0f, 1f
                        }
                    };
                    return MagSetFullscreenColorEffect(ref identity);
                }

                // 1. Calculate Gamma & Black Boost coefficients
                float totalOffset = 0f;
                float totalScale = 1f;

                if (IsGammaBoostActive)
                {
                    float boost = (float)CurrentBlackBoost;
                    float gammaGain = (float)(2.20 / CurrentGamma - 1.0);

                    if (gammaGain < 0f && boost < 0.01f)
                    {
                        totalOffset = 0f;
                        totalScale = Math.Clamp(1.0f + gammaGain * 0.40f, 0.5f, 1.0f);
                    }
                    else
                    {
                        float maxGain = Math.Max(0f, gammaGain);
                        totalOffset = Math.Clamp(0.31f * maxGain + 0.30f * boost, 0f, 0.45f);
                        totalScale = Math.Clamp(1.0f - 0.20f * maxGain - 0.35f * boost, 0.2f, 1.2f);
                    }

                    // Modulate scale & offset by CurrentContrast (0.0 to 1.0)
                    totalScale *= (float)CurrentContrast;
                    totalOffset *= (float)CurrentContrast;
                }

                // 2. Calculate Vibrance saturation factor (ITU-R BT.709)
                float s = CurrentVibrance / 50.0f;
                float invS = 1.0f - s;
                const float lr = 0.2126f;
                const float lg = 0.7152f;
                const float lb = 0.0722f;

                float rL = invS * lr;
                float gL = invS * lg;
                float bL = invS * lb;

                var effect = new MAGCOLOREFFECT
                {
                    transform = new float[25]
                    {
                        (rL + s) * totalScale, rL * totalScale,        rL * totalScale,        0f, 0f,
                        gL * totalScale,        (gL + s) * totalScale, gL * totalScale,        0f, 0f,
                        bL * totalScale,        bL * totalScale,        (bL + s) * totalScale, 0f, 0f,
                        0f,                     0f,                     0f,                     1f, 0f,
                        totalOffset,            totalOffset,            totalOffset,            0f, 1f
                    }
                };

                return MagSetFullscreenColorEffect(ref effect);
            }
            catch (Exception ex)
            {
                Program.Log($"DwmShaderColorEngine.UpdateEffectInternal error: {ex.Message}");
                return false;
            }
        }

        public static void Uninitialize()
        {
            lock (_lock)
            {
                if (_isInitialized)
                {
                    try
                    {
                        ResetAll();
                        MagUninitialize();
                    }
                    catch { }
                    _isInitialized = false;
                }
            }
        }
    }
}
