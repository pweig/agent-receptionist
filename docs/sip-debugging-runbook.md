# SIP Path Debugging Runbook

This runbook covers how to diagnose and fix problems in the Phase 1 telephony path:

```
Mobile/Landline caller
  │  PSTN
  ▼
FritzBox 7362 SL  (192.168.178.1)
  │  SIP / G.711 a-law over LAN
  ▼
Asterisk in Docker  (container: 172.19.0.2, host port 5060/udp)
  │  AudioSocket TCP
  ▼
Pipecat SIP listener  (host Mac, port 8089/tcp)
  │  pipeline
  ▼
VAD → Whisper STT → LLM (Ollama) → Piper TTS
```

See [architecture.adoc](architecture.adoc) for the full system context and pipeline frame flow.

The path has **five layers** that can each fail independently:

| Layer | What can break | First symptom |
|---|---|---|
| 1. SIP registration | Asterisk not registered with FritzBox | Phone rings, no answer |
| 2. SIP signaling | INVITE not reaching Asterisk, or wrong endpoint matched | Same — no answer |
| 3. RTP audio | Call connects, one- or two-way audio missing | Call connects, silence |
| 4. AudioSocket TCP | Pipeline not reached | Call answers, agent silent |
| 5. Pipecat pipeline | Model load failure, LLM timeout, crash | Agent connects then drops |

Work through the layers in order — a fault at layer 1 makes layers 2–5 untestable.

---

## 0. 60-second health check

Run this every time you sit down to test:

```bash
# 1. Is the Asterisk container running?
docker ps --filter name=agent-asterisk --format "{{.Status}}"
# Expected: "Up X minutes"

# 2. Is Asterisk registered with FritzBox?
docker exec agent-asterisk asterisk -rx "pjsip show registrations"
# Expected: fritzbox-reg/sip:192.168.178.1  →  Registered

# 3. Is Pipecat listening for AudioSocket connections?
lsof -i :8089 | grep LISTEN
# Expected: two lines — one IPv4 (*:8089), one IPv6 (*:8089)

# 4. Is Ollama running?
curl -s http://localhost:11434/api/tags | python3 -m json.tool | grep name
# Expected: your model name (e.g. "qwen2.5:14b") in the list
```

If all four pass, the system should be operational. Any failure goes straight to the relevant section below.

---

## Layer 1 — SIP registration

### Check registration status

```bash
docker exec agent-asterisk asterisk -rx "pjsip show registrations"
```

What you're reading: Asterisk sends a SIP REGISTER to FritzBox every ~5 minutes. `pjsip show registrations` shows the last known state. The status column tells you whether the most recent REGISTER was accepted.

| Status | Cause | Fix |
|---|---|---|
| `Registered` | All good | — |
| `Unregistered` | Container just started; hasn't tried yet | Wait 10 s, re-run |
| `Rejected` | Wrong password | Re-check `FRITZBOX_PASS` in `services/telephony/.env` |
| `No Authentication` | Wrong username, or IP phone not created in FritzBox | Re-check `FRITZBOX_USER`; verify the IP phone exists in FritzBox UI (Telefonie → Telefoniegeräte) |
| `Timeout` | Network unreachable | Check Docker networking (see Layer 1b) |

### Check that Asterisk can reach FritzBox

```bash
docker exec agent-asterisk asterisk -rx "pjsip set logger on"
docker logs -f agent-asterisk 2>&1 | grep -E "REGISTER|200 OK|401|received="
# Dial or wait for the next REGISTER heartbeat (~60 s)
```

Why: PJSIP logger prints all SIP messages to the container log. A `200 OK` to a REGISTER confirms FritzBox accepted us. A `401 Unauthorized` means credentials are wrong. **No response at all** means the REGISTER never got out — Docker networking is broken.

Look for `received=` in the FritzBox 200 OK response:

```
Via: SIP/2.0/UDP 192.168.178.97:5060;received=192.168.178.97;rport=...
```

