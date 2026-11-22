"""Project 4, Session 2 — Agent (local version).

The student grows up: instead of one question -> one answer, the agent works
in steps, using tools, carrying a notebook (state) between steps.

The graph (LangGraph StateGraph). Each node is one room; each edge is a
doorway; the state is the notebook carried from room to room:

    plan -> search -> answer
    plan -> summarise -> search -> answer   (two-step questions)
    search -> search (once, rephrased, if nothing useful was found)

Tools (the toolbox):
    search_docs(query)   - the librarian: top 5 chunks by cosine similarity
    summarise_doc(name)  - reads a whole document, returns a short summary

The notebook (state) carries: the question, the plan, the chunks found,
the summary, how many retries were used, and the final answer.

Run from the terraform-rag-agent folder, in the Python 3.12 venv:
    .venv\\Scripts\\Activate.ps1
    $env:OPENAI_API_KEY = "your-key-here"   (session only, never in a file)
    pip install langgraph
    python agent.py "Summarise the EKS document, then what does it say about the control plane?"

In Lambda the same code runs, except chunks.json comes from S3 instead of
the local file: set CHUNKS_S3_BUCKET and load_chunks() reads it from there.
See agent_lambda.py for the Lambda entry point.
"""

import json
import math
import os
import sys
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI

CHUNKS_FILE = Path("chunks.json")
EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = "gpt-4o-mini"
TOP_K = 5
WEAK_SCORE = 0.20  # below this, the best chunk is too weak to trust
MAX_RETRIES = 1

DOCS = [
    "How-Lambda-works.pdf",
    "Using-Lambda-with-API-Gateway.pdf",
    "Amazon-EKS.pdf",
    "EKS-Control-Plane.pdf",
    "Configure-Terraform-providers.pdf",
]

SYSTEM_PROMPT = (
    "You answer questions using ONLY the document chunks and summary given below. "
    "For each fact in your answer, name the source file it came from. "
    "If the answer is not in these chunks or the summary, say exactly: "
    "'Not in these documents.' Do not use any outside knowledge."
)

_client = None
_chunks = None


def client():
    global _client
    if _client is None:
        _client = OpenAI()  # reads OPENAI_API_KEY from the environment
    return _client


def load_chunks():
    """All 93 chunks. On the laptop they come from the local chunks.json;
    in Lambda they come from S3 (CHUNKS_S3_BUCKET set by Terraform)."""
    global _chunks
    if _chunks is None:
        bucket = os.environ.get("CHUNKS_S3_BUCKET")
        if bucket:
            import boto3

            obj = boto3.client("s3").get_object(Bucket=bucket, Key="chunks.json")
            _chunks = json.loads(obj["Body"].read().decode("utf-8"))
        else:
            _chunks = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    return _chunks


