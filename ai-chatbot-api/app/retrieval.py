from pathlib import Path
import re

DOCS_DIR = Path(__file__).parent.parent / "docs"

def load_docs():
    docs = []
    for path in sorted(DOCS_DIR.glob("*.txt")):
        docs.append({"source": path.name, "text": path.read_text()})
    return docs

def retrieve(question: str, top_k: int = 2):
    """Score docs by word overlap with the question. Simple, but teaches the RAG idea."""
    words = set(re.findall(r"\w+", question.lower()))
    scored = []
    for doc in load_docs():
        doc_words = set(re.findall(r"\w+", doc["text"].lower()))
        score = len(words & doc_words)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]
