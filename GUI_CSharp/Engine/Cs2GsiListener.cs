using System;
using System.IO;
using System.Net;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace GUI.Engine
{
    public class Cs2GsiListener : IDisposable
    {
        private HttpListener? _listener;
        private CancellationTokenSource? _cts;
        private Task? _listenTask;
        private bool _isDisposed = false;
        private bool _hasLoggedFirstConnection = false;

        private readonly System.Threading.Timer _watchdogTimer;
        private DateTime _lastPacketTime = DateTime.MinValue;
        private double _lastFlashed = 0.0;
        private double _lastSmoked = 0.0;

        public int Port { get; }
        public bool IsListening => _listener != null && _listener.IsListening;
        public bool IsCs2Connected => (DateTime.UtcNow - _lastPacketTime).TotalSeconds < 3.0;

        /// <summary>
        /// Fired when CS2 sends player state update.
        /// Arguments: (double flashedFraction [0.0-1.0], double smokedFraction [0.0-1.0], bool isPlayerActive, string? rawFlashed)
        /// </summary>
        public event Action<double, double, bool, string?>? StateUpdated;

        public Cs2GsiListener(int port = 23456)
        {
            Port = port;

            // Failsafe watchdog timer: If flashed > 0 but game suddenly stops sending updates
            // (e.g. crash / alt-f4 / freeze), revert back to 0 so screen never stays black!
            _watchdogTimer = new System.Threading.Timer(OnWatchdogTick, null, 1000, 1000);
        }

        public void Start()
        {
            if (_listener != null && _listener.IsListening) return;

            try
            {
                _cts = new CancellationTokenSource();
                _listener = new HttpListener();
                _listener.Prefixes.Add($"http://127.0.0.1:{Port}/");
                _listener.Start();

                Program.Log($"Cs2GsiListener: Listening on http://127.0.0.1:{Port}/");
                _listenTask = Task.Run(() => ListenLoopAsync(_cts.Token));
            }
            catch (Exception ex)
            {
                Program.Log($"Cs2GsiListener: Failed to start listener on port {Port}: {ex.Message}");
            }
        }

        private async Task ListenLoopAsync(CancellationToken token)
        {
            while (!token.IsCancellationRequested && _listener != null && _listener.IsListening)
            {
                try
                {
                    var context = await _listener.GetContextAsync().ConfigureAwait(false);
                    ThreadPool.QueueUserWorkItem(_ => ProcessRequest(context));
                }
                catch (HttpListenerException) when (token.IsCancellationRequested)
                {
                    break;
                }
                catch (ObjectDisposedException)
                {
                    break;
                }
                catch (Exception ex)
                {
                    Program.Log($"Cs2GsiListener ListenLoop error: {ex.Message}");
                }
            }
        }

        private void ProcessRequest(HttpListenerContext context)
        {
            try
            {
                if (context.Request.HttpMethod == "POST")
                {
                    using (var doc = JsonDocument.Parse(context.Request.InputStream))
                    {
                        ParseAndDispatch(doc.RootElement);
                    }
                }

                context.Response.StatusCode = (int)HttpStatusCode.OK;
                context.Response.Close();
            }
            catch (Exception ex)
            {
                try
                {
                    context.Response.StatusCode = (int)HttpStatusCode.OK;
                    context.Response.Close();
                }
                catch { }
                Program.Log($"Cs2GsiListener.ProcessRequest error: {ex.Message}");
            }
        }

        private readonly object _syncLock = new object();

        private void ParseAndDispatch(JsonElement root)
        {
            lock (_syncLock)
            {
                try
                {
                    string? providerSteamId = null;
                    if (root.TryGetProperty("provider", out var provElem) &&
                        provElem.TryGetProperty("steamid", out var pSteamElem))
                    {
                        providerSteamId = pSteamElem.GetString();
                    }

                    _lastPacketTime = DateTime.UtcNow;

                    if (!_hasLoggedFirstConnection && !string.IsNullOrEmpty(providerSteamId))
                    {
                        _hasLoggedFirstConnection = true;
                        Program.Log($"Cs2GsiListener: Connected to CS2 (SteamID: {providerSteamId})");
                    }

                    // CRITICAL: If this packet is purely map/round/heartbeat info without player state,
                    // do not falsely overwrite player's flashed status with 0!
                    if (!root.TryGetProperty("player", out var playerElem) ||
                        !playerElem.TryGetProperty("state", out var stateElem))
                    {
                        return;
                    }

                    string? playerSteamId = null;
                    if (playerElem.TryGetProperty("steamid", out var playerSteamElem))
                    {
                        playerSteamId = playerSteamElem.GetString();
                    }

                    double flashedFraction = 0.0;
                    double smokedFraction = 0.0;
                    int health = 100;
                    string? rawFlashed = null;

                    if (stateElem.TryGetProperty("flashed", out var flashedElem))
                    {
                        rawFlashed = flashedElem.GetRawText();
                        if (flashedElem.ValueKind == JsonValueKind.Number && flashedElem.TryGetDouble(out double fVal))
                        {
                            if (fVal > 1.0)
                            {
                                flashedFraction = Math.Clamp(fVal / 255.0, 0.0, 1.0);
                            }
                            else if (fVal > 0.0)
                            {
                                flashedFraction = Math.Clamp(fVal, 0.0, 1.0);
                            }
                        }
                        else if (flashedElem.ValueKind == JsonValueKind.True)
                        {
                            flashedFraction = 1.0;
                        }
                        else if (flashedElem.ValueKind == JsonValueKind.String &&
                                 double.TryParse(flashedElem.GetString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out double fsVal))
                        {
                            if (fsVal > 1.0) flashedFraction = Math.Clamp(fsVal / 255.0, 0.0, 1.0);
                            else if (fsVal > 0.0) flashedFraction = Math.Clamp(fsVal, 0.0, 1.0);
                        }
                    }

                    if (stateElem.TryGetProperty("smoked", out var smokedElem))
                    {
                        if (smokedElem.ValueKind == JsonValueKind.Number && smokedElem.TryGetDouble(out double sVal))
                        {
                            if (sVal > 1.0)
                            {
                                smokedFraction = Math.Clamp(sVal / 255.0, 0.0, 1.0);
                            }
                            else if (sVal > 0.0)
                            {
                                smokedFraction = Math.Clamp(sVal, 0.0, 1.0);
                            }
                        }
                        else if (smokedElem.ValueKind == JsonValueKind.True)
                        {
                            smokedFraction = 1.0;
                        }
                        else if (smokedElem.ValueKind == JsonValueKind.String &&
                                 double.TryParse(smokedElem.GetString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out double ssVal))
                        {
                            if (ssVal > 1.0) smokedFraction = Math.Clamp(ssVal / 255.0, 0.0, 1.0);
                            else if (ssVal > 0.0) smokedFraction = Math.Clamp(ssVal, 0.0, 1.0);
                        }
                    }

                    if (stateElem.TryGetProperty("health", out var healthElem))
                    {
                        if (healthElem.ValueKind == JsonValueKind.Number && healthElem.TryGetInt32(out int hVal))
                        {
                            health = hVal;
                        }
                        else if (healthElem.ValueKind == JsonValueKind.String && int.TryParse(healthElem.GetString(), out int hsVal))
                        {
                            health = hsVal;
                        }
                    }

                    // In spectator mode, playerSteamId is the target player and differs from our providerSteamId
                    bool isSpectatingOther = !string.IsNullOrEmpty(playerSteamId) &&
                                             !string.IsNullOrEmpty(providerSteamId) &&
                                             playerSteamId.Length >= 15 && providerSteamId.Length >= 15 &&
                                             !string.Equals(providerSteamId, playerSteamId, StringComparison.OrdinalIgnoreCase);

                    // If health is explicitly 0, player is dead
                    if (health == 0)
                    {
                        isSpectatingOther = true;
                    }

                    double effectiveFlashed = isSpectatingOther ? 0.0 : flashedFraction;
                    double effectiveSmoked = isSpectatingOther ? 0.0 : smokedFraction;
                    bool isActive = !isSpectatingOther;

                    _lastFlashed = effectiveFlashed;
                    _lastSmoked = effectiveSmoked;

                    StateUpdated?.Invoke(effectiveFlashed, effectiveSmoked, isActive, rawFlashed);
            }
            catch (JsonException) { }
            catch (Exception ex)
            {
                Program.Log($"Cs2GsiListener Parse error: {ex.Message}");
            }
            }
        }

        private void OnWatchdogTick(object? state)
        {
            if (_lastFlashed > 0.001 || _lastSmoked > 0.001)
            {
                if ((DateTime.UtcNow - _lastPacketTime).TotalSeconds > 2.0)
                {
                    _lastFlashed = 0.0;
                    _lastSmoked = 0.0;
                    StateUpdated?.Invoke(0.0, 0.0, false, null);
                }
            }
        }

        public void Stop()
        {
            try
            {
                _cts?.Cancel();
                _listener?.Stop();
                _listener?.Close();
            }
            catch { }
            finally
            {
                _listener = null;
            }
        }

        public void Dispose()
        {
            if (_isDisposed) return;
            _isDisposed = true;
            _watchdogTimer.Dispose();
            Stop();
            _cts?.Dispose();
        }
    }
}
