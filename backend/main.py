from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from functools import lru_cache
from ai_service import ANALYSIS_PROMPT_TEMPLATE, get_chatgpt_response
from models import (
  AnalyzeRequest,
  AnalyzeResponse,
  ChatRequest,
  ChatResponse,
  SimilarityRequest,
  SimilarityResponse,
  TopicAnalysis,
)
from topic_ditecter import TopicShiftDetector

app = FastAPI()
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "index"


@lru_cache(maxsize=1)
def get_topic_detector():
  return TopicShiftDetector()

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


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_transcript(request: AnalyzeRequest):
  messages = [line.strip() for line in request.transcript.splitlines() if line.strip()]
  if not messages:
    raise HTTPException(status_code=422, detail="会話の内容を入力してください")

  shift_indices = get_topic_detector().detect_shifts(messages, request.threshold)
  boundaries = [0, *shift_indices, len(messages)]
  topics = []

  for start, end in zip(boundaries, boundaries[1:]):
    topic = "\n".join(messages[start:end])
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(topic=topic)
    topics.append(
        TopicAnalysis(topic=topic, analysis=get_chatgpt_response(prompt))
    )

  return AnalyzeResponse(topics=topics, shift_indices=shift_indices)


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


if __name__ == "__main__":
  import uvicorn

  uvicorn.run(app, host="127.0.0.1", port=8080)