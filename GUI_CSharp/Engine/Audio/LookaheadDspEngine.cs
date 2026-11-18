using System;
using System.Runtime.InteropServices;
using System.Threading;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// Game audio -> Virtual Cable -> this pipeline -> physical output (headset / speakers / DAC).
    /// The only processing is <see cref="LookaheadLimiter"/>: a short delay so the gain is already down when a gunshot
    /// arrives. No boost, no EQ, no loudness. A small FIFO absorbs the 10 ms capture packets, and one frame is dropped or
    /// repeated now and then to follow the clock difference between the cable and the DAC (inaudible, no crackle).
    /// </summary>
    public sealed class LookaheadDspEngine : IDisposable
    {
        [DllImport("winmm.dll", EntryPoint = "timeBeginPeriod")]
        private static extern uint TimeBeginPeriod(uint uMilliseconds);

        [DllImport("winmm.dll", EntryPoint = "timeEndPeriod")]
        private static extern uint TimeEndPeriod(uint uMilliseconds);

        // MMCSS: lets Windows schedule this thread like its own audio engine, so a game pegging the CPU cannot
        // delay it for milliseconds (that delay is what turns into crackles)
        [DllImport("avrt.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr AvSetMmThreadCharacteristicsW(string taskName, ref uint taskIndex);

        [DllImport("avrt.dll", SetLastError = true)]
        private static extern bool AvSetMmThreadPriority(IntPtr avrtHandle, int priority);

        [DllImport("avrt.dll", SetLastError = true)]
        private static extern bool AvRevertMmThreadCharacteristics(IntPtr avrtHandle);

        private const int AVRT_PRIORITY_HIGH = 1;

        private const int AUDCLNT_SHAREMODE_SHARED = 0;
        private const uint AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000;
        private const uint AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000;
        private const uint AUDCLNT_BUFFERFLAGS_SILENT = 0x2;
        private const long BufferDuration100ns = 500_000; // 50 ms of headroom; latency is set by what we queue, not this

        // Shortest release that stays clean: the 12 ms hold spans half a cycle of 30 Hz bass, so the gain does not
        // follow the waveform; footsteps are back at full level ~17 ms after the last gunshot peak
        // (sweep: THD < 0.12 % down to 25 Hz at 2x over the ceiling).
        private const float ReleaseMs = 5f;
        private const float HoldMs = 12f;

        private static readonly Guid SubtypeIeeeFloat = new("00000003-0000-0010-8000-00aa00389b71");

        private readonly object _lock = new();
        private Thread? _thread;
        private volatile bool _isRunning;
        private bool _timerPeriodActive;

        private string? _captureId;
        private string? _renderId;
        private int _lookaheadMs;
        private float _ceiling;
        private volatile LookaheadLimiter? _limiter;

        private ManualResetEventSlim? _ready;
        private volatile bool _initOk;

        public bool IsRunning => _isRunning;

        /// <summary>Raised on the DSP thread when a running pipeline dies (device removed, audio service restart...).</summary>
        public event Action? Faulted;

        /// <summary>
        /// Starts the pipeline and waits until both streams are open. Returns false when it cannot run, so the caller
        /// never routes the game into a cable nobody reads. Calling it again with the same devices only updates the
        /// ceiling live (no restart, no gap).
        /// </summary>
        public bool Start(string captureDeviceId, string renderDeviceId, float ceiling, int lookaheadMs)
        {
            lock (_lock)
            {
                ceiling = Math.Clamp(ceiling, 0.05f, 1.0f);
                lookaheadMs = Math.Clamp(lookaheadMs, 1, 10);

                if (_isRunning && _initOk && _thread != null && _thread.IsAlive &&
                    string.Equals(_captureId, captureDeviceId, StringComparison.OrdinalIgnoreCase) &&
                    string.Equals(_renderId, renderDeviceId, StringComparison.OrdinalIgnoreCase) &&
                    _lookaheadMs == lookaheadMs)
                {
                    _ceiling = ceiling;
                    _limiter?.SetParameters(ceiling, ReleaseMs);
                    return true;
                }

                StopCore();

                _captureId = captureDeviceId;
                _renderId = renderDeviceId;
                _lookaheadMs = lookaheadMs;
                _ceiling = ceiling;
                _initOk = false;
                _ready = new ManualResetEventSlim(false);

                try
                {
                    if (!_timerPeriodActive && TimeBeginPeriod(1) == 0) _timerPeriodActive = true;
                }
                catch { }

                _isRunning = true;
                _thread = new Thread(DspLoop)
                {
                    IsBackground = true,
                    Priority = ThreadPriority.Highest,
                    Name = "LookaheadDspWorker"
                };
                _thread.Start();

                if (!_ready.Wait(2000) || !_initOk)
                {
                    Program.Log("[LookaheadDsp] Pipeline did not start");
                    StopCore();
                    return false;
                }

                Program.Log($"[LookaheadDsp] Started (capture={captureDeviceId}, render={renderDeviceId}, ceiling={ceiling:P0}, lookahead={lookaheadMs}ms, hold={HoldMs}ms, release={ReleaseMs}ms)");
                return true;
            }
        }

        public void Stop()
        {
            lock (_lock)
            {
                StopCore();
            }
        }

        private void StopCore()
        {
            bool wasRunning = _isRunning || _thread != null;
            _isRunning = false;
            try { _thread?.Join(1000); } catch { }
            _thread = null;
            _limiter = null;
            _ready?.Dispose();
            _ready = null;

            if (_timerPeriodActive)
            {
                try { TimeEndPeriod(1); } catch { }
                _timerPeriodActive = false;
            }
            if (wasRunning) Program.Log("[LookaheadDsp] Stopped");
        }

        private readonly struct StreamFormat
        {
            public readonly int Channels;
            public readonly int SampleRate;
            public readonly bool IsFloat32;

            public StreamFormat(IntPtr pFmt)
            {
                ushort tag = (ushort)Marshal.ReadInt16(pFmt, 0);
                Channels = (ushort)Marshal.ReadInt16(pFmt, 2);
                SampleRate = Marshal.ReadInt32(pFmt, 4);
                ushort bits = (ushort)Marshal.ReadInt16(pFmt, 14);
                bool isFloat = tag == 3;
                if (tag == 0xFFFE)
                {
                    byte[] sub = new byte[16];
                    Marshal.Copy(pFmt + 24, sub, 0, 16);
                    isFloat = new Guid(sub) == SubtypeIeeeFloat;
                }
                IsFloat32 = isFloat && bits == 32;
            }

            public override string ToString() => $"{Channels}ch {SampleRate}Hz {(IsFloat32 ? "float32" : "non-float")}";
        }

        private void DspLoop()
        {
            IMMDeviceEnumerator? enumerator = null;
            IMMDevice? captureDev = null, renderDev = null;
            IAudioClient? captureClient = null, renderClient = null;
            IAudioCaptureClient? captureService = null;
            IAudioRenderClient? renderService = null;
            IntPtr capFmt = IntPtr.Zero;
            bool faulted = false;
            IntPtr mmcss = IntPtr.Zero;

            try
            {
                uint taskIndex = 0;
                mmcss = AvSetMmThreadCharacteristicsW("Pro Audio", ref taskIndex);
                if (mmcss != IntPtr.Zero) AvSetMmThreadPriority(mmcss, AVRT_PRIORITY_HIGH);
                else Program.Log($"[LookaheadDsp] MMCSS registration failed ({Marshal.GetLastWin32Error()}), normal thread priority");

                enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();
                if (enumerator.GetDevice(_captureId!, out captureDev) != 0 || captureDev == null) { Program.Log("[LookaheadDsp] Capture device not found"); return; }
                if (enumerator.GetDevice(_renderId!, out renderDev) != 0 || renderDev == null) { Program.Log("[LookaheadDsp] Render device not found"); return; }

                Guid iidAudioClient = typeof(IAudioClient).GUID;
                if (captureDev.Activate(ref iidAudioClient, 1, IntPtr.Zero, out var capObj) != 0 || (captureClient = capObj as IAudioClient) == null) return;
                if (renderDev.Activate(ref iidAudioClient, 1, IntPtr.Zero, out var renObj) != 0 || (renderClient = renObj as IAudioClient) == null) return;

                if (captureClient.GetMixFormat(out capFmt) != 0 || capFmt == IntPtr.Zero) return;
                var fmt = new StreamFormat(capFmt);
                if (!fmt.IsFloat32 || fmt.Channels < 1 || fmt.SampleRate < 8000)
                {
                    Program.Log($"[LookaheadDsp] Unsupported cable format {fmt}");
                    return;
                }

                Guid session = Guid.Empty;
                int hr = captureClient.Initialize(AUDCLNT_SHAREMODE_SHARED, 0, BufferDuration100ns, 0, capFmt, ref session);
                if (hr != 0) { Program.Log($"[LookaheadDsp] Capture Initialize failed: 0x{hr:X8}"); return; }

                // Render in the cable's own format; Windows converts if the DAC runs another rate / channel count
                session = Guid.Empty;
                hr = renderClient.Initialize(AUDCLNT_SHAREMODE_SHARED,
                    AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                    BufferDuration100ns, 0, capFmt, ref session);
                if (hr != 0) { Program.Log($"[LookaheadDsp] Render Initialize failed: 0x{hr:X8}"); return; }

                Guid iidCapture = typeof(IAudioCaptureClient).GUID;
                if (captureClient.GetService(ref iidCapture, out var capSvc) != 0 || (captureService = capSvc as IAudioCaptureClient) == null) return;
                Guid iidRender = typeof(IAudioRenderClient).GUID;
                if (renderClient.GetService(ref iidRender, out var renSvc) != 0 || (renderService = renSvc as IAudioRenderClient) == null) return;

                renderClient.GetBufferSize(out uint renderBufferFrames);
                renderClient.GetDevicePeriod(out long periodHns, out _);
                int ch = fmt.Channels;
                int rate = fmt.SampleRate;
                int periodFrames = Math.Max(1, (int)(periodHns * rate / 10_000_000));

                // Cushion = how far the DAC queue may sink before the next capture packet arrives. One period normally;
                // each time the DAC still runs dry (CPU spike, stall) it grows by a period, up to three, and after 30 s
                // without trouble it shrinks back one period at a time. Latency stays minimal unless the PC needs more.
                int cushion = periodFrames;
                int maxCushion = Math.Max(periodFrames, Math.Min(periodFrames * 3, (int)renderBufferFrames - periodFrames));

                var limiter = new LookaheadLimiter(ch, rate, _lookaheadMs, _ceiling, ReleaseMs, HoldMs);
                _limiter = limiter;

                var fifo = new float[rate * ch]; // 1 s
                int fifoCapFrames = rate;
                int fifoHead = 0, fifoFrames = 0;
                float[] scratch = new float[rate / 10 * ch];
                float[] lastFrame = new float[ch];

                var render = renderService;
                void WriteSilence(int frames)
                {
                    if (frames > 0 && render.GetBuffer((uint)frames, out _) == 0)
                    {
                        render.ReleaseBuffer((uint)frames, AUDCLNT_BUFFERFLAGS_SILENT);
                    }
                }

                // The cushion in silence first: capture arrives in period-sized packets, so without it the DAC queue
                // would touch empty right before every packet
                WriteSilence(cushion);

                if (captureClient.Start() != 0 || renderClient.Start() != 0) return;

                Program.Log($"[LookaheadDsp] Cable {fmt}, DAC period {periodFrames} frames, cushion {cushion}..{maxCushion} frames, lookahead {limiter.LatencyFrames + 1} frames, MMCSS {(mmcss != IntPtr.Zero ? "Pro Audio" : "off")}");
                _initOk = true;
                try { _ready?.Set(); } catch (ObjectDisposedException) { }

                // Clock drift: watch the lowest queued level over a window; nudge by one frame when it drifts away
                long startTick = Environment.TickCount64;
                int windowMinQueued = int.MaxValue;
                long windowStart = startTick;
                long lastTrouble = startTick;
                int dropPending = 0, repeatPending = 0;
                long drops = 0, repeats = 0, dryEvents = 0;
                bool primed = false, wasDry = false;

                while (_isRunning)
                {
                    // 1. Capture -> limiter -> FIFO
                    while (_isRunning)
                    {
                        hr = captureService.GetNextPacketSize(out uint packet);
                        if (hr < 0) throw new COMException("GetNextPacketSize", hr);
                        if (packet == 0) break;

                        hr = captureService.GetBuffer(out IntPtr pData, out uint numFrames, out uint flags, out _, out _);
                        if (hr < 0) throw new COMException("Capture GetBuffer", hr);
                        if (hr != 0 || numFrames == 0) break;

                        int samples = (int)numFrames * ch;
                        if (scratch.Length < samples) scratch = new float[samples];
                        if ((flags & AUDCLNT_BUFFERFLAGS_SILENT) != 0 || pData == IntPtr.Zero)
                            Array.Clear(scratch, 0, samples);
                        else
                            Marshal.Copy(pData, scratch, 0, samples);
                        captureService.ReleaseBuffer(numFrames);

                        limiter.Process(scratch.AsSpan(0, samples));

                        // Push; on overflow the oldest audio goes (only if the DAC stalled for a full second)
                        int frames = (int)numFrames;
                        if (frames > fifoCapFrames) frames = fifoCapFrames;
                        int overflow = fifoFrames + frames - fifoCapFrames;
                        if (overflow > 0) { fifoHead = (fifoHead + overflow) % fifoCapFrames; fifoFrames -= overflow; }
                        int tailFrame = (fifoHead + fifoFrames) % fifoCapFrames;
                        int first = Math.Min(frames, fifoCapFrames - tailFrame);
                        Array.Copy(scratch, 0, fifo, tailFrame * ch, first * ch);
                        if (first < frames) Array.Copy(scratch, first * ch, fifo, 0, (frames - first) * ch);
                        fifoFrames += frames;
                    }

                    // 2. FIFO -> DAC
                    hr = renderClient.GetCurrentPadding(out uint padding);
                    if (hr < 0) throw new COMException("GetCurrentPadding", hr);
                    long now = Environment.TickCount64;

                    // The DAC queue ran dry: it already played a gap (the crackle). Grow the cushion and refill it at
                    // once, so the next packets are not late as well, instead of rebuilding it frame by frame.
                    bool dry = primed && padding == 0;
                    if (dry && !wasDry)
                    {
                        dryEvents++;
                        lastTrouble = now;
                        if (cushion < maxCushion) cushion = Math.Min(maxCushion, cushion + periodFrames);
                        WriteSilence(Math.Max(0, cushion - fifoFrames));
                        Program.Log($"[LookaheadDsp] DAC ran dry (#{dryEvents}) -> cushion {cushion * 1000 / rate} ms");
                        hr = renderClient.GetCurrentPadding(out padding);
                        if (hr < 0) throw new COMException("GetCurrentPadding", hr);
                    }
                    wasDry = dry;

                    // Quiet for 30 s: give one period of latency back (the drift control drains it gently, no click)
                    if (cushion > periodFrames && now - lastTrouble > 30_000)
                    {
                        cushion -= periodFrames;
                        lastTrouble = now;
                        Program.Log($"[LookaheadDsp] Stable -> cushion {cushion * 1000 / rate} ms");
                    }

                    int queued = (int)padding + fifoFrames;
                    if (queued < windowMinQueued) windowMinQueued = queued;

                    if (now - windowStart >= 100) // up to 10 frames/s of correction, well above real clock drift
                    {
                        // The lowest level should sit at the cushion. Above it -> the cable runs faster than the DAC (or
                        // the cushion just shrank): drop a frame. Far below -> the DAC runs faster: repeat a frame.
                        if (now - startTick < 2000 && windowMinQueued >= cushion + periodFrames / 2 && fifoFrames > 0)
                        {
                            // Capture backlog right after start: cut it at once. This is silence, the game is routed
                            // only after Start() returns.
                            int excess = Math.Min(fifoFrames, windowMinQueued - cushion);
                            fifoHead = (fifoHead + excess) % fifoCapFrames;
                            fifoFrames -= excess;
                            drops += excess;
                        }
                        else if (windowMinQueued > cushion + periodFrames / 8) dropPending++;
                        else if (windowMinQueued < cushion / 4) repeatPending++;
                        windowMinQueued = int.MaxValue;
                        windowStart = now;
                    }

                    if (dropPending > 0 && fifoFrames > 1)
                    {
                        fifoHead = (fifoHead + 1) % fifoCapFrames;
                        fifoFrames--;
                        dropPending--;
                        drops++;
                    }

                    int renderTarget = Math.Min((int)renderBufferFrames, cushion + periodFrames);
                    int want = renderTarget - (int)padding;
                    int extra = repeatPending > 0 && fifoFrames > 0 ? 1 : 0;
                    int toWrite = Math.Min(want, fifoFrames + extra);
                    if (toWrite > 0)
                    {
                        hr = renderService.GetBuffer((uint)toWrite, out IntPtr pDst);
                        if (hr < 0) throw new COMException("Render GetBuffer", hr);
                        if (hr == 0 && pDst != IntPtr.Zero)
                        {
                            int written = 0;
                            if (extra > 0)
                            {
                                // Repeat the last frame sent: a one-sample hold, inaudible
                                Marshal.Copy(lastFrame, 0, pDst, ch);
                                written = 1;
                                repeatPending--;
                                repeats++;
                            }
                            int fromFifo = toWrite - written;
                            while (fromFifo > 0)
                            {
                                int run = Math.Min(fromFifo, fifoCapFrames - fifoHead);
                                Marshal.Copy(fifo, fifoHead * ch, pDst + written * ch * sizeof(float), run * ch);
                                Array.Copy(fifo, (fifoHead + run - 1) * ch, lastFrame, 0, ch);
                                fifoHead = (fifoHead + run) % fifoCapFrames;
                                fifoFrames -= run;
                                written += run;
                                fromFifo -= run;
                            }
                            renderService.ReleaseBuffer((uint)toWrite, 0);
                            primed = true;
                        }
                    }

                    Thread.Sleep(1);
                }

                Program.Log($"[LookaheadDsp] Loop ended (DAC ran dry {dryEvents}x, drift drops={drops}, repeats={repeats}, cushion {cushion * 1000 / rate} ms)");
            }
            catch (Exception ex)
            {
                faulted = _isRunning;
                Program.Log($"[LookaheadDsp] Pipeline error: {ex.Message}");
            }
            finally
            {
                try { captureClient?.Stop(); } catch { }
                try { renderClient?.Stop(); } catch { }
                if (capFmt != IntPtr.Zero) Marshal.FreeCoTaskMem(capFmt);
                if (mmcss != IntPtr.Zero) AvRevertMmThreadCharacteristics(mmcss);
                Release(renderService); Release(captureService);
                Release(renderClient); Release(captureClient);
                Release(renderDev); Release(captureDev); Release(enumerator);
                try { _ready?.Set(); } catch (ObjectDisposedException) { } // unblocks Start() when init failed

                if (faulted)
                {
                    try { Faulted?.Invoke(); } catch { }
                }
            }
        }

        private static void Release(object? com)
        {
            if (com != null && Marshal.IsComObject(com))
            {
                try { Marshal.ReleaseComObject(com); } catch { }
            }
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
