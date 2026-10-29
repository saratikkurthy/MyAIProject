from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class ChatRequest(BaseModel):
    question: str

class ChatResponse(BaseModel):
    answer: str
    sources: list[str]

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    # For now, this is a placeholder AI service.
    # Later we swap it for RAG + a real LLM call.
    return ChatResponse(
        answer=f"You asked: {req.question}",
        sources=["doc1.txt"],
    )
