from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
  message: str


class ChatResponse(BaseModel):
  reply: str


class AnalyzeRequest(BaseModel):
  transcript: str = ""
  threshold: float = Field(default=0.74, ge=0, le=1)


class TopicAnalysis(BaseModel):
  topic: str
  analysis: str


class AnalyzeResponse(BaseModel):
  topics: list[TopicAnalysis]
  shift_indices: list[int]