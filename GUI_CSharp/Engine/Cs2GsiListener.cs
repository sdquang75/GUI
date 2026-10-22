using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using System.Threading;

namespace GUI.Engine
{
    public class Cs2GsiListener : IDisposable
    {
        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentThread();

        [DllImport("kernel32.dll")]
        private static extern bool SetThreadPriority(IntPtr hThread, int nPriority);

        private const int THREAD_PRIORITY_TIME_CRITICAL = 15;

        // Minimal HTTP/1.1 server on a plain loopback socket instead of HttpListener: under full CPU load, http.sys
        // added 12-15 ms per packet (seconds when the game runs above normal priority); a socket served by our own
        // time-critical thread stays around 0.25 ms. CS2 only ever POSTs JSON with a Content-Length.
        private const int MaxHeaderBytes = 16 * 1024;
        private const int MaxBodyBytes = 4 * 1024 * 1024;
        private const int MaxConnections = 16;
        private static readonly byte[] OkResponse = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n");
        private static readonly byte[] OkCloseResponse = Encoding.ASCII.GetBytes("HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        private static readonly byte[] HeaderTerminator = { (byte)'\r', (byte)'\n', (byte)'\r', (byte)'\n' };

        // One Start..Stop run: its own socket, wake-up event and latest-state slot,
        // so a quick Stop/Start can never leave two apply threads alive on shared state.
        private sealed class Session
        {
            public readonly Socket ListenSocket;
            public readonly AutoResetEvent StateReady = new AutoResetEvent(false);
            public readonly object PendingLock = new object();
            public volatile bool Running = true;
            public GsiState Pending;
            public bool HasPending;

            public Session(Socket listenSocket)
            {
                ListenSocket = listenSocket;
            }
        }

        // One client connection with the bytes received but not yet consumed (a request may arrive in pieces)
        private sealed class Connection
        {
            public readonly Socket Socket;
            public byte[] Buffer = new byte[8192];
            public int Count;

            public Connection(Socket socket)
            {
                Socket = socket;
            }
        }

        private Session? _session;
        private bool _isDisposed = false;
        private bool _hasLoggedFirstConnection = false;

        private readonly System.Threading.Timer _watchdogTimer;
        private DateTime _lastPacketTime = DateTime.MinValue;
        private DateTime _lastFlashUpdateTime = DateTime.MinValue;
        private double _lastFlashed = 0.0;
        private double _lastSmoked = 0.0;

        public int Port { get; }
        public bool IsListening => _session?.Running ?? false;
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
            if (IsListening) return;

            Socket? listenSocket = null;
            try
            {
                // Loopback only, and exclusive so no other process can bind the same port and read CS2's packets
                listenSocket = new Socket(AddressFamily.InterNetwork, SocketType.Stream, ProtocolType.Tcp)
                {
                    ExclusiveAddressUse = true
                };
                listenSocket.Bind(new IPEndPoint(IPAddress.Loopback, Port));
                listenSocket.Listen(MaxConnections);

                var session = new Session(listenSocket);
                _session = session;

                Program.Log($"Cs2GsiListener: Listening on http://127.0.0.1:{Port}/");

                // Dedicated threads instead of thread-pool tasks: while CS2 keeps every core busy (or runs with -high),
                // normal-priority pool work can wait several ms for a core, which is felt as a late black flash.
                new Thread(() => ReceiveLoop(session)) { IsBackground = true, Name = "CS2 GSI receive", Priority = ThreadPriority.Highest }.Start();
                new Thread(() => ApplyLoop(session)) { IsBackground = true, Name = "CS2 GSI apply", Priority = ThreadPriority.Highest }.Start();
            }
            catch (Exception ex)
            {
                try { listenSocket?.Dispose(); } catch { }
                Program.Log($"Cs2GsiListener: Failed to start listener on port {Port}: {ex.Message}");
            }
        }

        // Both loops only block (Select / WaitOne / driver vblank) and do microseconds of work per packet,
        // so time-critical priority (above a -high game's threads) costs nothing but removes scheduling delay.
        private static void ElevateCurrentThread()
        {
            try { SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_TIME_CRITICAL); } catch { }
        }

