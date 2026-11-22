"""Project 4, Session 2 — Agent Lambda handler (POST /agent).

Thin wrapper: the graph itself lives in agent.py (the same code as the
local version). The only difference is where the chunks come from —
agent.py loads chunks.json from S3 when CHUNKS_S3_BUCKET is set (see
load_chunks), which Terraform sets for this function.

Packaging: LangGraph and the OpenAI SDK do not ship in the Lambda
runtime, so they are installed into agent_package/ and zipped with
these two files before apply:
    pip install --target agent_package langgraph openai
    Copy-Item agent.py, agent_lambda.py agent_package/
Terraform zips agent_package/ at apply time (see main.tf). The zip is a
few tens of MB; Lambda allows up to 250 MB.

Timeout is 60 seconds: one agent run makes several model calls (plan,
maybe summarise, search, answer). API Gateway itself caps at 30 seconds,
so a very slow run can time out at the gateway even though the Lambda
finishes — check CloudWatch logs if that happens.
"""

import json

from agent import run_agent


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

    state = run_agent(question)
    sources = sorted({c["source"] for c in state.get("chunks", [])})

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "answer": state.get("answer", ""),
                "steps": state.get("steps", []),
                "sources": sources,
                "retries": state.get("retries", 0),
            }
        ),
    }
