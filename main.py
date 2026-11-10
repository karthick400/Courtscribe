from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from pdf_generator import generate_pdf

try:
    import websockets
    from websockets.exceptions import WebSocketException
except ImportError:  # pragma: no cover - reported when the application starts
    websockets = None
    WebSocketException = RuntimeError


REPOSITORY_DIR = Path(__file__).resolve().parent
load_dotenv(REPOSITORY_DIR / "backend" / ".env")
load_dotenv(REPOSITORY_DIR / ".env")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

GNANI_STREAM_URL = os.getenv(
    "GNANI_STT_STREAM_URL", "wss://api.vachana.ai/stt/v3/stream"
)
DEFAULT_LANGUAGE_CODE = os.getenv("GNANI_LANGUAGE_CODE", "en-IN")
DEFAULT_MODEL = os.getenv("GNANI_MODEL", "prisma-v3.0")
DEFAULT_FORMAT = os.getenv("GNANI_FORMAT", "transcribe")
RECORDINGS_DIR = Path(os.getenv("RECORDINGS_DIR", "recordings")).resolve()
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="CourtScribe API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TranscriptSegment(BaseModel):
    speaker: str | None = None
    speakerId: str | None = None
    role: str | None = None
    name: str | None = None
    text: str


class PdfExportRequest(BaseModel):
    title: str = Field(default="CourtScribe Transcript", max_length=200)
    court_name: str = Field(default="CourtScribe Court", max_length=200)
    case_number: str = Field(default="", max_length=100)
    hearing_date: str = Field(default="", max_length=40)
    appearances: list[str] = Field(default_factory=list, max_length=50)
    transcript: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    recording_id: str | None = None


class DesktopExportRequest(BaseModel):
    recording_id: str
    format: Literal["pdf", "txt"]


class SessionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class SegmentUpdateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


def _api_key() -> str:
    key = os.getenv("gnaniAPI") or os.getenv("GNANI_API_KEY")
    if not key:
        raise RuntimeError("gnaniAPI or GNANI_API_KEY is not configured")
    return key


def _stream_headers(websocket: WebSocket) -> dict[str, str]:
    query = websocket.query_params
    headers = {
        "x-api-key-id": _api_key(),
        "language_code": query.get("language_code", DEFAULT_LANGUAGE_CODE),
        "model": query.get("model", DEFAULT_MODEL),
        "format": query.get("format", DEFAULT_FORMAT),
        "x-sample-rate": query.get("sample_rate", "16000"),
    }
    speaker_names = query.get("speaker_names")
    if speaker_names:
        try:
            json.loads(speaker_names)
        except json.JSONDecodeError as exc:
            raise ValueError("speaker_names must be valid JSON") from exc
        headers["speaker_names"] = speaker_names
    return headers


def _message_payload(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, bytes):
        raise ValueError("Gnani returned an unexpected binary message")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as exc:
        raise ValueError("Gnani returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Gnani returned an unexpected message")
    return payload


def _safe_name(value: str, fallback: str = "recording") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return (cleaned[:80] or fallback)