        // Serves every connection from this one thread (CS2 keeps one alive, but may reconnect at any time):
        // answers each packet immediately and keeps only the newest player state for the apply thread.
        private void ReceiveLoop(Session session)
        {
            ElevateCurrentThread();
            var listen = session.ListenSocket;
            var connections = new List<Connection>();
            var readable = new List<Socket>(MaxConnections + 1);
            try
            {
                while (session.Running)
                {
                    readable.Clear();
                    readable.Add(listen);
                    foreach (var c in connections) readable.Add(c.Socket);

                    try
                    {
                        // The timeout only bounds how long a Stop() can go unnoticed; Stop closes the socket anyway
                        Socket.Select(readable, null, null, 1_000_000);
                    }
                    catch (Exception) when (!session.Running)
                    {
                        break;
                    }
                    catch (ObjectDisposedException)
                    {
                        break;
                    }
                    catch (SocketException ex)
                    {
                        // A broken client must not take the listener down: drop the clients and keep serving
                        Program.Log($"Cs2GsiListener ReceiveLoop error: {ex.Message}");
                        foreach (var c in connections) CloseConnection(c);
                        connections.Clear();
                        Thread.Sleep(50);
                        continue;
                    }

                    foreach (var s in readable)
                    {
                        if (s == listen)
                        {
                            AcceptClient(listen, connections);
                            continue;
                        }

                        var conn = connections.Find(c => c.Socket == s);
                        if (conn != null && !ServeReadable(session, conn))
                        {
                            CloseConnection(conn);
                            connections.Remove(conn);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                if (session.Running) Program.Log($"Cs2GsiListener ReceiveLoop stopped: {ex.Message}");
            }
            finally
            {
                foreach (var c in connections) CloseConnection(c);
            }
        }

        private static void AcceptClient(Socket listen, List<Connection> connections)
        {
            Socket client;
            try
            {
                client = listen.Accept();
            }
            catch (SocketException)
            {
                return;
            }

            // CS2 drops and reopens its connection now and then: make room by closing the oldest one, never refuse
            if (connections.Count >= MaxConnections)
            {
                CloseConnection(connections[0]);
                connections.RemoveAt(0);
            }

            client.NoDelay = true;
            connections.Add(new Connection(client));
        }

        private static void CloseConnection(Connection conn)
        {
            try { conn.Socket.Shutdown(SocketShutdown.Both); } catch { }
            try { conn.Socket.Close(); } catch { }
        }

        // Reads what arrived and handles every complete request in the buffer (split across reads or pipelined).
        // Returns false when the connection must be closed.
        private bool ServeReadable(Session session, Connection conn)
        {
            if (conn.Count == conn.Buffer.Length && !TryGrow(conn, conn.Buffer.Length * 2)) return false;

            int n;
            try
            {
                n = conn.Socket.Receive(conn.Buffer, conn.Count, conn.Buffer.Length - conn.Count, SocketFlags.None);
            }
            catch (SocketException)
            {
                return false;
            }
            if (n <= 0) return false;
            conn.Count += n;

            while (true)
            {
                int headerEnd = conn.Buffer.AsSpan(0, conn.Count).IndexOf(HeaderTerminator);
                if (headerEnd < 0) return conn.Count <= MaxHeaderBytes;

                if (!TryParseHead(conn.Buffer.AsSpan(0, headerEnd), out bool isPost, out int contentLength, out bool closeAfter))
                {
                    return false;
                }

                int total = headerEnd + HeaderTerminator.Length + contentLength;
                if (conn.Count < total)
                {
                    // Body still incoming: make sure the whole request fits, then wait for the rest
                    return total <= conn.Buffer.Length || TryGrow(conn, total);
                }

                // Reply first (before parsing and any GPU work), so CS2 is never held up by a ramp write
                try
                {
                    conn.Socket.Send(closeAfter ? OkCloseResponse : OkResponse);
                }
                catch (SocketException)
                {
                    return false;
                }

                if (isPost && contentLength > 0 &&
                    TryParseBody(new ReadOnlyMemory<byte>(conn.Buffer, headerEnd + HeaderTerminator.Length, contentLength), out var state))
                {
                    lock (session.PendingLock)
                    {
                        session.Pending = state;
                        session.HasPending = true;
                    }
                    session.StateReady.Set();
                }

                conn.Count -= total;
                if (conn.Count > 0) Buffer.BlockCopy(conn.Buffer, total, conn.Buffer, 0, conn.Count);
                if (closeAfter) return false;
            }
        }

        private static bool TryGrow(Connection conn, int needed)
        {
            if (needed > MaxHeaderBytes + MaxBodyBytes) return false;
            Array.Resize(ref conn.Buffer, Math.Min(Math.Max(needed, conn.Buffer.Length * 2), MaxHeaderBytes + MaxBodyBytes));
            return true;
        }

        // Request line + headers: method, Content-Length and whether the client wants the connection closed.
        // Anything this server cannot frame safely (chunked body, bad length) closes the connection.
        private static bool TryParseHead(ReadOnlySpan<byte> head, out bool isPost, out int contentLength, out bool closeAfter)
        {
            isPost = false;
            contentLength = 0;
            closeAfter = false;
            if (head.Length > MaxHeaderBytes) return false;

            string[] lines = Encoding.ASCII.GetString(head).Split("\r\n");
            string[] requestLine = lines[0].Split(' ');
            if (requestLine.Length < 3) return false;
            isPost = requestLine[0] == "POST";
            bool http10 = requestLine[2] == "HTTP/1.0";
            bool keepAlive = false;

            for (int i = 1; i < lines.Length; i++)
            {
                int colon = lines[i].IndexOf(':');
                if (colon <= 0) continue;
                string name = lines[i].Substring(0, colon).Trim();
                string value = lines[i].Substring(colon + 1).Trim();

                if (name.Equals("Content-Length", StringComparison.OrdinalIgnoreCase))
                {
                    if (!int.TryParse(value, System.Globalization.NumberStyles.None, System.Globalization.CultureInfo.InvariantCulture, out contentLength)
                        || contentLength > MaxBodyBytes)
                    {
                        return false;
                    }
                }
                else if (name.Equals("Transfer-Encoding", StringComparison.OrdinalIgnoreCase))
                {
                    return false;
                }
                else if (name.Equals("Connection", StringComparison.OrdinalIgnoreCase))
                {
                    if (value.Contains("close", StringComparison.OrdinalIgnoreCase)) closeAfter = true;
                    if (value.Contains("keep-alive", StringComparison.OrdinalIgnoreCase)) keepAlive = true;
                }
            }

            if (http10 && !keepAlive) closeAfter = true;
            return true;
        }

        private bool TryParseBody(ReadOnlyMemory<byte> body, out GsiState state)
        {
            state = default;
            try
            {
                using var doc = JsonDocument.Parse(body);
                return TryParseState(doc.RootElement, out state);
            }
            catch (Exception ex)
            {
                Program.Log($"Cs2GsiListener: bad packet: {ex.Message}");
                return false;
            }
        }

        // Coalesce: if a ramp write stalls (driver waiting for vblank), older flash values are skipped instead
        // of being replayed late, so the screen always follows the current flash state.
        private void ApplyLoop(Session session)
        {
            ElevateCurrentThread();
            while (true)
            {
                session.StateReady.WaitOne();
                if (!session.Running) break;

                GsiState latest;
                lock (session.PendingLock)
                {
                    if (!session.HasPending) continue;
                    latest = session.Pending;
                    session.HasPending = false;
                }

                try
                {
                    Dispatch(latest);
                }
                catch (Exception ex)
                {
                    Program.Log($"Cs2GsiListener ApplyLoop error: {ex.Message}");
                }
            }
        }

        private readonly record struct GsiState(double Flashed, double Smoked, bool IsActive, string? RawFlashed);

        private void Dispatch(GsiState state)
        {
            StateUpdated?.Invoke(state.Flashed, state.Smoked, state.IsActive, state.RawFlashed);
        }


        private readonly object _syncLock = new object();

        private bool TryParseState(JsonElement root, out GsiState state)
        {
            state = default;
            bool shouldNotify = false;
            double notifyFlashed = 0.0;
            double notifySmoked = 0.0;
            bool notifyActive = true;
            string? notifyRawFlashed = null;

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
                        return false;
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

                    // Always enable Black Flash even when dead (health == 0) or spectating others
                    double effectiveFlashed = flashedFraction;
                    double effectiveSmoked = smokedFraction;
                    bool isActive = true;

                    _lastFlashed = effectiveFlashed;
                    _lastSmoked = effectiveSmoked;
                    _lastFlashUpdateTime = DateTime.UtcNow;

                    notifyFlashed = effectiveFlashed;
                    notifySmoked = effectiveSmoked;
                    notifyActive = isActive;
                    notifyRawFlashed = rawFlashed;
                    shouldNotify = true;
                }
                catch (JsonException) { }
                catch (Exception ex)
                {
                    Program.Log($"Cs2GsiListener Parse error: {ex.Message}");
                }
            }

            if (shouldNotify)
            {
                state = new GsiState(notifyFlashed, notifySmoked, notifyActive, notifyRawFlashed);
            }
            return shouldNotify;
        }

        private void OnWatchdogTick(object? state)
        {
            bool needReset = false;
            lock (_syncLock)
            {
                if (_lastFlashed > 0.001 || _lastSmoked > 0.001)
                {
                    if ((DateTime.UtcNow - _lastFlashUpdateTime).TotalSeconds > 2.0)
                    {
                        _lastFlashed = 0.0;
                        _lastSmoked = 0.0;
                        needReset = true;
                    }
                }
            }

            if (needReset)
            {
                StateUpdated?.Invoke(0.0, 0.0, false, null);
            }
        }

        public void Stop()
        {
            try
            {
                var session = _session;
                if (session != null)
                {
                    // Event is left to the GC: the apply thread may still be waking from it
                    session.Running = false;
                    session.StateReady.Set();
                    // Wakes the receive thread out of Select; it closes its client connections on the way out
                    session.ListenSocket.Close();
                }
            }
            catch { }
            finally
            {
                _session = null;
            }
        }

        public void Dispose()
        {
            if (_isDisposed) return;
            _isDisposed = true;
            _watchdogTimer.Dispose();
            Stop();
        }
    }
}
