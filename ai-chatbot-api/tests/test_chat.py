from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    assert client.get("/health").json() == {"status": "ok"}

def test_chat_returns_answer_and_sources():
    res = client.post("/chat", json={"question": "What is RAG?"})
    assert res.status_code == 200
    assert "answer" in res.json()
    assert "rag.txt" in res.json()["sources"]

def test_chat_no_match():
    res = client.post("/chat", json={"question": "zzzzqqqq unknown topic"})
    assert res.status_code == 200
    assert res.json()["sources"] == []
