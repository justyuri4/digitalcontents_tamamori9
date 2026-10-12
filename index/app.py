"""FastAPIでHTMLを配信し、録音処理をJSON APIから操作します。"""

import os
import sqlite3
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from realtime_transcriber import DATABASE_PATH, TranscriptionService, initialize_database


PROJECT_DIR = Path(__file__).resolve().parent
service = TranscriptionService(model_name=os.environ.get("WHISPER_MODEL", "base"))
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


@app.get("/api/recording/status")
def recording_status():
    result = service.get_status()
    result["latest_id"] = get_latest_transcript_id()
    return result


@app.get("/api/transcripts")
def get_transcripts(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
):
    """指定IDより後に保存された文字起こしをJSONで返します。"""
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT id, timestamp, text, processed
               FROM transcripts WHERE id > ? ORDER BY id LIMIT ?""",
            (after_id, limit),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


app.mount("/css", StaticFiles(directory=PROJECT_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=PROJECT_DIR / "js"), name="js")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