def _recording_folder(recording_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", recording_id):
        raise HTTPException(status_code=404, detail="Recording not found")
    folder = (RECORDINGS_DIR / recording_id).resolve()
    if folder.parent != RECORDINGS_DIR:
        raise HTTPException(status_code=404, detail="Recording not found")
    return folder


def _write_wav(path: Path, audio: bytes) -> None:
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(audio)


def _merge_adjacent_segments(
    segments: list[TranscriptSegment],
) -> list[TranscriptSegment]:
    merged: list[TranscriptSegment] = []
    for segment in segments:
        if (
            merged
            and merged[-1].speaker == segment.speaker
            and merged[-1].speakerId == segment.speakerId
            and merged[-1].role == segment.role
            and merged[-1].name == segment.name
        ):
            merged[-1].text = f"{merged[-1].text} {segment.text}".strip()
        else:
            merged.append(segment)
    return merged


def _create_session(name: str) -> dict[str, Any]:
    display_name = _safe_name(name, "session")
    created_at = datetime.now(timezone.utc)
    session_id = (
        f"{created_at.strftime('%Y%m%d-%H%M%S')}-{display_name}-"
        f"{os.urandom(3).hex()}"
    )
    folder = _recording_folder(session_id)
    folder.mkdir(parents=True, exist_ok=False)
    metadata = {
        "id": session_id,
        "name": display_name,
        "created_at": created_at.isoformat(),
        "audio_file": "recording.wav",
        "pdf_file": "transcript.pdf",
        "segments": [],
    }
    (folder / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return metadata


def _save_recording(
    recording_id: str,
    display_name: str,
    audio: bytes,
    segments: list[TranscriptSegment],
) -> None:
    folder = _recording_folder(recording_id)
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / "metadata.json"
    existing_metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        existing_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    existing_audio = b""
    audio_path = folder / "recording.wav"
    if audio_path.is_file():
        with wave.open(str(audio_path), "rb") as previous:
            existing_audio = previous.readframes(previous.getnframes())
    combined_audio = existing_audio + audio
    _write_wav(audio_path, combined_audio)
    existing_segments = [
        TranscriptSegment(**segment)
        for segment in existing_metadata.get("segments", [])
    ]
    combined_segments = _merge_adjacent_segments(existing_segments + segments)
    pdf_request = PdfExportRequest(
        title=display_name,
        segments=combined_segments,
        transcript="\n".join(segment.text for segment in combined_segments),
    )
    (folder / "transcript.pdf").write_bytes(_pdf_content(pdf_request))
    metadata = {
        "id": recording_id,
        "name": existing_metadata.get("name", display_name),
        "created_at": existing_metadata.get(
            "created_at", datetime.now(timezone.utc).isoformat()
        ),
        "audio_file": "recording.wav",
        "pdf_file": "transcript.pdf",
        "segments": [segment.model_dump() for segment in combined_segments],
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _recording_list() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for folder in RECORDINGS_DIR.iterdir():
        metadata_path = folder / "metadata.json"
        if not folder.is_dir() or not metadata_path.is_file():
            continue
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping invalid recording metadata: %s", metadata_path)
            continue
        metadata["audio_url"] = f"/recordings/{folder.name}/audio"
        metadata["pdf_url"] = f"/recordings/{folder.name}/pdf"
        items.append(metadata)
    return sorted(items, key=lambda item: item.get("created_at", ""), reverse=True)


def _pdf_content(request: PdfExportRequest) -> bytes:
    return generate_pdf(
        title=request.title,
        court_name=request.court_name,
        case_number=request.case_number,
        hearing_date=request.hearing_date,
        appearances=request.appearances,
        segments=[segment.model_dump() for segment in request.segments],
        transcript=request.transcript,
    )


def _recording_pdf_request(recording_id: str) -> PdfExportRequest:
    folder = _recording_folder(recording_id)
    metadata_path = folder / "metadata.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        segments = [
            TranscriptSegment(**segment) for segment in metadata.get("segments", [])
        ]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Invalid recording metadata") from exc
    return PdfExportRequest(
        title=metadata.get("name", "CourtScribe Transcript"),
        court_name=metadata.get("court_name", "CourtScribe Court"),
        case_number=metadata.get("case_number", ""),
        hearing_date=metadata.get("hearing_date", ""),
        appearances=metadata.get("appearances", []),
        segments=segments,
        transcript="\n".join(segment.text for segment in segments),
    )


def _recording_filename(request: PdfExportRequest, recording_id: str, extension: str) -> str:
    case_number = _safe_name(request.case_number or request.title or recording_id, "UnknownCase")
    hearing_date = request.hearing_date.strip() or datetime.now().strftime("%Y-%m-%d")
    date = _safe_name(hearing_date.replace("/", "-"), "undated")
    return f"CourtScribe_{case_number}_{date}.{extension}"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions", status_code=201)
async def create_session(request: SessionCreateRequest) -> dict[str, Any]:
    try:
        metadata = _create_session(request.name)
    except OSError as exc:
        logger.exception("Could not create session folder")
        raise HTTPException(status_code=500, detail="Could not create session") from exc
    metadata["audio_url"] = f"/recordings/{metadata['id']}/audio"
    metadata["pdf_url"] = f"/recordings/{metadata['id']}/pdf"
    return metadata


@app.websocket("/ws/transcribe")
async def transcribe(websocket: WebSocket) -> None:
    await websocket.accept()
    if websockets is None:
        await websocket.send_json(
            {"type": "error", "message": "Install the 'websockets' package"}
        )
        await websocket.close(code=1011)
        return

    audio = bytearray()
    segments: list[TranscriptSegment] = []
    recording_id = websocket.query_params.get("session_id", "")
    try:
        folder = _recording_folder(recording_id)
    except HTTPException:
        await websocket.send_json({"type": "error", "message": "Create a session before recording."})
        await websocket.close(code=1008)
        return
    metadata_path = folder / "metadata.json"
    if not metadata_path.is_file():
        await websocket.send_json({"type": "error", "message": "Session not found."})
        await websocket.close(code=1008)
        return
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    display_name = str(metadata.get("name", "session"))
    current_speaker: dict[str, str | None] = {"name": "All speakers", "role": None}
    try:
        headers = _stream_headers(websocket)
        async with websockets.connect(GNANI_STREAM_URL, additional_headers=headers) as gnani:
            async def forward_audio() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        audio.extend(message["bytes"])
                        await gnani.send(message["bytes"])
                    elif message.get("text"):
                        try:
                            control = json.loads(message["text"])
                        except json.JSONDecodeError:
                            control = {}
                        if control.get("type") == "speaker":
                            current_speaker = {
                                "name": str(control.get("name") or "All speakers"),
                                "role": str(control.get("role") or "") or None,
                            }
                        elif control.get("type") == "stop":
                            await gnani.close()
                            return

            forwarder = asyncio.create_task(forward_audio())
            try:
                async for message in gnani:
                    payload = _message_payload(message)
                    if payload.get("type") == "transcript":
                        text = payload.get("text", payload.get("transcript", ""))
                        is_final = payload.get("final", True) is True
                        if text and is_final:
                            segments.append(
                                TranscriptSegment(
                                    speaker=current_speaker["name"],
                                    name=current_speaker["name"],
                                    role=current_speaker["role"],
                                    text=text,
                                )
                            )
                        await websocket.send_json(
                            {
                                "type": "transcript",
                                "text": text,
                                "speaker": current_speaker["name"],
                                "role": current_speaker["role"],
                                "final": is_final,
                            }
                        )
                    elif payload.get("type") == "error":
                        await websocket.send_json(
                            {"type": "error", "message": payload.get("message", "Gnani STT error")}
                        )
                        break
            finally:
                forwarder.cancel()
                await asyncio.gather(forwarder, return_exceptions=True)
    except WebSocketDisconnect:
        logger.info("Transcription client disconnected")
    except (RuntimeError, ValueError) as exc:
        logger.error("Unable to start transcription: %s", exc)
        await websocket.send_json({"type": "error", "message": str(exc)})
        await websocket.close(code=1011)
    except (WebSocketException, OSError, asyncio.TimeoutError):
        logger.exception("Realtime transcription failed")
        await websocket.send_json(
            {"type": "error", "message": "Realtime transcription failed"}
        )
        await websocket.close(code=1011)
    finally:
        if audio:
            try:
                _save_recording(recording_id, display_name, bytes(audio), segments)
                try:
                    await websocket.send_json(
                        {
                            "type": "saved",
                            "recording": {
                                "id": recording_id,
                                "name": display_name,
                                "audio_url": f"/recordings/{recording_id}/audio",
                                "pdf_url": f"/recordings/{recording_id}/pdf",
                            },
                        }
                    )
                except WebSocketDisconnect:
                    logger.info("Recording client disconnected after audio was saved")
            except Exception:
                logger.exception("Could not save recording %s", recording_id)


@app.post("/export-pdf")
async def export_pdf(request: PdfExportRequest) -> StreamingResponse:
    if request.recording_id:
        request = _recording_pdf_request(request.recording_id)
    if not request.transcript and not request.segments:
        raise HTTPException(
            status_code=400, detail="Provide transcript or at least one segment"
        )
    return StreamingResponse(
        io.BytesIO(_pdf_content(request)),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="courtscribe-transcript.pdf"'},
    )


@app.post("/export-to-desktop")
async def export_to_desktop(request: DesktopExportRequest) -> dict[str, str]:
    pdf_request = _recording_pdf_request(request.recording_id)
    if not pdf_request.transcript and not pdf_request.segments:
        raise HTTPException(
            status_code=400, detail="Recording does not contain a transcript"
        )

    desktop = Path.home() / "Desktop"
    destination_folder = desktop if desktop.is_dir() else Path.home()
    filename = _recording_filename(pdf_request, request.recording_id, request.format)
    destination = destination_folder / filename
    content = (
        _pdf_content(pdf_request)
        if request.format == "pdf"
        else (
            pdf_request.transcript
            or "\n".join(segment.text for segment in pdf_request.segments)
        ).encode("utf-8")
    )
    try:
        destination.write_bytes(content)
    except OSError as exc:
        logger.exception("Could not save export to %s", destination)
        raise HTTPException(status_code=500, detail="Could not save export to Desktop") from exc
    return {"saved_to": str(destination)}


@app.get("/recordings")
async def list_recordings() -> list[dict[str, Any]]:
    return _recording_list()


@app.patch("/recordings/{recording_id}/segment/{segment_index}")
async def update_recording_segment(
    recording_id: str,
    segment_index: int,
    request: SegmentUpdateRequest,
) -> dict[str, Any]:
    folder = _recording_folder(recording_id)
    metadata_path = folder / "metadata.json"
    if not metadata_path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found")
    if segment_index < 0:
        raise HTTPException(status_code=404, detail="Transcript segment not found")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        raw_segments = metadata.get("segments", [])
        segments = [TranscriptSegment(**segment) for segment in raw_segments]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Invalid recording metadata") from exc
    if segment_index >= len(segments):
        raise HTTPException(status_code=404, detail="Transcript segment not found")

    segments[segment_index].text = request.text.strip()
    if not segments[segment_index].text:
        raise HTTPException(status_code=422, detail="Text must not be blank")
    metadata["segments"] = [segment.model_dump() for segment in segments]
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pdf_request = PdfExportRequest(
        title=metadata.get("name", "CourtScribe Transcript"),
        court_name=metadata.get("court_name", "CourtScribe Court"),
        case_number=metadata.get("case_number", ""),
        hearing_date=metadata.get("hearing_date", ""),
        appearances=metadata.get("appearances", []),
        segments=segments,
        transcript="\n".join(segment.text for segment in segments),
    )
    try:
        (folder / "transcript.pdf").write_bytes(_pdf_content(pdf_request))
    except OSError as exc:
        logger.exception("Could not regenerate transcript PDF for %s", recording_id)
        raise HTTPException(status_code=500, detail="Could not regenerate transcript PDF") from exc
    return {"segment_index": segment_index, "segment": segments[segment_index].model_dump()}


@app.get("/recordings/{recording_id}/audio")
async def recording_audio(recording_id: str) -> FileResponse:
    path = _recording_folder(recording_id) / "recording.wav"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio recording not found")
    return FileResponse(path, media_type="audio/wav", filename=f"{recording_id}.wav")


@app.get("/recordings/{recording_id}/pdf")
async def recording_pdf(recording_id: str) -> FileResponse:
    path = _recording_folder(recording_id) / "transcript.pdf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Transcript PDF not found")
    return FileResponse(path, media_type="application/pdf", filename=f"{recording_id}.pdf")
