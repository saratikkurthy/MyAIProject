from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from .models import ChatRequest, ChatResponse
from .ai_service import get_answer

app = FastAPI(title="AI Chatbot API")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def home():
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    answer, sources = get_answer(req.question)
    return ChatResponse(answer=answer, sources=sources)
