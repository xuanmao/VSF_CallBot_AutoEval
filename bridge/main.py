"""CSCallBot bridge.

Per FreeSWITCH call, mod_audio_stream opens a WebSocket to /live/{uuid} and
streams 16-bit PCM mono @ 16 kHz binary frames upstream. We forward those
frames into a Gemini Live BIDI session.

For audio playback back to the caller, mod_audio_stream's open-source forks
fire a FreeSWITCH event instead of injecting frames directly. We bypass that
by writing audio to a shared volume and triggering playback via FreeSWITCH's
event socket (ESL) using uuid_broadcast.
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
MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live")
VOICE = os.getenv("GEMINI_VOICE", "Aoede")
PORT = int(os.getenv("PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

GEMINI_OUTPUT_RATE = 24_000
FS_PLAYBACK_RATE = 8_000    # 8 kHz → .r8 format; matches the PCMU channel rate

# ESL connection to FreeSWITCH for uuid_broadcast playback.
FS_ESL_HOST = os.getenv("FS_ESL_HOST", "freeswitch-host")
FS_ESL_PORT = int(os.getenv("FS_ESL_PORT", "8021"))
FS_ESL_PASSWORD = os.getenv("FS_ESL_PASSWORD", "ClueCon")

# Shared volume path (mounted in both bridge and freeswitch containers).
SHARED_AUDIO_DIR = Path(os.getenv("SHARED_AUDIO_DIR", "/shared-audio"))

# Flush audio to file and broadcast every N bytes (≈500 ms at 8 kHz 16-bit).
FLUSH_BYTES = FS_PLAYBACK_RATE * 2 * 500 // 1000  # 8000 bytes

_PROMPT_FILE = Path(__file__).parent / "agent_system_prompt.txt"
SYSTEM_INSTRUCTION = _PROMPT_FILE.read_text(encoding="utf-8").strip()

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


# --- ESL helpers ----------------------------------------------------------

async def _esl_connect() -> tuple:
    """Connect and authenticate to FreeSWITCH event socket."""
    reader, writer = await asyncio.open_connection(FS_ESL_HOST, FS_ESL_PORT)
    await reader.readuntil(b"\n\n")                          # auth/request
    writer.write(f"auth {FS_ESL_PASSWORD}\n\n".encode())
    await writer.drain()
    reply = await reader.readuntil(b"\n\n")
    if b"+OK" not in reply:
        writer.close()
        raise RuntimeError(f"ESL auth failed: {reply!r}")
    log.info("ESL connected to %s:%s", FS_ESL_HOST, FS_ESL_PORT)
    return reader, writer


async def _esl_drain(reader, call_uuid: str) -> None:
    """Continuously read and discard ESL API responses to prevent buffer buildup."""
    try:
        while True:
            await reader.readuntil(b"\n\n")
    except Exception:
        log.debug("call %s ESL drain ended", call_uuid)


async def _esl_broadcast(writer, uuid: str, file_path: str) -> None:
    """Tell FreeSWITCH to play a file on the caller's leg."""
    try:
        writer.write(f"api uuid_broadcast {uuid} {file_path} aleg\n\n".encode())
        await writer.drain()
    except Exception as e:
        log.warning("call %s ESL broadcast failed: %s", uuid, e)


async def _esl_break(writer, uuid: str) -> None:
    """Stop any currently playing audio on the caller's leg."""
    try:
        writer.write(f"api uuid_break {uuid}\n\n".encode())
        await writer.drain()
    except Exception as e:
        log.warning("call %s ESL break failed: %s", uuid, e)


# --- FastAPI endpoints ----------------------------------------------------

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


# --- audio pumps ----------------------------------------------------------

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
    """Receive Gemini audio and play it to the caller via ESL uuid_broadcast."""
    # Try to establish ESL connection for playback.
    esl_writer = None
    esl_drain_task = None
    try:
        _esl_reader, esl_writer = await _esl_connect()
        esl_drain_task = asyncio.create_task(_esl_drain(_esl_reader, call_uuid))
    except Exception as e:
        log.error("call %s ESL unavailable (%s) — caller will hear silence", call_uuid, e)

    SHARED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # File extension matches FS_PLAYBACK_RATE: .r8=8kHz, .r16=16kHz, .r32=32kHz
    ext = f".r{FS_PLAYBACK_RATE // 1000}"

    audio_buffer = bytearray()
    resample_state = None
    chunk_index = 0
    written_files: list = []

    async def flush(discard: bool = False) -> None:
        nonlocal audio_buffer, chunk_index
        if discard or not audio_buffer:
            audio_buffer = bytearray()
            return
        file_path = SHARED_AUDIO_DIR / f"{call_uuid}_{chunk_index}{ext}"
        file_path.write_bytes(audio_buffer)
        written_files.append(file_path)
        chunk_index += 1
        audio_buffer = bytearray()
        if esl_writer:
            await _esl_broadcast(esl_writer, call_uuid, str(file_path))
            log.debug("call %s broadcast %s", call_uuid, file_path.name)

    try:
        # session.receive() ends after each turn_complete on some model versions.
        # Wrap in while True so we re-enter it for every subsequent turn.
        while True:
            async for response in session.receive():
                server_content = getattr(response, "server_content", None)

                if server_content and server_content.interrupted:
                    await flush(discard=True)
                    if esl_writer:
                        await _esl_break(esl_writer, call_uuid)
                    log.debug("call %s interrupted -> break", call_uuid)
                    continue

                audio = _extract_audio(response)
                if audio is None:
                    if server_content and getattr(server_content, "turn_complete", False):
                        log.info("call %s turn_complete — flushing", call_uuid)
                        await flush()

                    output_tx = _extract_output_transcript(response)
                    if output_tx:
                        log.info("call %s BOT: %s", call_uuid, output_tx)

                    input_tx = _extract_input_transcript(response)
                    if input_tx:
                        log.info("call %s USER: %s", call_uuid, input_tx)

                    continue

                # Resample Gemini 24 kHz → 8 kHz for the .r8 file format.
                audio_8k, resample_state = audioop.ratecv(
                    audio, 2, 1, GEMINI_OUTPUT_RATE, FS_PLAYBACK_RATE, resample_state
                )
                audio_buffer.extend(audio_8k)

                # Flush every ~500 ms to keep latency low.
                if len(audio_buffer) >= FLUSH_BYTES:
                    await flush()

            log.info("call %s receive() ended — re-entering for next turn", call_uuid)
    finally:
        await flush()
        if esl_drain_task:
            esl_drain_task.cancel()
            with suppress(asyncio.CancelledError):
                await esl_drain_task
        if esl_writer:
            with suppress(Exception):
                esl_writer.close()
        # Delay cleanup so FreeSWITCH can finish playing queued broadcasts.
        async def _cleanup():
            await asyncio.sleep(30)
            for f in written_files:
                with suppress(Exception):
                    f.unlink()
        asyncio.create_task(_cleanup())


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


def _extract_input_transcript(response: types.LiveServerMessage) -> str | None:
    server_content = getattr(response, "server_content", None)
    if not server_content:
        return None
    transcript = getattr(server_content, "input_transcription", None)
    if transcript and transcript.text:
        return transcript.text
    return None


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level=LOG_LEVEL.lower())
