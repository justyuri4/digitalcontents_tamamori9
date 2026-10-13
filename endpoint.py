from fastapi import FastAPI
from models import ChatRequest, ChatResponse
from ai_service import get_chatgpt_response

app = FastAPI()


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(request: ChatRequest):
  reply_text = get_chatgpt_response(request.message)
  return ChatResponse(reply=reply_text)