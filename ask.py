"""Project 4, Session 1 — Ask (local test version).

The student, running on your laptop. The Lambda + API Gateway version
comes after this works (Step 4 puts the same logic into Lambda).

Takes your question, fingerprints it, finds the 5 chunks whose fingerprints
point closest to it (cosine similarity), and asks gpt-4o-mini to answer
using ONLY those 5 chunks, naming the source file for each fact. If the
answer is not in the chunks, the model must say so.

Run from the terraform-rag-agent folder, in the Python 3.12 venv:
    .venv\\Scripts\\Activate.ps1
    $env:OPENAI_API_KEY = "your-key-here"   (session only, never in a file)
    python ask.py "What is a Lambda function handler?"
"""

import json
import math
import sys
from pathlib import Path

from openai import OpenAI

CHUNKS_FILE = Path("chunks.json")
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
TOP_K = 5

SYSTEM_PROMPT = (
    "You answer questions using ONLY the document chunks given below. "
    "For each fact in your answer, name the source file it came from. "
    "If the answer is not in these chunks, say exactly: "
    "'Not in these documents.' Do not use any outside knowledge."
)


def cosine(a, b):
    """How close two fingerprints point in the same direction: 1 = same
    direction, 0 = unrelated, -1 = opposite."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b)


def main():
    if len(sys.argv) < 2:
        print('Usage: python ask.py "your question here"')
        return
    question = sys.argv[1]

    client = OpenAI()  # reads OPENAI_API_KEY from the environment
    chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(chunks)} chunks from {CHUNKS_FILE}")

    # 1. Fingerprint the question (the librarian's lookup).
    resp = client.embeddings.create(input=question, model=EMBED_MODEL)
    q_emb = resp.data[0].embedding

    # 2. Rank every chunk by cosine similarity, keep the top 5.
    ranked = sorted(chunks, key=lambda c: cosine(q_emb, c["embedding"]), reverse=True)
    top = ranked[:TOP_K]
    print(f"Top {TOP_K} chunks:")
    for c in top:
        print(f"  - {c['source']} (chunk {c['chunk_id']}): {c['text'][:100]}...")

    # 3. Ask the student, with ONLY those chunks on the desk.
    context = "\n\n".join(
        f"[{c['source']}, chunk {c['chunk_id']}]\n{c['text']}" for c in top
    )
    chat = client.chat.completions.create(
        model=CHAT_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Question: {question}\n\nDocument chunks:\n{context}",
            },
        ],
    )
    print("\nAnswer:")
    print(chat.choices[0].message.content)


if __name__ == "__main__":
    main()
