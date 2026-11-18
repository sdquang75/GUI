using System;

namespace GUI.Engine.Audio
{
    /// <summary>Footstep clarity settings of a profile.</summary>
    public readonly record struct FootstepEqSettings(bool Enabled, float PresenceDb, bool CutRumble)
    {
        public static readonly FootstepEqSettings Off = new(false, 0f, false);
    }

    /// <summary>
    /// Makes footsteps stand out from the background instead of making everything louder. Runs before the limiter, so
    /// the ceiling still holds whatever it boosts. Three fixed filters, coefficients computed for the cable's real
    /// sample rate (any PC):
    ///   - rumble cut: 2nd-order high-pass at 100 Hz (wind, hum, explosion sub-bass: the dense "bed" that makes the
    ///     limiter squash everything),
    ///   - mud cut: -presence/2 dB bell at 300 Hz (where ambience piles up),
    ///   - presence: +presence dB bell at 3 kHz (the tap / scuff of steps, reloads, weapon switches).
    /// No extra latency (IIR), a few multiply-adds per sample. Settings can change live.
    /// </summary>
    public sealed class FootstepEq
    {
        private sealed class Biquad
        {
            public double B0, B1, B2, A1, A2; // normalized (a0 = 1)
        }

        private readonly int _channels;
        private readonly double _sampleRate;
        private volatile Biquad[] _sections = Array.Empty<Biquad>();
        // Transposed direct form II state per section per channel (z1, z2)
        private double[] _state = Array.Empty<double>();
        private int _stateSections;

        public FootstepEqSettings Settings { get; private set; } = FootstepEqSettings.Off;

        public FootstepEq(int channels, int sampleRate, FootstepEqSettings settings)
        {
            _channels = Math.Max(1, channels);
            _sampleRate = sampleRate;
            SetSettings(settings);
        }

        public bool IsActive => _sections.Length > 0;

        /// <summary>Rebuilds the filters; the audio thread picks the new set up on its next buffer.</summary>
        public void SetSettings(FootstepEqSettings settings)
        {
            float presence = Math.Clamp(settings.PresenceDb, 0f, 12f);
            settings = settings with { PresenceDb = presence };
            Settings = settings;

            var list = new System.Collections.Generic.List<Biquad>(3);
            if (settings.Enabled)
            {
                if (settings.CutRumble) list.Add(HighPass(100.0, 0.7071));
                if (presence > 0.05f)
                {
                    list.Add(Peaking(300.0, 1.0, -presence / 2.0));
                    list.Add(Peaking(3000.0, 0.9, presence));
                }
            }
            _sections = list.ToArray();
        }

        /// <summary>Filters interleaved frames in place.</summary>
        public void Process(Span<float> interleaved)
        {
            var sections = _sections;
            int count = sections.Length;
            if (count == 0)
            {
                _stateSections = 0;
                return;
            }
            if (_stateSections != count)
            {
                // Filter set changed: fresh state (a settings change, not something that happens while playing)
                _state = new double[count * _channels * 2];
                _stateSections = count;
            }

            int ch = _channels;
            int frames = interleaved.Length / ch;
            double[] st = _state;
            for (int f = 0; f < frames; f++)
            {
                int o = f * ch;
                for (int c = 0; c < ch; c++)
                {
                    double x = interleaved[o + c];
                    for (int s = 0; s < count; s++)
                    {
                        var q = sections[s];
                        int k = (s * ch + c) * 2;
                        double y = q.B0 * x + st[k];
                        st[k] = q.B1 * x - q.A1 * y + st[k + 1];
                        st[k + 1] = q.B2 * x - q.A2 * y;
                        x = y;
                    }
                    interleaved[o + c] = (float)x;
                }
            }

            // Denormal guard: silence decays the state to tiny numbers that are very slow on x86
            for (int i = 0; i < st.Length; i++)
            {
                if (Math.Abs(st[i]) < 1e-20) st[i] = 0;
            }
        }

        /// <summary>Gain in dB of the current filter set at a frequency (for tests / UI).</summary>
        public double ResponseDb(double freq)
        {
            double w = 2 * Math.PI * freq / _sampleRate;
            double re = 1, im = 0;
            foreach (var q in _sections)
            {
                // H(e^jw) = (b0 + b1 e^-jw + b2 e^-2jw) / (1 + a1 e^-jw + a2 e^-2jw)
                double nr = q.B0 + q.B1 * Math.Cos(w) + q.B2 * Math.Cos(2 * w);
                double ni = -q.B1 * Math.Sin(w) - q.B2 * Math.Sin(2 * w);
                double dr = 1 + q.A1 * Math.Cos(w) + q.A2 * Math.Cos(2 * w);
                double di = -q.A1 * Math.Sin(w) - q.A2 * Math.Sin(2 * w);
                double hr = (nr * dr + ni * di) / (dr * dr + di * di);
                double hi = (ni * dr - nr * di) / (dr * dr + di * di);
                double r2 = re * hr - im * hi;
                im = re * hi + im * hr;
                re = r2;
            }
            return 20 * Math.Log10(Math.Sqrt(re * re + im * im));
        }

        // RBJ Audio EQ Cookbook
        private Biquad HighPass(double f0, double q)
        {
            double w0 = 2 * Math.PI * f0 / _sampleRate, cos = Math.Cos(w0), alpha = Math.Sin(w0) / (2 * q);
            double a0 = 1 + alpha;
            return new Biquad
            {
                B0 = (1 + cos) / 2 / a0,
                B1 = -(1 + cos) / a0,
                B2 = (1 + cos) / 2 / a0,
                A1 = -2 * cos / a0,
                A2 = (1 - alpha) / a0
            };
        }

        private Biquad Peaking(double f0, double q, double gainDb)
        {
            double a = Math.Pow(10, gainDb / 40), w0 = 2 * Math.PI * f0 / _sampleRate;
            double cos = Math.Cos(w0), alpha = Math.Sin(w0) / (2 * q);
            double a0 = 1 + alpha / a;
            return new Biquad
            {
                B0 = (1 + alpha * a) / a0,
                B1 = -2 * cos / a0,
                B2 = (1 - alpha * a) / a0,
                A1 = -2 * cos / a0,
                A2 = (1 - alpha / a) / a0
            };
        }
    }
}