def cosine(a, b):
    """How close two fingerprints point in the same direction: 1 = same
    direction, 0 = unrelated, -1 = opposite."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b)


def chat(messages):
    """One call to gpt-4o-mini, returns the reply text."""
    resp = client().chat.completions.create(model=CHAT_MODEL, messages=messages)
    return resp.choices[0].message.content


def search_docs(query):
    """The librarian: fingerprint the query, return the top 5 chunks with
    their similarity scores."""
    resp = client().embeddings.create(input=query, model=EMBED_MODEL)
    q_emb = resp.data[0].embedding
    ranked = sorted(
        load_chunks(), key=lambda c: cosine(q_emb, c["embedding"]), reverse=True
    )
    top = ranked[:TOP_K]
    return [
        {
            "source": c["source"],
            "chunk_id": c["chunk_id"],
            "text": c["text"],
            "score": cosine(q_emb, c["embedding"]),
        }
        for c in top
    ]


class AgentState(TypedDict, total=False):
    """The notebook. Every node reads it and writes its part back."""
    question: str
    plan: str
    next_step: str  # "search" | "summarise" | "answer"
    search_query: str
    doc_name: str
    then_search: bool  # after a summary, search for details too?
    chunks: list
    summary: str
    retries: int
    answer: str
    steps: list  # human-readable trace of what happened


def plan_node(state):
    """Room 1: read the question, write the plan. Which tool first, and
    what to search for?"""
    prompt = (
        "You plan how to answer a question about these documents:\n"
        + "\n".join(f"- {d}" for d in DOCS)
        + "\n\nQuestion: "
        + state["question"]
        + "\n\nIf the question asks to summarise a document, pick that document "
        "and set next_step to 'summarise'. Otherwise set next_step to 'search'. "
        "If it asks for a summary AND then something specific, set "
        "then_search to true so details are searched after the summary. "
        "Reply with JSON only, exactly like this:\n"
        '{"next_step": "search", "doc_name": "", "search_query": "...", '
        '"then_search": false, "plan": "one sentence"}'
    )
    try:
        raw = chat([{"role": "user", "content": prompt}])
        plan = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    except Exception:
        plan = {
            "next_step": "search",
            "doc_name": "",
            "search_query": state["question"],
            "then_search": False,
            "plan": "Plan unreadable, falling back to a plain search.",
        }
    step = f"PLAN: {plan['plan']} (first step: {plan['next_step']})"
    print(step)
    return {
        "plan": plan["plan"],
        "next_step": plan["next_step"],
        "doc_name": plan.get("doc_name") or "",
        "search_query": plan.get("search_query") or state["question"],
        "then_search": bool(plan.get("then_search")),
        "steps": state.get("steps", []) + [step],
    }


def search_node(state):
    """Room 2: the librarian searches. If nothing useful comes back and we
    still have a retry left, rephrase the question once and search again.
    Otherwise carry the chunks to the answer."""
    query = state["search_query"]
    top = search_docs(query)
    best = top[0]["score"] if top else 0.0
    if best < WEAK_SCORE and state.get("retries", 0) < MAX_RETRIES:
        rephrased = chat(
            [
                {
                    "role": "user",
                    "content": "Rephrase this search query using different words, "
                    "keeping the same meaning. Reply with only the rephrased query.\n\n"
                    + query,
                }
            ]
        ).strip()
        step = (
            f"SEARCH: '{query}' found nothing strong (best score {best:.2f}). "
            f"Retry 1 with rephrased query: '{rephrased}'"
        )
        print(step)
        return {
            "search_query": rephrased,
            "retries": state.get("retries", 0) + 1,
            "next_step": "search",
            "steps": state.get("steps", []) + [step],
        }
    step = f"SEARCH: '{query}' -> top {len(top)} chunks, best score {best:.2f}"
    for c in top:
        step += f"\n    - {c['source']} (chunk {c['chunk_id']}, score {c['score']:.2f})"
    print(step)
    return {
        "chunks": top,
        "next_step": "answer",
        "steps": state.get("steps", []) + [step],
    }


def summarise_node(state):
    """Room 3: read a whole document (all its chunks, in order) and write
    a short summary into the notebook."""
    doc = state["doc_name"]
    parts = [c for c in load_chunks() if c["source"] == doc]
    parts.sort(key=lambda c: c["chunk_id"])
    text = "\n\n".join(c["text"] for c in parts)
    summary = chat(
        [
            {
                "role": "user",
                "content": "Summarise this document in 5 short bullet points. "
                "Name the document in the first line.\n\n" + text,
            }
        ]
    )
    step = f"SUMMARISE: {doc} ({len(parts)} chunks) -> summary written to notebook"
    print(step)
    nxt = "search" if state.get("then_search") else "answer"
    if nxt == "search":
        # The follow-up needs detail: search for the specific part asked about.
        return {
            "summary": summary,
            "search_query": state["question"],
            "next_step": "search",
            "steps": state.get("steps", []) + [step],
        }
    return {
        "summary": summary,
        "next_step": "answer",
        "steps": state.get("steps", []) + [step],
    }


def answer_node(state):
    """Room 4: write the final answer from the notebook only — chunks and
    summary — with citations, or 'Not in these documents.'"""
    context = ""
    if state.get("summary"):
        context += "Summary:\n" + state["summary"] + "\n\n"
    if state.get("chunks"):
        context += "Document chunks:\n" + "\n\n".join(
            f"[{c['source']}, chunk {c['chunk_id']}]\n{c['text']}"
            for c in state["chunks"]
        )
    answer = chat(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Question: {state['question']}\n\n{context}",
            },
        ]
    )
    step = "ANSWER: final answer written from the notebook"
    print(step)
    return {
        "answer": answer,
        "steps": state.get("steps", []) + [step],
    }


def route(state):
    """Which doorway next? Reads next_step from the notebook."""
    return state.get("next_step", "answer")


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("search", search_node)
    graph.add_node("summarise", summarise_node)
    graph.add_node("answer", answer_node)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", route, {"search": "search", "summarise": "summarise", "answer": "answer"}
    )
    graph.add_conditional_edges("search", route, {"search": "search", "answer": "answer"})
    graph.add_conditional_edges(
        "summarise", route, {"search": "search", "answer": "answer"}
    )
    graph.add_edge("answer", END)
    return graph.compile()


def run_agent(question):
    """Run one question through the graph. Returns the final state."""
    app = build_graph()
    return app.invoke(
        {
            "question": question,
            "retries": 0,
            "steps": [],
            "chunks": [],
            "summary": "",
        }
    )


def main():
    if len(sys.argv) < 2:
        print('Usage: python agent.py "your question here"')
        return
    question = sys.argv[1]
    print(f"Loaded {len(load_chunks())} chunks from {CHUNKS_FILE}\n")
    result = run_agent(question)
    print("\n--- Steps taken ---")
    for s in result.get("steps", []):
        print(s)
    print("\n--- Answer ---")
    print(result.get("answer", "(no answer)"))
    if result.get("summary"):
        print("\n--- Summary used ---")
        print(result["summary"])


if __name__ == "__main__":
    main()
