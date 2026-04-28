# CSCallBot — FreeSWITCH × Gemini 3.1 Flash Live Voice Bot

A low-latency telephone voice agent that bridges a SIP softphone (or a real PSTN/SIP trunk)
to **Gemini 3.1 Flash Live** through **FreeSWITCH** and a thin Python WebSocket bridge.

The setup follows the architecture described in
[Gemini Live Part 1 — Building a low-latency telephone voice agent with FreeSWITCH and ADK
agents](https://discuss.google.dev/t/gemini-live-part-1-building-a-low-latency-telephone-voice-agent-with-freeswitch-and-adk-agents-powered-by-gemini-live/332641),
simplified to a two-service stack (FreeSWITCH + Bridge) so you can register a softphone and
talk to Gemini Live in a few minutes.

---

## 1. Architecture

```
                    SIP / RTP                   WebSocket (L16 PCM)              WebSocket (L16 PCM)
 ┌──────────────┐  ───────────►  ┌──────────────┐  ───────────►  ┌──────────────┐  ───────────►  ┌──────────────────┐
 │  Softphone   │                │  FreeSWITCH  │                │ Bridge (FastAPI) │             │  Gemini 3.1      │
 │ (Linphone /  │  ◄───────────  │ mod_audio_   │  ◄───────────  │  Python asyncio │  ◄────────  │  Flash Live API  │
 │  Zoiper)     │   audio out    │   stream     │   audio out    │ google-genai SDK │  audio out  │ (BIDI streaming) │
 └──────────────┘                └──────────────┘                └──────────────────┘             └──────────────────┘
        ▲                              ▲                                  ▲
        │ register as 1000             │ dialplan parks the call          │
        │                              │ and starts uuid_audio_stream     │
        │                              │                                  │
   tap "call 9196"               mod_audio_stream pumps raw L16     Bridge forwards caller
                                 audio to ws://bridge:8080/live/    audio to Gemini Live and
                                 <uuid>                             streams TTS audio back
```

**Audio path**

| Direction                   | Format     | Rate   | Channels | Carrier                          |
| --------------------------- | ---------- | ------ | -------- | -------------------------------- |
| Phone → FreeSWITCH          | PCMU/PCMA  | 8 kHz  | mono     | RTP                              |
| FreeSWITCH → Bridge (in)    | 16-bit PCM | 16 kHz | mono     | WebSocket binary frames          |
| Bridge → Gemini Live        | 16-bit PCM | 16 kHz | mono     | `audio/pcm;rate=16000` blobs     |
| Gemini Live → Bridge        | 16-bit PCM | 24 kHz | mono     | `audio/pcm;rate=24000` blobs     |
| Bridge → FreeSWITCH (out)   | 16-bit PCM | 24 kHz | mono     | `streamAudio` JSON (base64 PCM)  |
| FreeSWITCH → Phone          | PCMU/PCMA  | 8 kHz  | mono     | RTP (FreeSWITCH resamples)       |

FreeSWITCH does all transcoding/resampling between the softphone codec (PCMU @ 8 kHz) and the
linear 16 kHz / 24 kHz streams the bridge sees, so the bridge code stays codec-free.

**Components**

- **FreeSWITCH** with [`mod_audio_stream`](https://github.com/0x15c/mod_audio_stream) — opens a
  WebSocket per call and forwards raw L16 audio in both directions.
- **Bridge service** — FastAPI + `websockets` server that:
  1. Accepts the per-call WebSocket from FreeSWITCH (`/live/{uuid}`).
  2. Opens a BIDI streaming session to **Gemini 3.1 Flash Live** via `google-genai`.
  3. Pumps caller audio up, base64-wraps Gemini's audio reply into the `streamAudio` JSON
     envelope `mod_audio_stream` expects, and sends it back down the same socket.
- **Softphone** — any SIP UA (Linphone / Zoiper / MicroSIP) registered against FreeSWITCH as
  extension `1000`.

---

## 2. Repository layout

```
CSCallBot/
├── README.md
├── docker-compose.yml
├── .env.example
├── .gitignore
├── freeswitch/
│   ├── Dockerfile
│   └── conf/
│       ├── vars.xml
│       ├── autoload_configs/modules.conf.xml
│       ├── sip_profiles/internal.xml
│       ├── directory/default/1000.xml
│       └── dialplan/default.xml
├── bridge/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── main.py
└── scripts/
    └── make_softphone_creds.sh
```

---

## 3. Prerequisites

- Docker + Docker Compose (v2).
- A Google AI Studio API key with access to `gemini-3.1-flash-live` (or whichever Gemini Live
  model you want to point at — set via `GEMINI_LIVE_MODEL`).
- A SIP softphone on your laptop/phone:
  - **Linphone** (cross-platform, free) — recommended.
  - **Zoiper** (Win/Mac/iOS/Android).
  - **MicroSIP** (Windows).
- The host needs UDP ports `5060` (SIP) and `16384–16484` (RTP) reachable from the softphone.
  When everything runs on the same machine `127.0.0.1` is fine.

---

## 4. Setup

### 4.1 Configure environment

```bash
cp .env.example .env
# then edit .env and set:
#   GEMINI_API_KEY=...       # from https://aistudio.google.com/app/apikey
#   GEMINI_LIVE_MODEL=gemini-3.1-flash-live
#   SIP_EXT_1000_PASSWORD=change-me
#   FS_EXTERNAL_IP=auto-nat   # or your LAN IP if calling from another device
```

### 4.2 Build and start the stack

```bash
docker compose up --build
```

This brings up:

| Service       | Ports (host)                            | Purpose                                 |
| ------------- | --------------------------------------- | --------------------------------------- |
| `freeswitch`  | `5060/udp`, `5060/tcp`, `16384-16484/udp` | SIP signalling + RTP media              |
| `bridge`      | `8080/tcp`                              | WebSocket endpoint for `mod_audio_stream` |

Watch the logs — you should see FreeSWITCH register `1000@<host>` as soon as your softphone
connects, and the bridge will print `gemini live session opened` on the first call.

### 4.3 Register your softphone

Create one SIP account in your softphone with these settings:

| Field              | Value                              |
| ------------------ | ---------------------------------- |
| Username / User ID | `1000`                             |
| Auth user          | `1000`                             |
| Password           | value of `SIP_EXT_1000_PASSWORD`   |
| Domain / Realm     | `127.0.0.1` (or the host's LAN IP) |
| Proxy / Outbound   | `127.0.0.1:5060` (UDP)             |
| Transport          | UDP                                |

Once the account shows **Registered**, dial **`9196`** from the softphone. FreeSWITCH will
answer, attach `mod_audio_stream` to the channel, and the bridge will start a Gemini Live
session — say "hello" and you should hear the model reply.

> **Tip:** The dialplan also accepts `9979` (FreeSWITCH's built-in echo test) and `9664`
> (music-on-hold), useful for verifying audio plumbing before you involve Gemini.

---

## 5. How a call flows

1. Softphone INVITEs `1000@host` → FreeSWITCH; FreeSWITCH proxies the leg into the dialplan.
2. The `9196` extension answers, exports `STREAM_PLAYBACK=1`, and runs:
   ```
   uuid_audio_stream <uuid> start ws://bridge:8080/live/<uuid>?caller_id=<num> mono 16000
   ```
3. `mod_audio_stream` opens a WebSocket to the bridge and starts pushing 16 kHz mono L16
   binary frames.
4. The bridge opens a `client.aio.live.connect(model=GEMINI_LIVE_MODEL, ...)` session and:
   - forwards every binary frame as a `Blob(mime_type="audio/pcm;rate=16000")`,
   - receives Gemini's audio chunks (24 kHz mono L16),
   - base64-encodes each chunk into:
     ```json
     {
       "type": "streamAudio",
       "data": {
         "audioDataType": "raw",
         "sampleRate": 24000,
         "audioData": "<base64 PCM>"
       }
     }
     ```
   - sends that JSON back over the same WebSocket.
5. `mod_audio_stream` decodes the base64 payload and plays it on the call leg, resampling to
   the softphone's codec automatically.
6. When the caller hangs up, FreeSWITCH closes the WebSocket; the bridge cancels its Gemini
   session and frees the slot.

---

## 6. Customising the agent

The bridge's system instruction lives in `bridge/main.py` (`SYSTEM_INSTRUCTION`). Edit it to
change persona, language, or guardrails, then `docker compose restart bridge`.

To add tool calls (Google Search, function tools, etc.), pass a `tools=[...]` list into the
`LiveConnectConfig` in `bridge/main.py` — the `google-genai` SDK accepts the same tool spec
as the non-live API.

---

## 7. Troubleshooting

| Symptom                                              | Likely cause / fix                                                                 |
| ---------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Softphone says **403 Forbidden** on register         | Wrong `SIP_EXT_1000_PASSWORD`, or you edited `directory/default/1000.xml` and forgot to restart FreeSWITCH. |
| Call connects but you hear silence one-way           | NAT — set `FS_EXTERNAL_IP` to your real LAN IP in `.env` and `docker compose up -d --force-recreate freeswitch`. |
| Bridge logs `mod_audio_stream` connect then immediate close | The dialplan's `ws://bridge:8080/...` URL isn't reachable from the FreeSWITCH container. Both services are on the same Docker network in `docker-compose.yml` — don't change the service names. |
| Gemini returns `PERMISSION_DENIED`                   | `GEMINI_API_KEY` missing or your key doesn't have Live API access yet.             |
| Robotic / chipmunk voice on playback                 | `sampleRate` in the `streamAudio` envelope doesn't match what Gemini actually sent. Keep it at `24000` unless you change the model's output rate. |

FreeSWITCH CLI is handy while debugging:

```bash
docker compose exec freeswitch fs_cli -x "sofia status profile internal reg"
docker compose exec freeswitch fs_cli -x "show channels"
```

---

## 8. References

- Original article — [Gemini Live Part 1: Building a low-latency telephone voice agent with FreeSWITCH and ADK agents](https://discuss.google.dev/t/gemini-live-part-1-building-a-low-latency-telephone-voice-agent-with-freeswitch-and-adk-agents-powered-by-gemini-live/332641)
- [`mod_audio_stream`](https://github.com/0x15c/mod_audio_stream) — FreeSWITCH WebSocket audio module
- [Gemini Live API docs](https://ai.google.dev/gemini-api/docs/live)
- [`google-genai` Python SDK](https://github.com/googleapis/python-genai)
