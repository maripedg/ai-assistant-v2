from pydantic import BaseModel

class ChatRequest(BaseModel):
    question: str

class ChatResponse(BaseModel):
    question: str
    answer: str
    answer2: str
    answer3: str
    retrieved_chunks_metadata: list
