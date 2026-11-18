using System;

namespace GUI.Engine.Audio
{
    /// <summary>Reverb cut settings of a profile.</summary>
    public readonly record struct TransientShaperSettings(bool Enabled, float AttackDb, float SustainCutDb)
    {
        public static readonly TransientShaperSettings Off = new(false, 0f, 0f);
    }

    /// <summary>
    /// "Reverb cut": shortens the decaying tail of every sound (room reverb, ringing) and can lift its first
    /// milliseconds, so footsteps stay distinct when the game volume is high. Level independent (works on distant,
    /// quiet steps exactly like on close ones), zero added latency (no lookahead, a few operations per sample).
    ///
    /// - Split: low = 2nd-order low-pass at 200 Hz, high = x - low; output x + (g - 1) * high. Bass / explosion
    ///   rumble passes through, and with g = 1 the output is the input bit for bit.
    /// - Detector: |300 Hz high-pass|, stereo linked, then a peak envelope p. On p:
    ///   attack delta  = dB(fast-attack follower) - dB(slow-attack follower)  -> "a sound just started"
    ///   sustain delta = dB(anchor) - dB(current level)                        -> "how far into its decay we are"
    ///   The anchor holds the level of the last onset (slow release) and is re-set by every new onset, so a quiet
    ///   step right after a loud gunshot is measured against itself, not against the gunshot.
    /// - Gain (dB) = +AttackDb * attack weight - SustainCutDb * sustain weight (sustain weight drops to 0 during an
    ///   onset), with a 1 dB dead band so steady sounds are not modulated; below -70 dBFS nothing is changed.
    /// Runs before the limiter, so whatever the attack lift adds, the ceiling still holds.
    /// </summary>
    public sealed class TransientShaper
    {
        private sealed class Params
        {
            public readonly bool Enabled;
            public readonly double AttackDb;
            public readonly double SustainCutDb;

            public Params(bool enabled, double attackDb, double sustainCutDb)
            {
                Enabled = enabled;
                AttackDb = attackDb;
                SustainCutDb = sustainCutDb;
            }
        }

        public const float MaxAttackDb = 6f;
        public const float MaxSustainCutDb = 12f;

        private const double SplitHz = 200.0;
        private const double DetectHz = 300.0;
        private const double DeadBandDb = 1.0;
        private const double AttackRangeDb = 11.0;   // attack delta above the dead band for the full lift
        private const double SustainRangeDb = 8.0;   // decay depth above the dead band for the full cut
        private const double OnsetDb = 6.0;          // attack delta that counts as a new sound
        private static readonly double GateLevel = Math.Pow(10, -70.0 / 20.0);

        private readonly int _channels;
        private readonly double _sampleRate;
        private volatile Params _params = new(false, 0, 0);

        // Biquads (normalized, a0 = 1): split low-pass and detector high-pass, per channel TDF-II state
        private readonly double _lpB0, _lpB1, _lpB2, _lpA1, _lpA2;
        private readonly double _hpB0, _hpB1, _hpB2, _hpA1, _hpA2;
        private readonly double[] _lpState;
        private readonly double[] _hpState;

        // One-pole coefficients (1 - exp(-1 / (t * fs)))
        private readonly double _peakRel;
        private readonly double _fastAtt, _slowAtt, _attRel;
        private readonly double _levelAtt, _levelRel;
        private readonly double _anchorAtt, _anchorRel;
        private readonly double _gainUp, _gainDown;

        private double _p, _aFast, _aSlow, _level, _anchor, _gainDb;
        private bool _wasEnabled;

        public TransientShaperSettings Settings { get; private set; } = TransientShaperSettings.Off;

        /// <summary>Current gain in dB (for tests / diagnostics).</summary>
        public double CurrentGainDb => _gainDb;

        public TransientShaper(int channels, int sampleRate, TransientShaperSettings settings)
        {
            _channels = Math.Max(1, channels);
            _sampleRate = sampleRate;

            (_lpB0, _lpB1, _lpB2, _lpA1, _lpA2) = LowPass(SplitHz, 0.7071);
            (_hpB0, _hpB1, _hpB2, _hpA1, _hpA2) = HighPass(DetectHz, 0.7071);
            _lpState = new double[_channels * 2];
            _hpState = new double[_channels * 2];

            _peakRel = Coef(10.0);
            _fastAtt = Coef(0.5);
            _slowAtt = Coef(20.0);
            _attRel = Coef(30.0);
            _levelAtt = Coef(0.5);
            _levelRel = Coef(20.0);
            _anchorAtt = Coef(0.5);
            _anchorRel = Coef(400.0);
            _gainUp = Coef(0.3);    // back to full level fast when a new sound starts
            _gainDown = Coef(2.0);

            SetSettings(settings);
        }

        public bool IsActive => _params.Enabled;

        /// <summary>Live update; the audio thread picks it up on its next buffer.</summary>
        public void SetSettings(TransientShaperSettings settings)
        {
            settings = settings with
            {
                AttackDb = Math.Clamp(settings.AttackDb, 0f, MaxAttackDb),
                SustainCutDb = Math.Clamp(settings.SustainCutDb, 0f, MaxSustainCutDb)
            };
            Settings = settings;
            _params = new Params(settings.Enabled, settings.AttackDb, settings.SustainCutDb);
        }

