import asyncio
import json
import sys
import threading
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ChatRequest,
    ChatResponse,
    TopicAnalysis,
)
from backend.topic_detecter import TopicShiftDetector

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
  sys.path.insert(0, str(PROJECT_ROOT))


class SimilarityRequest(BaseModel):
  current_message: str
  context_messages: list[str]
  threshold: float = Field(default=0.74, ge=0, le=1)


class TopicMessageRequest(BaseModel):
  added_text: str


class TopicResetRequest(BaseModel):
  transcript: str = ""


class SimilarityResponse(BaseModel):
  similarity: float
  context_similarity: float
  threshold: float
  topic_changed: bool


FRONTEND_DIR = Path(__file__).resolve().parent / "index"
if str(FRONTEND_DIR) not in sys.path:
  sys.path.insert(0, str(FRONTEND_DIR))

# main.pyがサーバーを起動し、index側のgetvoice.pyを同じサーバーに組み込みます。
import index.getvoice


@asynccontextmanager
async def lifespan(_app):
  await asyncio.to_thread(get_topic_detector)
  yield
  await asyncio.to_thread(index.getvoice.service.stop)


app = FastAPI(lifespan=lifespan)


@lru_cache(maxsize=1)
def get_topic_detector():
  return TopicShiftDetector()


topic_detector_lock = threading.Lock()


# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# フロントエンドのCSSとJavaScriptを公開
app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


# 1. ルート（http://127.0.0.1:8080/）にアクセスされたら HTML を返す
@app.get("/", response_class=HTMLResponse)
def read_root():
  index_file = FRONTEND_DIR / "index.html"
  if not index_file.is_file():
    raise HTTPException(status_code=404, detail="index.htmlが見つかりません")
  return HTMLResponse(content=index_file.read_text(encoding="utf-8"), status_code=200)


# 2. チャット用のAPIエンドポイント
@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
  user_message = request.message
  print(f"受信したメッセージ: {user_message}")

  # ここでChatGPTのAPIを呼び出す
  reply_text = f"「{user_message}」ですね。サーバーで受け取りました！"

  return ChatResponse(reply=reply_text)


@app.post("/analyze")
async def analyze_transcript(request: AnalyzeRequest):
  messages = [line.strip() for line in request.transcript.splitlines() if line.strip()]
  if not messages:
    raise HTTPException(status_code=422, detail="会話の内容を入力してください")

  try:
    # ai_treat 側から処理用関数をインポート
    from backend.ai_treat import run_analysis_from_messages
  except ImportError as error:
    raise HTTPException(status_code=503, detail=f"ai_treatのインポートに失敗しました: {str(error)}") from error

  event_queue = asyncio.Queue()
  event_loop = asyncio.get_running_loop()

  def publish(event):
    event_loop.call_soon_threadsafe(event_queue.put_nowait, event)

  def analyze():
    try:
      run_analysis_from_messages(
          messages,
          threshold=request.threshold,
          on_update=lambda results: publish(results[-1]),
      )
    except Exception as error:
      publish({"error": str(error)})
    finally:
      publish(None)

  async def stream_events():
    analysis_task = asyncio.create_task(asyncio.to_thread(analyze))
    while True:
      event = await event_queue.get()
      if event is None:
        break
      yield json.dumps(event, ensure_ascii=False) + "\n"
    await analysis_task

  return StreamingResponse(stream_events(), media_type="application/x-ndjson")


@app.post("/similarity", response_model=SimilarityResponse)
def compare_recent_messages(request: SimilarityRequest):
  current_message = request.current_message.strip()
  context_messages = [message.strip() for message in request.context_messages if message.strip()]
  if not context_messages or not current_message:
    raise HTTPException(status_code=422, detail="比較する発言を2つ入力してください")

  detector = get_topic_detector()
  similarity = detector.compare_context(
      context_messages, current_message
  )
  return SimilarityResponse(
      similarity=similarity,
      context_similarity=similarity,
      threshold=request.threshold,
      topic_changed=(
        similarity < request.threshold
        and not detector.has_demonstrative_reference(current_message)
      ),
  )


@app.post("/topic")
def add_topic_message(request: TopicMessageRequest):
  messages = [line.strip() for line in request.added_text.splitlines() if line.strip()]
  if not messages:
    raise HTTPException(status_code=422, detail="追加する発言を入力してください")

  detector = get_topic_detector()
  with topic_detector_lock:
    completed_topics = [
      topic
      for message in messages
      if (topic := detector.add_message(message)) is not None
    ]
    return {
      "completed_topics": completed_topics,
      "topic_count": detector.topic_count,
      "current_topic": list(detector.current_topic),
    }


@app.post("/topic/reset")
def reset_topic_detector(request: TopicResetRequest):
  detector = get_topic_detector()
  messages = [line.strip() for line in request.transcript.splitlines() if line.strip()]
  with topic_detector_lock:
    detector.current_topic.clear()
    detector.topic_count = 0
    for message in messages:
      detector.add_message(message)
  return {"topic_count": detector.topic_count, "current_topic": list(detector.current_topic)}


@app.get("/api/get-gpt-response")
async def get_gpt_response_api():
    """
    conversation.json またはサンプルファイルからメッセージを読み込み、
    話題分析を実行して蓄積された結果をJSON形式で返します。
    """
    try:
        from backend.ai_treat import (
          get_sample_prompt_log_path,
          get_sample_output_log_path,
          load_messages,
          LiveTopicAnalyzer,
        )
        from backend.ai_service import ANALYSIS_PROMPT_TEMPLATE, get_chatgpt_response
        from backend.topic_detecter import TopicShiftDetector

        messages = load_messages(None)
        if not messages:
            return {"status": "error", "message": "会話テキストが空です"}

        results = []
        
        analyzer = LiveTopicAnalyzer(
            get_chatgpt_response=get_chatgpt_response,
            prompt_template=ANALYSIS_PROMPT_TEMPLATE,
          save_analysis=lambda topic, analysis: None,
          prompt_log_path=get_sample_prompt_log_path(None),
          analysis_log_path=get_sample_output_log_path(None),
        )

        detector = TopicShiftDetector(
            on_topic_completed=lambda topic: results.append(analyzer.analyze_topic(topic))
        )

        for message in messages:
            detector.add_message(message)
        detector.flush_topic()

        return {"status": "success", "results": results}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# 既存のAPIにないパスはgetvoice.py側で処理します（同じ8080番ポート）。
app.mount("/", index.getvoice.app)

if __name__ == "__main__":
  import uvicorn

  uvicorn.run(app, host="127.0.0.1", port=8080, access_log=True)