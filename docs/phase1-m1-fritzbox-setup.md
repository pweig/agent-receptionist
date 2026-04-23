# Phase 1 — M1 Runbook: FritzBox + Asterisk Setup

This runbook covers the hands-on steps for M1 Task 1 of the [Phase 1 Build Plan](phase1-build-plan.md): get Asterisk registered as an internal IP phone on the FritzBox 7362 SL, so incoming PSTN calls reach Asterisk.

**Environment assumed:**
- FritzBox 7362 SL at `192.168.178.1` (default)
- Dev Mac at `192.168.178.97` on the same LAN (verify with: `docker logs agent-asterisk | grep "received="` — use the IP shown there, not `ipconfig`)
- Numbers available on FritzBox: `6190556` (Telekom), `38534` (Telekom), `999999999` (Poivy — leave alone)
- Docker 29.x installed

All commits for this milestone live under [services/telephony/](../services/telephony/).

---

## Step 1 — Create an internal IP phone on the FritzBox

1. Browse to `http://fritz.box` and log in with your FritzBox admin password.
2. Navigate: **Telefonie → Telefoniegeräte → Neues Gerät einrichten**.
3. Choose **Telefon (mit und ohne Anrufbeantworter)**, click **Weiter**.
4. Choose **LAN/WLAN (IP-Telefon)**, click **Weiter**.
5. **Name:** `receptionist` (this label only appears in the FritzBox UI).
6. **Anmeldedaten:** FritzBox generates or lets you set a `Benutzername` and `Kennwort`. Set them to something memorable — these go into `.env` next step. Suggested:
   - `Benutzername: receptionist`
   - `Kennwort: <generate a strong password, ≥ 16 chars>`
   - Save these somewhere safe — you cannot read the password back from the FritzBox later, only reset it.
7. **Rufnummer zuweisen (ausgehend):** select **one of the Telekom numbers** — recommend `38534` for the agent so regular calls to `6190556` still ring the normal phones.
8. **Rufnummern (eingehend):** tick **only** the same number (`38534`). Untick all others.
9. Click **Weiter → Übernehmen**. FritzBox shows a summary.

> **Reboot-sometimes-needed quirk:** if later steps can't register, reboot the FritzBox once. This is a known 7362 SL behaviour after adding a new IP phone.

---

## Step 2 — Configure the Asterisk `.env`

```bash
cd /Users/D026233/dev/agent-receptionist/services/telephony
cp .env.example .env
```

Edit `.env`:

```
FRITZBOX_HOST=192.168.178.1
FRITZBOX_USER=receptionist
FRITZBOX_PASS=<the password you set in Step 1.6>
EXTERNAL_IP=192.168.178.97
```

**Important:** `EXTERNAL_IP` must be the IP that Docker actually routes outbound traffic through — not necessarily what `ipconfig getifaddr en0` returns. macOS can have multiple IPs on the same interface (aliases), and `ipconfig` may return one that Docker doesn't use. The authoritative way to find it:

```bash
# Start Asterisk, wait for registration, then:
docker logs agent-asterisk 2>&1 | grep "received=" | tail -1
# The IP after received= is what FritzBox sees — use THAT as EXTERNAL_IP
```

---

## Step 3 — Build and start Asterisk

```bash
cd /Users/D026233/dev/agent-receptionist/services/telephony
docker compose build
docker compose up
```

Leave it running in the foreground for now — you'll watch the log. Expected startup output includes:

```
== PJSIP Realtime: Loading...
== PJSIP Registrations loaded: 1
```

Within ~5 seconds you should see:

```
== Contact fritzbox/sip:192.168.178.1 has been created
-- Outbound registration attempt to 'sip:192.168.178.1' with 'From: sip:receptionist@192.168.178.1'
== Outbound registration successful
```

If you see `Outbound registration successful` — **M1 Task 1 is done.**

---

## Step 4 — Verify from the Asterisk CLI

In another terminal:

```bash
docker exec -it agent-asterisk asterisk -rvvv
```

At the `*CLI>` prompt:

```
*CLI> pjsip show registrations
```

Expected output (abbreviated):

```
 <Registration/ServerURI..............................>  <Auth..........>  <Status.......>
 <Contact..............................................................................>
==========================================================================================

 fritzbox-reg/sip:192.168.178.1                           fritzbox-auth     Registered
```

If `Status` shows `Registered`, registration is healthy. Other states to recognize:

| Status | Meaning | Fix |
|---|---|---|
| `Unregistered` | Asterisk hasn't tried yet | Wait 5 s |
| `Rejected` | FritzBox rejected auth | Wrong password, re-check `.env`; FritzBox passwords are case-sensitive |
| `No Authentication` | FritzBox didn't ask for auth | Username wrong or IP phone not enabled in FritzBox |

Also inspect the endpoint:

