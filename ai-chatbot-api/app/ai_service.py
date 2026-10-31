import os
import requests
from .retrieval import retrieve

def build_prompt(question: str, docs) -> str:
    context = "\n\n".join(d["text"] for d in docs)
    return f"Answer using only these documents:\n\n{context}\n\nQuestion: {question}\nAnswer:"

def call_llm(prompt: str) -> str:
    """Real LLM via REST, only if a key is set. Otherwise use the mock below."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

def get_answer(question: str):
    docs = retrieve(question)
    if not docs:
        return "I don't have information on that in my documents.", []

    sources = [d["source"] for d in docs]
    prompt = build_prompt(question, docs)

    answer = call_llm(prompt)
    if answer is None:
        # Mock LLM: grounded answer, no hallucination, for local learning
        answer = f"Based on {', '.join(sources)}: {docs[0]['text'][:400]}"

    return answer, sources
