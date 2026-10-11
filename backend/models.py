from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
  message: str


class ChatResponse(BaseModel):
  reply: str


class AnalyzeRequest(BaseModel):
  transcript: str
  threshold: float = Field(default=0.88, ge=0, le=1)


class SimilarityRequest(BaseModel):
  context_messages: list[str] = Field(min_length=1)
  current_message: str
  threshold: float = Field(default=0.88, ge=0, le=1)


class SimilarityResponse(BaseModel):
  similarity: float
  threshold: float
  topic_changed: bool


class TopicAnalysis(BaseModel):
  topic: str
  analysis: str


class AnalyzeResponse(BaseModel):
  topics: list[TopicAnalysis]
  shift_indices: list[int]