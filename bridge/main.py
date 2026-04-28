"""CSCallBot bridge.

Per FreeSWITCH call, mod_audio_stream opens a WebSocket to /live/{uuid} and
streams 16-bit PCM mono @ 16 kHz binary frames upstream. We forward those
frames into a Gemini Live BIDI session, then wrap each audio chunk Gemini
returns into the JSON envelope mod_audio_stream expects and send it back
down the same socket. Hangup closes the FS-side socket; we cancel the
Gemini session and exit.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from contextlib import suppress

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
import uvicorn

# --- config ---------------------------------------------------------------

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live")
VOICE = os.getenv("GEMINI_VOICE", "Aoede")
PORT = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Gemini Live's default output sample rate. mod_audio_stream needs to know
# what rate the PCM we hand it is at so it can resample to the channel codec.
GEMINI_OUTPUT_RATE = 24_000

SYSTEM_INSTRUCTION = (
    "You are CSCallBot, a friendly customer-service voice assistant reached "
    "over the phone. Keep replies short and conversational — one or two "
    "sentences at a time — because the caller hears you, not reads you. "
    "If you don't know something, say so plainly. Greet the caller as soon "
    "as the call connects."
)

# --- app ------------------------------------------------------------------

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("cscallbot.bridge")

app = FastAPI()
client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1beta"})

LIVE_CONFIG = types.LiveConnectConfig(
    response_modalities=[types.Modality.AUDIO],
    system_instruction=types.Content(
        parts=[types.Part(text=SYSTEM_INSTRUCTION)]
    ),
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
        )
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "model": MODEL}


@app.websocket("/live/{call_uuid}")
async def live(ws: WebSocket, call_uuid: str) -> None:
    await ws.accept()
    caller_id = ws.query_params.get("caller_id", "unknown")
    log.info("call %s connected (caller_id=%s)", call_uuid, caller_id)

    try:
        async with client.aio.live.connect(model=MODEL, config=LIVE_CONFIG) as session:
            log.info("call %s gemini live session opened", call_uuid)

            up = asyncio.create_task(_pump_caller_to_gemini(ws, session, call_uuid))
            down = asyncio.create_task(_pump_gemini_to_caller(ws, session, call_uuid))

            done, pending = await asyncio.wait(
                {up, down}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                exc = task.exception()
                if exc:
                    log.warning("call %s task error: %r", call_uuid, exc)
    except WebSocketDisconnect:
        log.info("call %s websocket disconnected", call_uuid)
    except Exception:
        log.exception("call %s bridge error", call_uuid)
    finally:
        with suppress(Exception):
            await ws.close()
        log.info("call %s closed", call_uuid)


async def _pump_caller_to_gemini(
    ws: WebSocket, session: "genai.live.AsyncSession", call_uuid: str
) -> None:
    """Forward FS binary audio frames to Gemini as 16 kHz PCM blobs."""
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            log.info("call %s caller disconnected", call_uuid)
            return

        data = msg.get("bytes")
        if data:
            await session.send_realtime_input(
                audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
            )
            continue

        # mod_audio_stream also sends a small JSON text frame with channel
        # events ("connect", "disconnect", interrupt acks, etc.). They're
        # informational — log at debug level and move on.
        text = msg.get("text")
        if text:
            log.debug("call %s fs text frame: %s", call_uuid, text)


async def _pump_gemini_to_caller(
    ws: WebSocket, session: "genai.live.AsyncSession", call_uuid: str
) -> None:
    """Receive Gemini audio chunks and push them back to FreeSWITCH."""
    async for response in session.receive():
        # Mid-utterance interruption from VAD: tell mod_audio_stream to stop
        # whatever it's currently playing so the model can speak again.
        if getattr(response, "server_content", None) and response.server_content.interrupted:
            await ws.send_json({"type": "killAudio"})
            log.debug("call %s interrupted -> killAudio", call_uuid)
            continue

        audio = _extract_audio(response)
        if audio is None:
            transcript = _extract_output_transcript(response)
            if transcript:
                log.debug("call %s gemini said: %s", call_uuid, transcript)
            continue

        await ws.send_json(
            {
                "type": "streamAudio",
                "data": {
                    "audioDataType": "raw",
                    "sampleRate": GEMINI_OUTPUT_RATE,
                    "audioData": base64.b64encode(audio).decode("ascii"),
                },
            }
        )


def _extract_audio(response: types.LiveServerMessage) -> bytes | None:
    if response.data:
        return response.data
    server_content = getattr(response, "server_content", None)
    if not server_content:
        return None
    model_turn = getattr(server_content, "model_turn", None)
    if not model_turn:
        return None
    for part in model_turn.parts or []:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            return inline.data
    return None


def _extract_output_transcript(response: types.LiveServerMessage) -> str | None:
    server_content = getattr(response, "server_content", None)
    if not server_content:
        return None
    transcript = getattr(server_content, "output_transcription", None)
    if transcript and transcript.text:
        return transcript.text
    return None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=LOG_LEVEL.lower())
