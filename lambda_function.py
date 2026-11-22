"""Project 4, Session 1 — Lambda handler (Step 4).

Same logic as ask.py, running in Lambda behind API Gateway:
- loads chunks.json from S3 once per warm container (cached in memory)
- fingerprints the question with OpenAI text-embedding-3-small
- finds the 5 closest chunks by cosine similarity
- asks gpt-4o-mini to answer using ONLY those chunks
- returns the answer plus the sources used

No third-party packages: only the Python standard library (urllib) plus
boto3, which the Lambda runtime already includes. That keeps the deployment
zip to this one file.

The OpenAI key comes from the OPENAI_API_KEY environment variable, set by
Terraform from a sensitive variable (never in a file, never in the repo).
"""

import json
import math
import os
import urllib.request

import boto3

BUCKET = os.environ["BUCKET"]
CHUNKS_KEY = os.environ.get("CHUNKS_KEY", "chunks.json")
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
TOP_K = 5

SYSTEM_PROMPT = (
    "You answer questions using ONLY the document chunks given below. "
    "For each fact in your answer, name the source file it came from. "
    "If the answer is not in these chunks, say exactly: "
    "'Not in these documents.' Do not use any outside knowledge."
)

s3 = boto3.client("s3")
_chunks = None


def load_chunks():
    """All 93 chunks from S3, cached in the container's memory so a warm
    invocation does not download them again."""
    global _chunks
    if _chunks is None:
        obj = s3.get_object(Bucket=BUCKET, Key=CHUNKS_KEY)
        _chunks = json.loads(obj["Body"].read().decode("utf-8"))
    return _chunks


def openai_post(path, payload):
    """One HTTPS call to the OpenAI API (plain urllib, no SDK needed)."""
    req = urllib.request.Request(
        "https://api.openai.com/v1/" + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + OPENAI_API_KEY,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cosine(a, b):
    """How close two fingerprints point in the same direction: 1 = same
    direction, 0 = unrelated, -1 = opposite."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b)


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        body = {}
    question = (body.get("question") or "").strip()
    if not question:
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": 'Send JSON like {"question": "..."}'}),
        }

    chunks = load_chunks()

    # 1. Fingerprint the question (the librarian's lookup).
    emb = openai_post("embeddings", {"input": question, "model": EMBED_MODEL})
    q_emb = emb["data"][0]["embedding"]

    # 2. Rank every chunk by cosine similarity, keep the top 5.
    ranked = sorted(chunks, key=lambda c: cosine(q_emb, c["embedding"]), reverse=True)
    top = ranked[:TOP_K]

    # 3. Ask the student, with ONLY those chunks on the desk.
    context_text = "\n\n".join(
        "[%s, chunk %s]\n%s" % (c["source"], c["chunk_id"], c["text"]) for c in top
    )
    chat = openai_post("chat/completions", {
        "model": CHAT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Question: %s\n\nDocument chunks:\n%s" % (question, context_text),
            },
        ],
    })
    answer = chat["choices"][0]["message"]["content"]

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "answer": answer,
            "sources": [
                {"source": c["source"], "chunk_id": c["chunk_id"]} for c in top
            ],
        }),
    }