The IP after `received=` is **the address FritzBox actually sees packets from**. This MUST match `EXTERNAL_IP` in `services/telephony/.env`. If they differ, RTP will be sent to the wrong host (silent calls — see Layer 3).

To find the correct `EXTERNAL_IP`:
```bash
# Check ALL IPs on the Mac's LAN interface (there can be multiple)
ifconfig en0 | grep "inet "
# e.g. returns both 192.168.178.97 and 192.168.178.197 (aliases)
# The received= field shows which one Docker routes through
```

If `EXTERNAL_IP` is wrong, fix it and restart:
```bash
# Edit services/telephony/.env → change EXTERNAL_IP=<correct ip>
cd services/telephony && docker compose down && docker compose up -d
```

### "Use kernel networking for UDP" check

If REGISTER messages go out but no response is ever logged, the Docker Desktop UDP proxy is dropping replies. This is the most common first-time setup failure.

```bash
# Verify the setting is ON: Docker Desktop → Settings → Resources → Network
# There is no CLI command to check this — you must use the UI.
```

Symptom: `pjsip show registrations` shows `Timeout` or stays at `Unregistered` for > 30 s even though the REGISTER was sent.

Fix: Docker Desktop → Settings → Resources → Network → enable **Use kernel networking for UDP** → Apply & Restart Docker.

---

## Layer 2 — SIP signaling (call routing)

### Does the call reach Asterisk at all?

Enable verbose logging, then place a test call:

```bash
docker exec agent-asterisk asterisk -rx "core set verbose 5"
docker logs -f agent-asterisk 2>&1 | grep -E "Executing|INVITE|NoOp|Answer|Hangup|No matching endpoint"
```

**Good** (call reaches dialplan):
```
-- Executing [s@incoming:1] NoOp("PJSIP/fritzbox-00000001", "Incoming call from 0176...") in new stack
-- Executing [s@incoming:2] Answer(...)
```

**Bad** (call not matching endpoint):
```
-- No matching endpoint found for 'sip:receptionist@192.168.178.97'
-- Failed to authenticate
```

If you see "No matching endpoint found", the `fritzbox-identify` block in `pjsip.conf.tmpl` is not matching the inbound packet's source IP.

### Why calls land on extension `s` instead of `_X.`

FritzBox routes incoming PSTN calls to the registered SIP user's username string (e.g. `receptionist`), not to a numeric DID. The `_X.` pattern only matches numeric extensions. Both the `_X.` and `s` patterns in `extensions.conf` route to AudioSocket — this is intentional.

To verify which extension was matched:
```bash
docker logs agent-asterisk 2>&1 | grep "Executing \[.*@incoming:1\]" | tail -5
```
You will see either `[s@incoming:1]` (username routing) or `[38534@incoming:1]` (if FritzBox ever sends a DID). Both are correct.

### PJSIP identify — Docker source-NAT problem

