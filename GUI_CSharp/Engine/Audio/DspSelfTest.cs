using System;
using System.Diagnostics;
using System.Text;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// Offline checks of the reverb cut / limiter chain on synthetic signals ("GUI.exe --dsp-selftest [outDir]").
    /// Pure math in memory: no audio device is opened and nothing is played.
    /// </summary>
    public static class DspSelfTest
    {
        private const int Rate = 48000;
        private const int Ch = 2;
        private const int Block = 480; // 10 ms, like the capture packets

        public static (bool Passed, string Report) Run()
        {
            var sb = new StringBuilder();
            bool allOk = true;
            void Check(string name, bool ok, string detail)
            {
                allOk &= ok;
                sb.AppendLine($"[{(ok ? "PASS" : "FAIL")}] {name}: {detail}");
            }

            var cut6 = new TransientShaperSettings(true, 0f, 6f);

            // 1. Neutral settings and disabled -> bit-exact
            {
                float[] x = BurstWithTail(0.5, 3000, 0.15, 0.6, seed: 1);
                AddNoise(x, 0.01, 7);
                float[] a = Process(x, new TransientShaperSettings(true, 0f, 0f));
                float[] b = Process(x, TransientShaperSettings.Off);
                Check("neutral (0 dB / 0 dB) is bit-exact", Same(x, a), "");
                Check("disabled is bit-exact", Same(x, b), "");
            }

            // 2. Burst + decaying tail: onset kept, tail cut; same at -6 dBFS and -46 dBFS
            double redLoud = 0, redQuiet = 0;
            foreach (double amp in new[] { 0.5, 0.005 })
            {
                float[] x = BurstWithTail(amp, 3000, 0.15, 0.6, seed: 0);
                float[] y = Process(x, cut6);
                double onset = PeakDb(y, 0.100, 0.105) - PeakDb(x, 0.100, 0.105);
                double tail = RmsDb(x, 0.150, 0.500) - RmsDb(y, 0.150, 0.500);
                string lvl = $"{20 * Math.Log10(amp):0} dBFS";
                Check($"onset kept @ {lvl}", Math.Abs(onset) <= 1.0, $"{onset:+0.00;-0.00} dB");
                Check($"tail cut (setting 6 dB) @ {lvl}", tail >= 4.0 && tail <= 8.0, $"{tail:0.00} dB less (50-400 ms after onset)");
                if (amp > 0.1) redLoud = tail; else redQuiet = tail;
            }
            Check("level independent", Math.Abs(redLoud - redQuiet) <= 1.0, $"loud {redLoud:0.00} dB vs quiet {redQuiet:0.00} dB");

            // 3. Steady tone: no pumping
            {
                float[] x = Tone(0.3, 3000, 1.0);
                var shaper = new TransientShaper(Ch, Rate, cut6);
                double minG = 0, maxG = 0;
                float[] y = (float[])x.Clone();
                for (int off = 0; off < y.Length; off += Block * Ch)
                {
                    int n = Math.Min(Block * Ch, y.Length - off);
                    shaper.Process(y.AsSpan(off, n));
                    if (off >= (int)(0.3 * Rate) * Ch)
                    {
                        minG = Math.Min(minG, shaper.CurrentGainDb);
                        maxG = Math.Max(maxG, shaper.CurrentGainDb);
                    }
                }
                double diff = RmsDb(y, 0.3, 1.0) - RmsDb(x, 0.3, 1.0);
                Check("steady 3 kHz tone untouched", Math.Abs(diff) <= 0.3, $"{diff:+0.00;-0.00} dB, gain {minG:0.00}..{maxG:0.00} dB");
            }

            // 4. Bass burst + tail (explosion rumble): passes through
            {
                float[] x = BurstWithTail(0.5, 60, 0.15, 0.6, seed: 0, burstMs: 30);
                float[] y = Process(x, cut6);
                double tail = RmsDb(x, 0.150, 0.500) - RmsDb(y, 0.150, 0.500);
                Check("60 Hz tail kept", Math.Abs(tail) <= 1.0, $"{tail:0.00} dB less");
            }

            // 5. Quiet step right after a loud shot: its onset must not be cut
            {
                float[] loud = BurstWithTail(0.8, 3000, 0.05, 0.3, seed: 0);
                float[] quiet = BurstWithTail(0.02, 2000, 0.15, 0.3, seed: 0, startSec: 0.400);
                float[] x = new float[Math.Max(loud.Length, quiet.Length)];
                for (int i = 0; i < x.Length; i++) x[i] = (i < loud.Length ? loud[i] : 0) + (i < quiet.Length ? quiet[i] : 0);
                float[] y = Process(x, cut6);
                double onset = PeakDb(y, 0.400, 0.405) - PeakDb(x, 0.400, 0.405);
                Check("quiet onset after loud shot kept", onset >= -1.5, $"{onset:+0.00;-0.00} dB");
            }

            // 6. Onset lift + limiter: ceiling still holds
            {
                float[] x = BurstWithTail(0.9, 3000, 0.15, 0.6, seed: 0);
                var shaper = new TransientShaper(Ch, Rate, new TransientShaperSettings(true, 6f, 6f));
                var limiter = new LookaheadLimiter(Ch, Rate, 5f, 0.4f, 5f, 12f);
                float[] y = (float[])x.Clone();
                for (int off = 0; off < y.Length; off += Block * Ch)
                {
                    int n = Math.Min(Block * Ch, y.Length - off);
                    shaper.Process(y.AsSpan(off, n));
                    limiter.Process(y.AsSpan(off, n));
                }
                double peak = 0;
                foreach (float v in y) peak = Math.Max(peak, Math.Abs(v));
                Check("onset lift +6 dB stays under 40 % ceiling", peak <= 0.4 + 1e-6, $"peak {peak:0.0000}");
            }

            // 7. Cost: 10 s of stereo audio
            {
                float[] x = Tone(0.3, 1000, 10.0);
                AddNoise(x, 0.05, 3);
                var shaper = new TransientShaper(Ch, Rate, cut6);
                var sw = Stopwatch.StartNew();
                for (int off = 0; off < x.Length; off += Block * Ch)
                {
                    shaper.Process(x.AsSpan(off, Math.Min(Block * Ch, x.Length - off)));
                }
                sw.Stop();
                sb.AppendLine($"[INFO] 10 s stereo 48 kHz processed in {sw.Elapsed.TotalMilliseconds:0.0} ms ({sw.Elapsed.TotalMilliseconds / 10000 * 100:0.00} % of real time), added latency 0 frames");
            }

            sb.Insert(0, $"DSP self-test: {(allOk ? "ALL PASSED" : "FAILURES")}{Environment.NewLine}");
            return (allOk, sb.ToString());
        }

        private static float[] Process(float[] input, TransientShaperSettings settings)
        {
            var shaper = new TransientShaper(Ch, Rate, settings);
            float[] y = (float[])input.Clone();
            for (int off = 0; off < y.Length; off += Block * Ch)
            {
                shaper.Process(y.AsSpan(off, Math.Min(Block * Ch, y.Length - off)));
            }
            return y;
        }

        // Silence until start, a full-level burst, then a tail starting 6 dB lower and decaying with time constant tau
        private static float[] BurstWithTail(double amp, double freq, double tau, double tailSec, int seed,
            double startSec = 0.100, int burstMs = 5)
        {
            int start = (int)(startSec * Rate);
            int burst = Rate * burstMs / 1000;
            int tail = (int)(tailSec * Rate);
            var x = new float[(start + burst + tail + Rate / 10) * Ch];
            for (int i = 0; i < burst + tail; i++)
            {
                double env = i < burst ? 1.0 : 0.5 * Math.Exp(-(i - burst) / (tau * Rate));
                double s = amp * env * Math.Sin(2 * Math.PI * freq * i / Rate + seed);
                for (int c = 0; c < Ch; c++) x[(start + i) * Ch + c] = (float)s;
            }
            return x;
        }

        private static float[] Tone(double amp, double freq, double sec)
        {
            int n = (int)(sec * Rate);
            var x = new float[n * Ch];
            for (int i = 0; i < n; i++)
            {
                float s = (float)(amp * Math.Sin(2 * Math.PI * freq * i / Rate));
                for (int c = 0; c < Ch; c++) x[i * Ch + c] = s;
            }
            return x;
        }

        private static void AddNoise(float[] x, double amp, int seed)
        {
            var rnd = new Random(seed);
            for (int i = 0; i < x.Length; i++) x[i] += (float)(amp * (rnd.NextDouble() * 2 - 1));
        }

        private static bool Same(float[] a, float[] b)
        {
            for (int i = 0; i < a.Length; i++)
            {
                if (BitConverter.SingleToInt32Bits(a[i]) != BitConverter.SingleToInt32Bits(b[i])) return false;
            }
            return true;
        }

        private static double PeakDb(float[] x, double fromSec, double toSec)
        {
            double p = 1e-12;
            for (int i = (int)(fromSec * Rate) * Ch; i < Math.Min(x.Length, (int)(toSec * Rate) * Ch); i++) p = Math.Max(p, Math.Abs(x[i]));
            return 20 * Math.Log10(p);
        }

        private static double RmsDb(float[] x, double fromSec, double toSec)
        {
            double s = 0;
            int a = (int)(fromSec * Rate) * Ch, b = Math.Min(x.Length, (int)(toSec * Rate) * Ch);
            for (int i = a; i < b; i++) s += (double)x[i] * x[i];
            return 10 * Math.Log10(s / Math.Max(1, b - a) + 1e-24);
        }
    }
}