        /// <summary>Processes interleaved frames in place (no delay).</summary>
        public void Process(Span<float> interleaved)
        {
            var prm = _params;
            if (!prm.Enabled)
            {
                _wasEnabled = false;
                return;
            }
            if (!_wasEnabled)
            {
                // Fresh start (first buffer or re-enabled): no stale filter / envelope state
                Array.Clear(_lpState);
                Array.Clear(_hpState);
                _p = _aFast = _aSlow = _level = _anchor = _gainDb = 0;
                _wasEnabled = true;
            }

            int ch = _channels;
            int frames = interleaved.Length / ch;
            double attackDb = prm.AttackDb, sustainDb = prm.SustainCutDb;
            double[] lp = _lpState, hp = _hpState;
            Span<double> high = ch <= 16 ? stackalloc double[ch] : new double[ch];

            for (int f = 0; f < frames; f++)
            {
                int o = f * ch;
                double d = 0;
                for (int c = 0; c < ch; c++)
                {
                    double x = interleaved[o + c];
                    int k = c * 2;

                    double low = _lpB0 * x + lp[k];
                    lp[k] = _lpB1 * x - _lpA1 * low + lp[k + 1];
                    lp[k + 1] = _lpB2 * x - _lpA2 * low;
                    high[c] = x - low;

                    double h = _hpB0 * x + hp[k];
                    hp[k] = _hpB1 * x - _hpA1 * h + hp[k + 1];
                    hp[k + 1] = _hpB2 * x - _hpA2 * h;
                    double a = Math.Abs(h);
                    if (a > d) d = a;
                }

                // Peak envelope (instant attack) smooths the waveform; the followers work on it
                _p = d > _p ? d : _p + (d - _p) * _peakRel;
                _aFast += (_p - _aFast) * (_p > _aFast ? _fastAtt : _attRel);
                _aSlow += (_p - _aSlow) * (_p > _aSlow ? _slowAtt : _attRel);
                _level += (_p - _level) * (_p > _level ? _levelAtt : _levelRel);

                double attackDelta = 20 * Math.Log10((_aFast + 1e-12) / (_aSlow + 1e-12));
                // Only while the level is still rising: right after a burst the slow follower lags for ~20 ms and
                // attackDelta stays high, but that is already the tail
                bool rising = _p >= _level;
                if (rising && attackDelta > OnsetDb && _anchor > _level) _anchor = _level; // new sound: measure its own decay
                _anchor += (_p - _anchor) * (_p > _anchor ? _anchorAtt : _anchorRel);

                double target = 0;
                if (_anchor > GateLevel)
                {
                    double onset = rising ? Math.Clamp(attackDelta / OnsetDb, 0, 1) : 0;
                    double aw = Math.Clamp((attackDelta - DeadBandDb) / AttackRangeDb, 0, 1);
                    double sustainDelta = 20 * Math.Log10((_anchor + 1e-12) / (_level + 1e-12));
                    double sw = Math.Clamp((sustainDelta - DeadBandDb) / SustainRangeDb, 0, 1) * (1 - onset);
                    target = attackDb * aw - sustainDb * sw;
                }
                _gainDb += (target - _gainDb) * (target > _gainDb ? _gainUp : _gainDown);

                if (_gainDb != 0)
                {
                    double gm1 = Math.Pow(10, _gainDb / 20) - 1;
                    for (int c = 0; c < ch; c++)
                    {
                        interleaved[o + c] = (float)(interleaved[o + c] + gm1 * high[c]);
                    }
                }
            }

            // Denormal guard: silence decays the state to tiny numbers that are very slow on x86
            for (int i = 0; i < lp.Length; i++)
            {
                if (Math.Abs(lp[i]) < 1e-20) lp[i] = 0;
                if (Math.Abs(hp[i]) < 1e-20) hp[i] = 0;
            }
            if (_p < 1e-20) _p = 0;
            if (_aFast < 1e-20) _aFast = 0;
            if (_aSlow < 1e-20) _aSlow = 0;
            if (_level < 1e-20) _level = 0;
            if (_anchor < 1e-20) _anchor = 0;
            if (Math.Abs(_gainDb) < 1e-9) _gainDb = 0;
        }

        private double Coef(double ms) => 1.0 - Math.Exp(-1.0 / (ms / 1000.0 * _sampleRate));

        // RBJ Audio EQ Cookbook (same as FootstepEq)
        private (double, double, double, double, double) LowPass(double f0, double q)
        {
            double w0 = 2 * Math.PI * f0 / _sampleRate, cos = Math.Cos(w0), alpha = Math.Sin(w0) / (2 * q);
            double a0 = 1 + alpha;
            return ((1 - cos) / 2 / a0, (1 - cos) / a0, (1 - cos) / 2 / a0, -2 * cos / a0, (1 - alpha) / a0);
        }

        private (double, double, double, double, double) HighPass(double f0, double q)
        {
            double w0 = 2 * Math.PI * f0 / _sampleRate, cos = Math.Cos(w0), alpha = Math.Sin(w0) / (2 * q);
            double a0 = 1 + alpha;
            return ((1 + cos) / 2 / a0, -(1 + cos) / a0, (1 + cos) / 2 / a0, -2 * cos / a0, (1 - alpha) / a0);
        }
    }
}