```
*CLI> pjsip show endpoint fritzbox
```

---

## Step 5 — Smoke test with a real call

With Asterisk registered and running, call `38534` (or whichever number you dedicated) from any phone — mobile, landline, another extension.

Expected behaviour:
1. The call rings.
2. The agent answers within one ring.
3. You hear Asterisk's built-in `hello-world` sample ("Hello world!" in English).
4. The call hangs up.

On the Asterisk console you should see:

```
-- Executing [38534@incoming:1] NoOp("PJSIP/fritzbox-...", "Incoming call from 017...") in new stack
-- Executing [38534@incoming:2] Answer(...)
-- <PJSIP/fritzbox-...> Playing 'hello-world.gsm' (language 'en')
-- Executing [38534@incoming:5] Hangup(...)
```

If you hear the greeting end-to-end, **the SIP + RTP path works**. This is the gate into M1 Task 2 (wiring the AudioSocket bridge to Pipecat).

---

## Docker-on-Mac gotchas (validated in practice)

These four issues all had to be fixed before the FritzBox → Asterisk path worked. On a native Linux deployment (the M4 production host), none of these apply.

### 1. Debian `asterisk` package is missing on arm64
Debian 12 bookworm does not ship the `asterisk` binary package on `arm64`. The Dockerfile uses `ubuntu:24.04` instead — Ubuntu's `universe` repo has Asterisk 20 LTS on all architectures.

### 2. "Use kernel networking for UDP" must be enabled in Docker Desktop
Without it, Docker Desktop's userspace `vpnkit` proxy drops SIP response packets asymmetrically — outbound REGISTERs leave, the 401/200 replies never make it back to the container. Symptom: `Unregistered (exp. Ns ago)` in `pjsip show registrations`, no `Received SIP response` lines in the PJSIP log.

Enable: Docker Desktop → Settings → Resources → Network → **Use kernel networking for UDP**. Apply & Restart.

### 3. `local_net` in pjsip.conf must NOT include the LAN
The FritzBox is on `192.168.178.0/24`. From a host container's perspective, that LAN is "external" — Asterisk reaches it through the Docker bridge via NAT. If you add `192.168.178.0/24` to `local_net`, PJSIP treats FritzBox as on the same network as itself and skips `external_signaling_address` rewriting. The REGISTER then advertises the container's internal Docker IP (e.g. `172.19.0.2`) in `Via` and `Contact`, which FritzBox can't reach — registration silently fails.

Only list the container's real local networks:
```
local_net=172.16.0.0/12
local_net=10.0.0.0/8
```

### 4. Docker source-NATs inbound UDP to its gateway IP (192.168.65.1)
Even with kernel UDP networking, inbound INVITEs from FritzBox arrive at the container with a **source IP of `192.168.65.1`** (Docker Desktop's internal NAT gateway), not the real `192.168.178.1`. PJSIP's `identify` block must match both:
```
[fritzbox-identify]
type=identify
endpoint=fritzbox
match=${FRITZBOX_HOST}
match=192.168.65.0/24
```
Without this, Asterisk logs `No matching endpoint found - Failed to authenticate` and replies `401 Unauthorized` instead of routing the call to the `incoming` context.

---

## Other troubleshooting

**`docker compose up` fails with `port is already allocated`**
Something else is bound to UDP 5060. Find it with `sudo lsof -i UDP:5060`. Usually a previous Asterisk container — `docker rm -f agent-asterisk`.

**Registered, but FritzBox IP phone shows `Nicht angemeldet`**
The Contact header advertises the wrong IP. Check the PJSIP log (`pjsip set logger on`) — the `Contact:` line in the outbound REGISTER must show your Mac's LAN IP, not `172.x.x.x`. If it shows `172.x`, gotcha #3 above applies.

**One-way audio during the smoke test**
RTP ports are not published or `external_media_address` doesn't match the Mac's actual LAN IP. Verify:
- `docker port agent-asterisk` includes `10000-10100/udp`
- `ipconfig getifaddr en0` matches `EXTERNAL_IP` in `.env`

**Can't hear `hello-world` but call connects**
Sounds package missing — `asterisk-core-sounds-en` must be in the Dockerfile `apt-get install` line. It already is; if you see this on a custom build, re-check.

---

## What's next (M1 Task 2)

Once Step 5 passes:

1. Replace the `Playback(hello-world)` line in [extensions.conf](../services/telephony/asterisk/etc/extensions.conf) with an `AudioSocket()` application call.
2. Add an `AsteriskAudioSocketTransport` to `services/receptionist/` that accepts the TCP stream, upsamples 8 kHz → 16 kHz for Whisper, and downsamples TTS back to 8 kHz.
3. Add a `TRANSPORT=sip|webrtc` env var to `main.py` so both paths coexist during dev.

That's scoped as a separate PR. Keep this one about getting the phone to ring.