Docker Desktop source-NATs inbound UDP from FritzBox, so the packet arrives at the container with source IP `192.168.65.1` (Docker's internal gateway), not `192.168.178.1` (FritzBox). The `fritzbox-identify` block handles this:

```ini
[fritzbox-identify]
type=identify
endpoint=fritzbox
match=192.168.178.1        ; real FritzBox IP (Linux deployment)
match=192.168.65.0/24      ; Docker Desktop NAT range (Mac dev)
```

If you change the `192.168.65.0/24` range and calls stop arriving, restore it. To confirm what source IP the container sees:

```bash
docker exec agent-asterisk tcpdump -n -i any udp port 5060 -c 5 2>&1
# Look at the source IP of any SIP packet arriving from FritzBox
```

---

## Layer 3 — RTP audio (call connects but silence)

RTP is the audio stream. It is completely separate from SIP signaling. A call can be signaled correctly (you hear ringing, the call "connects") but have broken audio because RTP packets go to the wrong address.

### Confirm RTP is leaving the container

During an active call:
```bash
docker exec agent-asterisk tcpdump -n -i any udp and host 192.168.178.1 -c 20 2>&1
```

Expected output: a stream of outbound UDP packets at ~50 packets/second (one every 20 ms):
```
12:08:50.023760 eth0  Out IP 172.19.0.2.15110 > 192.168.178.1.7106: UDP, length 172
12:08:50.048045 eth0  Out IP 172.19.0.2.15110 > 192.168.178.1.7106: UDP, length 172
```

Length 172 = 12 bytes RTP header + 160 bytes G.711 a-law payload (20 ms at 8 kHz). This is the correct RTP frame size.

If you see **no packets** or only inbound packets (from FritzBox), Asterisk is not sending audio.

If you see outbound packets but the caller still hears nothing, the IP in the SDP is wrong (see EXTERNAL_IP above), or FritzBox is rejecting the audio because the source NAT port doesn't match expectations.

### Confirm what IP Asterisk advertises in SDP

Enable PJSIP logging and look at the `200 OK` response to an incoming INVITE:

```bash
docker exec agent-asterisk asterisk -rx "pjsip set logger on"
docker logs -f agent-asterisk 2>&1 | grep -A 20 "Transmitting SIP response"
```

In the SDP section, look for:
```
o=- 4873310 4873312 IN IP4 192.168.178.97   ← must be EXTERNAL_IP
...
c=IN IP4 192.168.178.97                      ← must be EXTERNAL_IP
m=audio 15110 RTP/AVP 8                      ← port must be in 10000-20000
```

If these show `172.19.0.2` (the container's internal IP), the `external_media_address` setting in `pjsip.conf.tmpl` is not being applied. This usually means `EXTERNAL_IP` env var is empty or `local_net` in pjsip.conf includes the LAN range.

### RTP port range

Asterisk allocates RTP ports from `rtpstart=10000` to `rtpend=20000` (set in `/etc/asterisk/rtp.conf` inside the container). The `docker-compose.yml` publishes `10000-10100/udp`, which is enough for 50 simultaneous calls. If you need more, widen the published range.

To confirm what port Asterisk assigned:
```bash
# Look at the "Strict RTP learning" log line after a call arrives
docker logs agent-asterisk 2>&1 | grep "Strict RTP"
# e.g.: Strict RTP learning after remote address set to: 192.168.178.1:7126
```

---

## Layer 4 — AudioSocket TCP bridge

AudioSocket is a lightweight binary framing protocol over TCP. Asterisk connects to Pipecat as a TCP client; Pipecat listens as the server.

For the protocol details see the docstring at the top of [services/receptionist/audiosocket_transport.py](../services/receptionist/audiosocket_transport.py).

### Is Pipecat listening?

```bash
lsof -i :8089 | grep LISTEN
```

Expected — two lines, one IPv4 one IPv6:
```
Python  28605 ...  IPv4  ...  TCP *:8089 (LISTEN)
Python  28605 ...  IPv6  ...  TCP *:8089 (LISTEN)
```

**Both IPv4 and IPv6 are required.** `host.docker.internal` can resolve to either an IPv4 or IPv6 address depending on DNS query order. If Pipecat only listens on IPv4 and Asterisk connects via IPv6, the connection is refused silently and the caller hears dead air.

If only one line appears, restart Pipecat:
```bash
pkill -f "services.receptionist.main"
TRANSPORT=sip nohup .venv/bin/python -m services.receptionist.main > /tmp/pipecat-sip.log 2>&1 & disown
until lsof -i :8089 2>/dev/null | grep -q LISTEN; do sleep 2; done
lsof -i :8089 | grep LISTEN
```

### Can the container reach port 8089?

```bash
docker exec agent-asterisk bash -c \
  "timeout 3 bash -c 'echo > /dev/tcp/host.docker.internal/8089' && echo REACHABLE || echo UNREACHABLE"
```

If `UNREACHABLE`: Pipecat is not running, or the Docker network changed. Check `lsof -i :8089` on the host and restart Pipecat if needed.

If `REACHABLE`: the TCP path works. If AudioSocket still doesn't connect during a real call, the issue is likely that `host.docker.internal` resolves to IPv6 and Pipecat's listener needs both families (see above).

### Did Pipecat receive the AudioSocket connection?

```bash
grep "AudioSocket\|PipelineTask\|PipelineRunner" /tmp/pipecat-sip.log | grep -v write_audio_frame | tail -20
```

Each call should produce a pair of lines:
```
[AudioSocket] connection from ('127.0.0.1', 56509)
...
[AudioSocket] connection from ('127.0.0.1', 56509) closed
```

Connections from `127.0.0.1` are normal — Docker Desktop proxies container→host TCP connections through localhost. They are NOT localhost connections initiated from the Mac itself.

If a call happened (Asterisk log shows `Executing AudioSocket(...)`) but no connection line appears in the Pipecat log, the TCP connection was never accepted. Causes:
1. Pipecat not listening (fix above)
2. Pipecat listening on IPv4 only and Asterisk tried IPv6 (fix: restart Pipecat with both-family listener — see code in `_sip_server_main()` in `main.py`)
3. Caller hung up before the TCP handshake completed

### Reading the Pipecat log

```bash
tail -f /tmp/pipecat-sip.log
```

Key log lines and what they mean:

| Log line | Meaning |
|---|---|
| `AudioSocket listening on 0.0.0.0:8089` | Server started (IPv4 only — restart needed) |
| `[AudioSocket] connection from ('127.0.0.1', XXXXX)` | New call connected |
| `AudioSocket reader loop started` | Pipecat is reading audio from Asterisk |
| `AudioSocket call UUID: xxxxxxxx-...` | Asterisk sent the call UUID (first message on connect) |
| `AudioSocket wrote N frames (X bytes) @ 8000Hz` | TTS audio being sent to caller |
| `AudioSocket peer closed the connection` | Asterisk hung up (normal call end or caller hung up) |
| `AudioSocket HANGUP received` | Asterisk sent explicit hangup frame |
| `AudioSocket ERROR: ...` | Asterisk reported an error condition |

---

## Layer 5 — Pipecat pipeline

### Model loading delays (silent calls / caller hangs up)

On each new call, `_build_pipeline()` in `main.py` creates fresh instances of Whisper, Piper TTS, and the LLM service. Whisper model loading can take 5–20 seconds on first load (the OS page-cache warms it for subsequent calls, making them faster — but not instant).

During this loading window the call is connected, Asterisk is answering, but the caller hears silence. If the caller gives up before loading completes, Asterisk sends a BYE and AudioSocket terminates.

**Diagnosis:** check for a BYE received while AudioSocket was executing in the Asterisk log:

```bash
docker logs agent-asterisk 2>&1 | grep -A 5 "BYE" | grep -B 5 "exited non-zero"
```

If you see `Spawn extension (incoming, s, 5) exited non-zero` immediately after a BYE, the caller hung up during initialization.

**Workaround until M2 (planned pre-warming):** tell test callers to wait at least 15–20 seconds on the first call of a session. Subsequent calls are faster because the OS keeps model files in memory.

### Checking for pipeline errors

```bash
grep -E "ERROR|Exception|Traceback|CRITICAL" /tmp/pipecat-sip.log | tail -20
```

Common errors:

| Error | Cause | Fix |
|---|---|---|
| `Connection refused` to `localhost:11434` | Ollama not running | `ollama serve` in a terminal |
| `model not found: qwen2.5:14b` | Model not pulled | `ollama pull qwen2.5:14b` |
| `SOXRStreamAudioResampler` import error | soxr not installed | `pip install soxr` inside `.venv` |
| `CancelledError` | Normal pipeline shutdown on hangup | Not an error — expected |

### Is Ollama responding?

```bash
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin)['models']]"
# Expected: qwen2.5:14b (or whichever model is configured in settings.yaml)

# Smoke-test inference:
curl -s http://localhost:11434/api/generate \
  -d '{"model":"qwen2.5:14b","prompt":"Say hello","stream":false}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['response'])"
```

If Ollama responds but the LLM seems stuck during calls, check the model's response latency. On CPU-only hardware, `qwen2.5:14b` can take 5–15 s per response. The pipeline has a 5-minute idle timeout (`idle_timeout_secs=300` in `main.py`), so it won't crash from LLM latency alone — but the conversation will feel unusable.

---

## Full restart procedure

When in doubt, a clean restart of all components resolves most transient issues:

```bash
# 1. Stop Pipecat
pkill -f "services.receptionist.main" 2>/dev/null

# 2. Restart Asterisk (picks up any config changes from etc/)
cd /Users/D026233/dev/agent-receptionist/services/telephony
docker compose down && docker compose up -d

# 3. Wait for Asterisk to register
until docker exec agent-asterisk asterisk -rx "pjsip show registrations" 2>&1 | grep -q "Registered"; do
  sleep 3
done
echo "Asterisk registered"

# 4. Start Pipecat
cd /Users/D026233/dev/agent-receptionist
TRANSPORT=sip nohup .venv/bin/python -m services.receptionist.main > /tmp/pipecat-sip.log 2>&1 & disown

# 5. Wait for AudioSocket listener
until lsof -i :8089 2>/dev/null | grep -q LISTEN; do sleep 2; done
echo "Pipecat ready"

# 6. Verify both listeners are up (must see both IPv4 and IPv6 lines)
lsof -i :8089 | grep LISTEN
```

---

## Dialplan live reload (no container restart)

If you change `extensions.conf` on the host and need Asterisk to reload it without a full container restart:

```bash
# 1. Copy the updated file into the running container
docker cp services/telephony/asterisk/etc/extensions.conf agent-asterisk:/etc/asterisk/extensions.conf

# 2. Tell Asterisk to reload dialplan in-place
docker exec agent-asterisk asterisk -rx "dialplan reload"

# 3. Verify the new dialplan is active
docker exec agent-asterisk asterisk -rx "dialplan show incoming"
```

Why copy instead of just reload: the config volume mounts templates to `/etc/asterisk-templates/`, not to `/etc/asterisk/` directly. The entrypoint script renders them at container startup. A live reload reads from `/etc/asterisk/` — so you must copy the file into that path first. On a full container restart this is automatic.

---

## Known gotchas — quick reference

These are the specific issues discovered during M1. Each took significant time to diagnose. Check here before starting a deep investigation.

### G1 — Docker Desktop UDP proxy drops SIP replies
**Symptom:** REGISTER packets visible in tcpdump, but no response, `pjsip show registrations` stays `Unregistered`.
**Fix:** Docker Desktop → Settings → Resources → Network → **Use kernel networking for UDP** → Apply & Restart.

### G2 — `local_net` includes the LAN subnet
**Symptom:** Asterisk registers successfully, but SDP advertises the container's Docker IP (`172.19.0.2`) instead of the Mac's LAN IP. Callers hear nothing.
**Check:**
```bash
docker exec agent-asterisk cat /etc/asterisk/pjsip.conf | grep local_net
# Must NOT include 192.168.178.0/24
```
**Fix:** ensure only `172.16.0.0/12` and `10.0.0.0/8` are listed.

### G3 — Docker source-NATs inbound SIP to 192.168.65.x
**Symptom:** call rings, Asterisk logs `No matching endpoint found`.
**Fix:** ensure `match=192.168.65.0/24` is present in `[fritzbox-identify]` in `pjsip.conf.tmpl`.

### G4 — Wrong `EXTERNAL_IP` (alias vs. routing IP)
**Symptom:** call connects, Asterisk sends RTP, but caller hears silence.
**Diagnosis:** compare `EXTERNAL_IP` in `services/telephony/.env` with the `received=` field in a REGISTER 200 OK:
```bash
docker logs agent-asterisk 2>&1 | grep "received="
```
If they differ, update `EXTERNAL_IP` to match `received=`.
**Why:** macOS can have multiple IPs on `en0`. `ipconfig getifaddr en0` may return an alias; Docker uses a different IP for outbound routing.

### G5 — AudioSocket connects via IPv6, Pipecat on IPv4 only
**Symptom:** second+ calls hang silently; Asterisk executes AudioSocket app but Pipecat log shows no new connection.
**Diagnosis:**
```bash
docker exec agent-asterisk getent ahosts host.docker.internal
# If an IPv6 address appears, Asterisk may use it first
lsof -i :8089 | grep LISTEN
# Must show TWO lines (IPv4 + IPv6)
```
**Fix:** Pipecat's `_sip_server_main()` must use `host=""` (empty string) in `asyncio.start_server()` so it binds to all interfaces and both IP families. This is already the case in the current code — if you see only one LISTEN line, restart Pipecat.

### G6 — FritzBox routes to SIP username, not numeric DID
**Symptom:** calls land on extension `s` instead of `_X.` in the dialplan; this is correct behavior, not a bug.
**Why:** FritzBox sends the call to the SIP username string ("receptionist"), not a numeric called-party number. The dialplan has both `s` and `_X.` routes pointing to AudioSocket. If only `_X.` is defined, calls from FritzBox will ring indefinitely with no answer.

### G7 — Caller hangs up during model warm-up (silent first call)
**Symptom:** Asterisk answers, AudioSocket app starts, then BYE arrives while AudioSocket is executing; no Pipecat connection logged.
**Why:** Whisper model loading (5–20 s) happens after AudioSocket connects, during `StartFrame` propagation. Caller hears silence during this time.
**Workaround:** wait 15–20 s after the call connects before speaking. Subsequent calls in the same Pipecat session are faster.

---

## Useful one-liners — copy-paste reference

```bash
# Registration status
docker exec agent-asterisk asterisk -rx "pjsip show registrations"

# Active calls
docker exec agent-asterisk asterisk -rx "core show channels"

# Dialplan as loaded
docker exec agent-asterisk asterisk -rx "dialplan show incoming"

# Enable SIP message logging (shows INVITE/REGISTER/BYE text)
docker exec agent-asterisk asterisk -rx "pjsip set logger on"

# Capture UDP traffic to/from FritzBox (run, then place a call)
docker exec agent-asterisk tcpdump -n -i any udp and host 192.168.178.1 -c 30

# Capture SIP only (port 5060)
docker exec agent-asterisk tcpdump -n -i any udp port 5060 -c 20

# Check what IP Pipecat is listening on
lsof -i :8089 | grep LISTEN

# Test AudioSocket TCP reachability from container
docker exec agent-asterisk bash -c "timeout 3 bash -c 'echo > /dev/tcp/host.docker.internal/8089' && echo OK || echo FAIL"

# Check resolved DNS for host.docker.internal (both families)
docker exec agent-asterisk getent ahosts host.docker.internal

# Follow Pipecat log live
tail -f /tmp/pipecat-sip.log

# Check Pipecat log for errors only
grep -E "ERROR|Exception|CRITICAL" /tmp/pipecat-sip.log | tail -20

# Check Ollama model availability
curl -s http://localhost:11434/api/tags | python3 -c "import sys,json; [print(m['name']) for m in json.load(sys.stdin)['models']]"

# Check EXTERNAL_IP value currently in use
docker exec agent-asterisk cat /etc/asterisk/pjsip.conf | grep "external_media_address"

# Check received= IP that FritzBox sees (run after a REGISTER)
docker logs agent-asterisk 2>&1 | grep "received=" | tail -3
```
