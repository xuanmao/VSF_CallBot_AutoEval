"""CSCallBot bridge.

Per FreeSWITCH call, mod_audio_stream opens a WebSocket to /live/{uuid} and
streams 16-bit PCM mono @ 16 kHz binary frames upstream. We forward those
frames into a Gemini Live BIDI session, then send Gemini's audio back as
streamAudio JSON which mod_audio_stream injects directly into the channel's
write frame (patched build — write-frame injection).
"""

from __future__ import annotations

import audioop
import asyncio
import base64
import logging
import os
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types
import uvicorn

# --- config ---------------------------------------------------------------

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL   = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live")
VOICE   = os.getenv("GEMINI_VOICE", "Aoede")
PORT    = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

GEMINI_OUTPUT_RATE = 24_000
FS_PLAYBACK_RATE   = 8_000   # matches PCMU channel; .r8 write frames

_PROMPT_FILE = Path(__file__).parent / "agent_system_prompt.txt"
SYSTEM_INSTRUCTION = _PROMPT_FILE.read_text(encoding="utf-8").strip()

# --- app ------------------------------------------------------------------

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("cscallbot.bridge")

app    = FastAPI()
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

            up   = asyncio.create_task(_pump_caller_to_gemini(ws, session, call_uuid))
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
    """Forward FreeSWITCH binary audio frames to Gemini as 16 kHz PCM blobs."""
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

        text = msg.get("text")
        if text:
            log.info("call %s fs text frame: %s", call_uuid, text)


async def _pump_gemini_to_caller(
    ws: WebSocket, session: "genai.live.AsyncSession", call_uuid: str
) -> None:
    """Receive Gemini audio and inject it into the write frame via streamAudio."""
    resample_state = None

    # session.receive() ends after each turn_complete on some model versions;
    # wrap in while True to re-enter for every subsequent turn.
    while True:
        async for response in session.receive():
            server_content = getattr(response, "server_content", None)

            if server_content and server_content.interrupted:
                # Tell mod_audio_stream to clear its m_playback_buf so the
                # bot stops speaking mid-sentence. Also reset resampler state.
                await ws.send_json({"type": "killAudio"})
                resample_state = None
                log.debug("call %s interrupted -> killAudio", call_uuid)
                continue

            audio = _extract_audio(response)
            if audio is None:
                if server_content and getattr(server_content, "turn_complete", False):
                    log.info("call %s turn_complete", call_uuid)

                output_tx = _extract_output_transcript(response)
                if output_tx:
                    log.info("call %s BOT: %s", call_uuid, output_tx)

                input_tx = _extract_input_transcript(response)
                if input_tx:
                    log.info("call %s USER: %s", call_uuid, input_tx)

                continue

            # Resample Gemini 24 kHz → 8 kHz (PCMU channel rate).
            audio_8k, resample_state = audioop.ratecv(
                audio, 2, 1, GEMINI_OUTPUT_RATE, FS_PLAYBACK_RATE, resample_state
            )

            log.info("call %s → streamAudio %d bytes", call_uuid, len(audio_8k))

            # mod_audio_stream (patched) decodes this and pushes directly into
            # the channel's write frame — no files, no ESL round-trip.
            await ws.send_json({
                "type": "streamAudio",
                "data": {
                    "audioDataType": "raw",
                    "sampleRate":    FS_PLAYBACK_RATE,
                    "audioData":     base64.b64encode(audio_8k).decode("ascii"),
                },
            })

        log.info("call %s receive() ended — re-entering for next turn", call_uuid)


def _extract_audio(response: types.LiveServerMessage) -> bytes | None:
    if response.data:
        return response.data
    sc = getattr(response, "server_content", None)
    if not sc:
        return None
    mt = getattr(sc, "model_turn", None)
    if not mt:
        return None
    for part in mt.parts or []:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            return inline.data
    return None


def _extract_output_transcript(response: types.LiveServerMessage) -> str | None:
    sc = getattr(response, "server_content", None)
    if not sc:
        return None
    tx = getattr(sc, "output_transcription", None)
    return tx.text if tx and tx.text else None


def _extract_input_transcript(response: types.LiveServerMessage) -> str | None:
    sc = getattr(response, "server_content", None)
    if not sc:
        return None
    tx = getattr(sc, "input_transcription", None)
    return tx.text if tx and tx.text else None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=LOG_LEVEL.lower())
