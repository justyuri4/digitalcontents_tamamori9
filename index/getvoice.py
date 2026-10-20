"""FastAPIでHTMLを配信し、録音処理をJSON APIから操作します。"""

import os
import sqlite3
import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from realtime_transcriber import (
    DATABASE_PATH,
    TranscriptionService,
    initialize_database,
    load_whisper_model,
    save_transcript,
    transcribe_audio_file,
)


PROJECT_DIR = Path(__file__).resolve().parent
service = TranscriptionService(model_name=os.environ.get("WHISPER_MODEL", "base"))
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
MAX_AUDIO_FILE_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".opus", ".webm"
}
initialize_database()


@asynccontextmanager
async def lifespan(_app):
    yield
    await asyncio.to_thread(service.stop)


app = FastAPI(title="ローカル音声文字起こし", lifespan=lifespan)


class RecordingCommand(BaseModel):
    """録音開始・停止のJSONリクエスト（現時点では空オブジェクト）。"""


def get_latest_transcript_id() -> int:
    with sqlite3.connect(DATABASE_PATH) as connection:
        row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM transcripts").fetchone()
    return row[0]


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(PROJECT_DIR / "index.html")


@app.post("/api/recording/start")
def start_recording(command: RecordingCommand):
    # 開始時点のIDを返し、画面が過去の保存分を新規音声として表示しないようにします。
    after_id = get_latest_transcript_id()
    status = service.start()
    return {"status": status, "after_id": after_id}


@app.post("/api/recording/stop")
def stop_recording(command: RecordingCommand):
    return {"status": service.stop()}


def transcribe_and_save_file(audio_path: Path) -> str:
    """アップロードファイルをローカルWhisperで処理し、結果だけ保存します。"""
    model = load_whisper_model(MODEL_NAME)
    text = transcribe_audio_file(model, audio_path)
    if text:
        save_transcript(text)
    return text


@app.post("/api/transcripts/upload")
async def upload_audio_file(file: UploadFile = File(...)):
    """音声ファイルを文字起こしし、マイク入力と同じDBへ保存します。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="対応形式は WAV、MP3、M4A、MP4、AAC、FLAC、OGG、OPUS、WebM です。",
        )

    temporary_path = None
    try:
        total_bytes = 0
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > MAX_AUDIO_FILE_BYTES:
                    raise HTTPException(status_code=413, detail="音声ファイルは25MB以下にしてください。")
                temporary_file.write(chunk)

        text = await asyncio.to_thread(transcribe_and_save_file, temporary_path)
        return {"status": "saved" if text else "empty", "text": text, "saved": bool(text)}
    finally:
        await file.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@app.get("/api/recording/status")
def recording_status():
    result = service.get_status()
    result["latest_id"] = get_latest_transcript_id()
    return result


@app.get("/api/transcripts")
def get_transcripts(
    after_id: int = Query(default=0, ge=0),
    from_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
):
    """新しい行、または指定ID以降の行をJSONで返します。"""
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        if from_id is None:
            rows = connection.execute(
                """SELECT id, timestamp, text, processed
                   FROM transcripts WHERE id > ? ORDER BY id LIMIT ?""",
                (after_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT id, timestamp, text, processed
                   FROM transcripts WHERE id >= ? ORDER BY id LIMIT ?""",
                (from_id, limit),
            ).fetchall()
    return {"items": [dict(row) for row in rows]}


app.mount("/css", StaticFiles(directory=PROJECT_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=PROJECT_DIR / "js"), name="js")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
