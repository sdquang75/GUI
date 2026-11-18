using System;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// Transparent peak limiter with lookahead. Nothing below the ceiling is touched (no boost, no EQ, no loudness):
    /// the signal only goes through a short delay. When a peak above the ceiling is coming, the gain is lowered
    /// smoothly over the lookahead window so it is already low when the peak plays (no "pop then duck"), then released
    /// exponentially. Gain is linked across channels so the stereo image does not shift.
    ///
    /// Per frame: required gain r = ceiling / peak (or 1) -> minimum of r over the last L+H frames -> moving average
    /// over L frames (a linear ramp down, ending exactly at r when the peak is output) -> release smoothing.
    /// The audio is delayed L-1 frames, which makes gain(peak) <= r, i.e. the output never exceeds the ceiling.
    /// The hold H keeps the gain down for H after a peak: it spans half a cycle of deep bass, so a very short release
    /// can be used without the gain following the waveform (which is what sounds distorted / "muddy").
    /// </summary>
    public sealed class LookaheadLimiter
    {
        private readonly int _channels;
        private readonly int _lookahead;           // L frames
        private readonly int _window;              // L + H frames (min filter)
        private readonly float[] _delay;           // L frames x channels (ring)
        private int _delayPos;

        // Sliding-window minimum of the required gain (monotonic deque in a ring)
        private readonly float[] _minVal;
        private readonly long[] _minIdx;
        private int _minHead, _minCount;

        // Moving average of the windowed minimum
        private readonly float[] _avgRing;
        private int _avgPos;
        private double _avgSum;

        private long _frame;
        private float _env = 1f;
        private volatile float _ceiling;
        private volatile float _releaseCoef;
        private readonly int _sampleRate;

        public int Channels => _channels;
        public int LatencyFrames => _lookahead - 1;
        public float CurrentGain => _env;

        public LookaheadLimiter(int channels, int sampleRate, float lookaheadMs, float ceiling, float releaseMs, float holdMs = 0f)
        {
            if (channels < 1) throw new ArgumentOutOfRangeException(nameof(channels));
            _channels = channels;
            _sampleRate = sampleRate;
            _lookahead = Math.Max(1, (int)Math.Round(sampleRate * lookaheadMs / 1000.0));
            _window = _lookahead + Math.Max(0, (int)Math.Round(sampleRate * holdMs / 1000.0));
            _delay = new float[_lookahead * channels];
            _minVal = new float[_window];
            _minIdx = new long[_window];
            _avgRing = new float[_lookahead];
            Array.Fill(_avgRing, 1f);
            _avgSum = _lookahead;
            SetParameters(ceiling, releaseMs);
        }

        /// <summary>Thread-safe enough for live tweaks: two independent float writes, read once per frame.</summary>
        public void SetParameters(float ceiling, float releaseMs)
        {
            _ceiling = Math.Clamp(ceiling, 0.01f, 1.0f);
            double tau = Math.Max(1.0, releaseMs) / 1000.0 * _sampleRate;
            _releaseCoef = (float)(1.0 - Math.Exp(-1.0 / tau));
        }

        /// <summary>Processes interleaved frames in place (output is delayed by LatencyFrames).</summary>
        public void Process(Span<float> interleaved)
        {
            int ch = _channels;
            int frames = interleaved.Length / ch;
            float ceiling = _ceiling;
            float rel = _releaseCoef;
            int L = _lookahead;
            int W = _window;

            for (int f = 0; f < frames; f++)
            {
                int o = f * ch;

                float peak = 0f;
                for (int c = 0; c < ch; c++)
                {
                    float a = Math.Abs(interleaved[o + c]);
                    if (a > peak) peak = a;
                }
                float required = peak > ceiling ? ceiling / peak : 1f;

                // Sliding minimum over the last W = L + H frames
                long n = _frame++;
                // Evict the frame leaving the window first, so the ring (W slots) never holds more than W entries
                while (_minCount > 0 && _minIdx[_minHead] <= n - W) { _minHead = _minHead + 1 == W ? 0 : _minHead + 1; _minCount--; }
                while (_minCount > 0 && _minVal[(_minHead + _minCount - 1) % W] >= required) _minCount--;
                int tail = (_minHead + _minCount) % W;
                _minVal[tail] = required;
                _minIdx[tail] = n;
                _minCount++;
                float windowMin = _minVal[_minHead];

                // Moving average over L frames -> linear attack ramp
                _avgSum += windowMin - _avgRing[_avgPos];
                _avgRing[_avgPos] = windowMin;
                _avgPos = _avgPos + 1 == L ? 0 : _avgPos + 1;
                if ((n & 0xFFFF) == 0) // re-sum now and then so float error cannot creep in
                {
                    double s = 0; for (int i = 0; i < L; i++) s += _avgRing[i]; _avgSum = s;
                }
                float target = (float)(_avgSum / L);

                // Instant toward lower gain (already ramped), exponential release toward higher gain
                _env = target < _env ? target : _env + (target - _env) * rel;
                float g = _env;

                // Delay line: write current frame, read the one written L-1 frames ago
                int w = _delayPos * ch;
                int r = (_delayPos + 1 == L ? 0 : _delayPos + 1) * ch;
                for (int c = 0; c < ch; c++)
                {
                    _delay[w + c] = interleaved[o + c];
                }
                for (int c = 0; c < ch; c++)
                {
                    float y = _delay[r + c] * g;
                    // Safety net only; the gain math already keeps |y| <= ceiling
                    if (y > ceiling) y = ceiling; else if (y < -ceiling) y = -ceiling;
                    interleaved[o + c] = y;
                }
                _delayPos = _delayPos + 1 == L ? 0 : _delayPos + 1;
            }
        }
    }
}
