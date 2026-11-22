"""Project 4, Session 1 — Ingest.

Reads the 5 PDFs in docs/, cuts each one into small chunks (sticky notes),
asks OpenAI (text-embedding-3-small) for a fingerprint of each chunk, and
saves all chunks + embeddings as chunks.json, both on disk and in S3.

Run from the terraform-rag-agent folder, in the Python 3.12 venv:
    .venv\\Scripts\\Activate.ps1
    pip install pypdf openai boto3
    $env:OPENAI_API_KEY = "your-key-here"   (session only, never in a file)
    python ingest.py

The S3 bucket must already exist (created once with aws s3 mb).

Notes:
- Embeddings are sent in batches of 100 chunks per API call, so the run
  takes seconds per doc instead of one call per chunk.
- A doc that makes more than 500 chunks is a full guide, not one page —
  the script stops and tells you to re-save just that page.
- OpenAI needs a small credit balance on the account first ($5 minimum
  purchase; this whole project costs a few cents of it).
"""

import json
from pathlib import Path

import boto3
from openai import OpenAI
from pypdf import PdfReader

DOCS_DIR = Path("docs")
BUCKET = "rag-docs-sdeystack"
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
EMBED_MODEL = "text-embedding-3-small"
MAX_CHUNKS_PER_DOC = 500
BATCH_SIZE = 100


def read_pdf(path):
    """Plain text of a PDF, page by page."""
    reader = PdfReader(str(path))
    text = ""
    for page in reader.pages:
        text += (page.extract_text() or "") + "\n"
    return text


def chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Cut text into pieces of ~size chars, overlapping by `overlap` chars
    so a sentence split across two pieces still appears whole in one of them."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def embed_chunks(client, chunks, model=EMBED_MODEL, batch_size=BATCH_SIZE):
    """Fingerprint a list of chunks, `batch_size` at a time (one API call
    per batch instead of one call per chunk)."""
    embeddings = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start:start + batch_size]
        resp = client.embeddings.create(input=batch, model=model)
        embeddings.extend([d.embedding for d in resp.data])
    return embeddings


def main():
    client = OpenAI()  # reads OPENAI_API_KEY from the environment
    s3 = boto3.client("s3")

    pdfs = sorted(DOCS_DIR.glob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs in {DOCS_DIR}/")
    if not pdfs:
        print("Nothing to ingest. Save the 5 docs as PDFs in docs/ first.")
        return

    all_chunks = []
    for pdf in pdfs:
        text = read_pdf(pdf)
        chunks = chunk_text(text)
        print(f"{pdf.name}: {len(text)} chars -> {len(chunks)} chunks")
        if len(chunks) > MAX_CHUNKS_PER_DOC:
            print(f"STOP: {pdf.name} has {len(chunks)} chunks — that is a full guide, not one page.")
            print("Re-save just that one page as a PDF (Ctrl+P on the page itself), then run again.")
            return
        embeddings = embed_chunks(client, chunks)
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            all_chunks.append({
                "source": pdf.name,
                "chunk_id": i,
                "text": chunk,
                "embedding": emb,
            })

    out = Path("chunks.json")
    out.write_text(json.dumps(all_chunks), encoding="utf-8")
    print(f"Wrote {len(all_chunks)} chunks to {out}")

    s3.upload_file(str(out), BUCKET, "chunks.json")
    print(f"Uploaded to s3://{BUCKET}/chunks.json")


if __name__ == "__main__":
    main()
