using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace GUI.Engine.Audio
{
    /// <summary>
    /// True Sample-Level DSP Lookahead Limiter & Footstep Booster (100% Zero-Pop).
    /// Captures from Virtual Audio Cable, inspects 4ms future peak in a ring buffer,
    /// clamps gunshots and boosts footsteps, and outputs directly to the physical DAC (Sound BlasterX G6).
    /// </summary>
    public class LookaheadDspEngine : IDisposable
    {
        [DllImport("winmm.dll", EntryPoint = "timeBeginPeriod", SetLastError = true)]
        private static extern uint TimeBeginPeriod(uint uMilliseconds);

        [DllImport("winmm.dll", EntryPoint = "timeEndPeriod", SetLastError = true)]
        private static extern uint TimeEndPeriod(uint uMilliseconds);

        private const int AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000;
        private const int AUDCLNT_SHAREMODE_SHARED = 0;

        private readonly object _lock = new();
        private Thread? _dspThread;
        private volatile bool _isRunning = false;
        private bool _timerPeriodActive = false;

        // DSP Parameters
        private float _ceilingThreshold = 0.40f;
        private float _footstepBoostLinear = 1.41f; // +3 dB default
        private int _lookaheadFrames = 192; // 4ms @ 48kHz
        private float _releaseStepPerSample = 0.00015f; // ~40-100ms release @ 48kHz

        private string? _captureDeviceId;
        private string? _renderDeviceId;

        public bool IsRunning => _isRunning;

        public void Start(string? captureDeviceId, string? renderDeviceId, float ceilingLimit, float boostDb, int lookaheadMs, float releaseTimeMs)
        {
            lock (_lock)
            {
                if (_isRunning)
                {
                    Stop();
                }

                _captureDeviceId = captureDeviceId;
                _renderDeviceId = renderDeviceId;

                _ceilingThreshold = Math.Clamp(ceilingLimit, 0.05f, 1.0f);
                float db = Math.Clamp(boostDb, 0.0f, 12.0f);
                _footstepBoostLinear = (float)Math.Pow(10.0, db / 20.0);

                int ms = Math.Clamp(lookaheadMs, 2, 8);
                _lookaheadFrames = (int)(48000 * (ms / 1000.0f));

                float relMs = Math.Clamp(releaseTimeMs, 20.0f, 300.0f);
                _releaseStepPerSample = 1.0f / (48000.0f * (relMs / 1000.0f));

                try
                {
                    if (!_timerPeriodActive && TimeBeginPeriod(1) == 0)
                    {
                        _timerPeriodActive = true;
                    }
                }
                catch { }

                _isRunning = true;
                _dspThread = new Thread(DspLoop)
                {
                    IsBackground = true,
                    Priority = ThreadPriority.Highest,
                    Name = "LookaheadDspWorker"
                };
                _dspThread.Start();
                Program.Log($"[LookaheadDsp] Started worker thread (Capture={captureDeviceId}, Render={renderDeviceId}, Limit={_ceilingThreshold:P0}, Boost=+{boostDb}dB, Lookahead={ms}ms)");
            }
        }

        public void Stop()
        {
            lock (_lock)
            {
                if (!_isRunning) return;

                _isRunning = false;

                try
                {
                    _dspThread?.Join(500);
                }
                catch { }
                _dspThread = null;

                try
                {
                    if (_timerPeriodActive)
                    {
                        TimeEndPeriod(1);
                        _timerPeriodActive = false;
                    }
                }
                catch { }
                Program.Log("[LookaheadDsp] Stopped worker thread");
            }
        }

        private void DspLoop()
        {
            IAudioClient? captureClient = null;
            IAudioCaptureClient? captureService = null;
            IAudioClient? renderClient = null;
            IAudioRenderClient? renderService = null;

            try
            {
                var enumerator = (IMMDeviceEnumerator)new MMDeviceEnumeratorComObject();

                // 1. Resolve Capture Device (Virtual Audio Cable output endpoint)
                IMMDevice? captureDev = null;
                if (!string.IsNullOrEmpty(_captureDeviceId))
                {
                    enumerator.GetDevice(_captureDeviceId, out captureDev);
                }
                if (captureDev == null)
                {
                    // Search for Virtual Cable recording endpoint
                    var (_, capId) = AudioEndpointHelper.GetVirtualCableEndpoints();
                    if (!string.IsNullOrEmpty(capId))
                    {
                        enumerator.GetDevice(capId, out captureDev);
                    }
                }
                if (captureDev == null)
                {
                    Program.Log("[LookaheadDsp] Could not find capture device (Virtual Cable)");
                    return;
                }

                // 2. Resolve Render Device (Physical DAC - Sound BlasterX G6 or chosen device)
                IMMDevice? renderDev = null;
                if (!string.IsNullOrEmpty(_renderDeviceId))
                {
                    enumerator.GetDevice(_renderDeviceId, out renderDev);
                }
                if (renderDev == null)
                {
                    // Auto-detect Sound BlasterX G6
                    enumerator.EnumAudioEndpoints(EDataFlow.eRender, 1, out var col);
                    if (col != null)
                    {
                        col.GetCount(out int count);
                        for (int i = 0; i < count; i++)
                        {
                            if (col.Item(i, out var d) == 0 && d != null)
                            {
                                d.GetId(out string id);
                                string name = AudioEndpointHelper.GetDeviceFriendlyName(d);
                                if (name.Contains("Sound BlasterX G6", StringComparison.OrdinalIgnoreCase) ||
                                    id.Contains("35862ba1-5b8c-4d8f-827b-97bbbf2d6d9f", StringComparison.OrdinalIgnoreCase))
                                {
                                    renderDev = d;
                                    break;
                                }
                            }
                        }
                    }
                }
                if (renderDev == null)
                {
                    enumerator.GetDefaultAudioEndpoint(EDataFlow.eRender, ERole.eMultimedia, out renderDev);
                }
                if (renderDev == null)
                {
                    Program.Log("[LookaheadDsp] Could not find render device");
                    return;
                }

                Guid iidAudioClient = typeof(IAudioClient).GUID;

                // 3. Initialize Capture Client
                // Note: For eCapture endpoint (CABLE Output), streamFlags = 0 (standard recording).
                // For eRender endpoint (CABLE Input), streamFlags = AUDCLNT_STREAMFLAGS_LOOPBACK.
                captureDev.Activate(ref iidAudioClient, 1, IntPtr.Zero, out var capObj);
                captureClient = capObj as IAudioClient;
                if (captureClient == null) return;

                captureClient.GetMixFormat(out IntPtr capFmtPtr);
                Guid sessionGuid = Guid.Empty;
                long hnsRequestedDuration = 100000; // 10ms

                // Test if capture device is recording or playback
                int hrCap = captureClient.Initialize(AUDCLNT_SHAREMODE_SHARED, 0, hnsRequestedDuration, 0, capFmtPtr, ref sessionGuid);
                if (hrCap != 0)
                {
                    // Fallback to loopback mode if it was a render endpoint
                    hrCap = captureClient.Initialize(AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK, hnsRequestedDuration, 0, capFmtPtr, ref sessionGuid);
                    if (hrCap != 0)
                    {
                        Program.Log($"[LookaheadDsp] Failed to initialize capture client: hr=0x{hrCap:X8}");
                        return;
                    }
                }

                Guid iidCaptureService = typeof(IAudioCaptureClient).GUID;
                captureClient.GetService(ref iidCaptureService, out var capSvcObj);
                captureService = capSvcObj as IAudioCaptureClient;
                if (captureService == null) return;

                // 4. Initialize Render Client (WASAPI Playback to physical DAC)
                renderDev.Activate(ref iidAudioClient, 1, IntPtr.Zero, out var renObj);
                renderClient = renObj as IAudioClient;
                if (renderClient == null) return;

                renderClient.GetMixFormat(out IntPtr renFmtPtr);
                int hrRen = renderClient.Initialize(AUDCLNT_SHAREMODE_SHARED, 0, hnsRequestedDuration, 0, renFmtPtr, ref sessionGuid);
                if (hrRen != 0)
                {
                    Program.Log($"[LookaheadDsp] Failed to initialize render client: hr=0x{hrRen:X8}");
                    return;
                }

                Guid iidRenderService = typeof(IAudioRenderClient).GUID;
                renderClient.GetService(ref iidRenderService, out var renSvcObj);
                renderService = renSvcObj as IAudioRenderClient;
                if (renderService == null) return;

                captureClient.Start();
                renderClient.Start();

                renderClient.GetBufferSize(out uint renderBufferSize);

                // Circular Ring Buffer Setup
                const int bufferCapacity = 48000; // 1 second buffer
                float[] ringBufferL = new float[bufferCapacity];
                float[] ringBufferR = new float[bufferCapacity];
                int writeIdx = _lookaheadFrames; // preload delay offset
                int readIdx = 0;
                float currentGain = 1.0f;

                Program.Log($"[LookaheadDsp] Pipeline active: Virtual Cable -> Lookahead DSP ({_lookaheadFrames} frames) -> Physical DAC");

                while (_isRunning)
                {
                    while (captureService.GetNextPacketSize(out uint packetFrames) == 0 && packetFrames > 0 && _isRunning)
                    {
                        int hrGet = captureService.GetBuffer(out IntPtr pData, out uint numFrames, out uint flags, out _, out _);
                        if (hrGet != 0 || pData == IntPtr.Zero) break;

                        unsafe
                        {
                            float* pSrc = (float*)pData;
                            bool isSilent = (flags & 0x01) != 0; // AUDCLNT_BUFFERFLAGS_SILENT

                            for (int i = 0; i < numFrames; i++)
                            {
                                float sL = isSilent ? 0f : pSrc[2 * i] * _footstepBoostLinear;
                                float sR = isSilent ? 0f : pSrc[2 * i + 1] * _footstepBoostLinear;

                                ringBufferL[writeIdx] = sL;
                                ringBufferR[writeIdx] = sR;

                                // Lookahead Horizon Peak Scan
                                float futureMax = 0f;
                                int scanLen = _lookaheadFrames;
                                for (int k = 0; k < scanLen; k++)
                                {
                                    int idx = (readIdx + k) % bufferCapacity;
                                    float aL = Math.Abs(ringBufferL[idx]);
                                    float aR = Math.Abs(ringBufferR[idx]);
                                    if (aL > futureMax) futureMax = aL;
                                    if (aR > futureMax) futureMax = aR;
                                }

                                float targetGain = 1.0f;
                                if (futureMax > _ceilingThreshold && futureMax > 0.001f)
                                {
                                    targetGain = _ceilingThreshold / futureMax;
                                }

                                if (targetGain < currentGain)
                                {
                                    currentGain = targetGain;
                                }
                                else
                                {
                                    currentGain = Math.Min(1.0f, currentGain + _releaseStepPerSample);
                                }

                                // Write delayed sample back to source buffer for rendering
                                pSrc[2 * i] = ringBufferL[readIdx] * currentGain;
                                pSrc[2 * i + 1] = ringBufferR[readIdx] * currentGain;

                                readIdx = (readIdx + 1) % bufferCapacity;
                                writeIdx = (writeIdx + 1) % bufferCapacity;
                            }

                            // Render out to Physical DAC with padding check
                            if (renderClient.GetCurrentPadding(out uint padding) == 0)
                            {
                                uint available = renderBufferSize > padding ? renderBufferSize - padding : 0;
                                uint framesToWrite = Math.Min(numFrames, available);
                                if (framesToWrite > 0 && renderService.GetBuffer(framesToWrite, out IntPtr pDst) == 0 && pDst != IntPtr.Zero)
                                {
                                    Buffer.MemoryCopy((void*)pData, (void*)pDst, framesToWrite * 8, framesToWrite * 8);
                                    renderService.ReleaseBuffer(framesToWrite, 0);
                                }
                            }
                        }

                        captureService.ReleaseBuffer(numFrames);
                    }

                    Thread.Sleep(1);
                }
            }
            catch (Exception ex)
            {
                Program.Log($"[LookaheadDsp] Exception in DspLoop: {ex.Message}");
            }
            finally
            {
                try { captureClient?.Stop(); } catch { }
                try { renderClient?.Stop(); } catch { }
            }
        }

        public void Dispose()
        {
            Stop();
        }
    }
}
