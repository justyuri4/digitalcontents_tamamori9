"""FastAPIでHTMLを配信し、録音処理をJSON APIから操作します。"""

import os
import sqlite3
import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import whisper


PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_DIR / "transcripts.db"
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
MAX_AUDIO_FILE_BYTES = 25 * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac", ".ogg", ".opus", ".webm"
}


# --- データベース・Whisper関連のヘルパー関数 ---

def initialize_database():
    """データベースとテーブルを初期化します。"""
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS transcripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                text TEXT,
                processed BOOLEAN DEFAULT 0
            )
            """
        )


def load_whisper_model(model_name: str):
    """Whisperモデルをロードします。"""
    return whisper.load_model(model_name)


def transcribe_audio_file(model, audio_path: Path) -> str:
    """音声ファイルをWhisperで文字起こしします。"""
    result = model.transcribe(str(audio_path))
    return result.get("text", "").strip()


def save_transcript(text: str):
    """文字起こし結果をデータベースに保存します。"""
    if not text:
        return
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            "INSERT INTO transcripts (text) VALUES (?)",
            (text,)
        )


class TranscriptionService:
    """音声文字起こしサービスを管理するクラス。"""

    def __init__(self, model_name: str = "base"):
        self.model_name = model_name
        self.model = None
        self._running = False

    def start(self) -> str:
        if self._running:
            return "already_running"
        self._running = True
        if self.model is None:
            self.model = load_whisper_model(self.model_name)
        return "started"

    def stop(self) -> str:
        if not self._running:
            return "not_running"
        self._running = False
        return "stopped"

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "model": self.model_name,
        }


# --- FastAPI アプリケーション設定 ---

service = TranscriptionService(model_name=MODEL_NAME)
initialize_database()


@asynccontextmanager
async def lifespan(_app):
    yield
    await asyncio.to_thread(service.stop)


app = FastAPI(title="ローカル音声文字起こし", lifespan=lifespan)


class RecordingCommand(BaseModel):
    """録音開始・停止のJSONリクエスト。"""
    speaker_count: int = Field(default=2, ge=1, le=20)


class RenameSpeakersRequest(BaseModel):
    transcript_ids: list[int] = Field(min_length=1)
    names: dict[str, str]


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
    status = service.start(speaker_count=command.speaker_count)
    return {"status": status, "after_id": after_id}


@app.post("/api/transcripts/rename-speakers")
def rename_speakers(request: RenameSpeakersRequest):
    """指定された録音行の話者ラベルを人物名に置き換えて保存します。"""
    names = {
        f"話者{int(speaker_id)}": name.strip()
        for speaker_id, name in request.names.items()
        if speaker_id.isdigit() and name.strip()
    }
    if not names:
        raise HTTPException(status_code=422, detail="人物名を1つ以上入力してください")
    with sqlite3.connect(DATABASE_PATH) as connection:
        for transcript_id in request.transcript_ids:
            row = connection.execute(
                "SELECT text FROM transcripts WHERE id = ?", (transcript_id,)
            ).fetchone()
            if row is None:
                continue
            text = row[0]
            for old_name, new_name in names.items():
                text = text.replace(f"{old_name}：", f"{new_name}：")
            connection.execute(
                "UPDATE transcripts SET text = ? WHERE id = ?",
                (text, transcript_id),
            )
    return {"updated": len(request.transcript_ids)}


@app.post("/api/recording/stop")
def stop_recording(command: RecordingCommand):
    return {"status": service.stop()}


def transcribe_and_save_file(audio_path: Path, speaker_count: int) -> tuple[str, int | None]:
    """アップロードファイルをローカルWhisperで処理し、結果だけ保存します。"""
    model = load_whisper_model(MODEL_NAME)
    text = transcribe_audio_file(model, audio_path, speaker_count)
    transcript_id = save_transcript(text) if text else None
    return text, transcript_id


@app.post("/api/transcripts/upload")
async def upload_audio_file(
    file: UploadFile = File(...),
    speaker_count: int = Form(default=2, ge=1, le=20),
):
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

        text, transcript_id = await asyncio.to_thread(
            transcribe_and_save_file, temporary_path, speaker_count
        )
        return {
            "status": "saved" if text else "empty",
            "text": text,
            "saved": bool(text),
            "transcript_id": transcript_id,
        }
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


# 静的ファイルのディレクトリが存在する場合のみマウント
if (PROJECT_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=PROJECT_DIR / "css"), name="css")
if (PROJECT_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=PROJECT_DIR / "js"), name="js")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)