"""Project 4, Session 2 — Evals.

A test paper with an answer key: 15 questions, each with the expected
answer and the document it should come from. The script runs every
question through the agent, then asks gpt-4o-mini (the judge) to mark it:
right / partly right / wrong, and whether the citation names the right
document. Unanswerable questions are right only if the agent says
"Not in these documents."

The score — including the failures — goes in the README. A 12/15 with
honest failures is stronger evidence than a claimed 15/15.

Run from the terraform-rag-agent folder, in the Python 3.12 venv:
    .venv\\Scripts\\Activate.ps1
    $env:OPENAI_API_KEY = "your-key-here"   (session only, never in a file)
    python eval.py

Results are saved to eval_results.json. Takes a few minutes (15 agent
runs + 15 judge calls, a few cents).
"""

import json
from pathlib import Path

from agent import chat, load_chunks, run_agent

RESULTS_FILE = Path("eval_results.json")

# Each item: the question, what a good answer must say, and which
# document(s) it must come from. Empty sources = unanswerable on purpose.
EVALS = [
    {
        "id": 1,
        "question": "What is a Lambda function handler?",
        "expected": "The method in the function code that processes events.",
        "sources": ["How-Lambda-works.pdf"],
    },
    {
        "id": 2,
        "question": "What is a Lambda execution role?",
        "expected": "An IAM role the function assumes, granting it permission "
        "to access AWS services and resources.",
        "sources": ["How-Lambda-works.pdf"],
    },
    {
        "id": 3,
        "question": "What permission must a function's resource-based policy grant "
        "another AWS service to invoke it?",
        "expected": "lambda:InvokeFunction.",
        "sources": ["How-Lambda-works.pdf"],
    },
    {
        "id": 4,
        "question": "What does the Lambda with API Gateway tutorial build?",
        "expected": "An API that invokes a Lambda function when it receives a request.",
        "sources": ["Using-Lambda-with-API-Gateway.pdf"],
    },
    {
        "id": 5,
        "question": "What tools must be installed before creating an EKS cluster with eksctl?",
        "expected": "The AWS CLI, kubectl, and eksctl.",
        "sources": ["Amazon-EKS.pdf"],
    },
    {
        "id": 6,
        "question": "What command creates an EKS cluster with eksctl?",
        "expected": "eksctl create cluster.",
        "sources": ["Amazon-EKS.pdf"],
    },
    {
        "id": 7,
        "question": "How does EKS keep the control plane available?",
        "expected": "It runs multiple API server nodes across Availability Zones "
        "and replaces unhealthy control plane nodes.",
        "sources": ["EKS-Control-Plane.pdf"],
    },
    {
        "id": 8,
        "question": "What does EKS do when the load on the control plane increases?",
        "expected": "It monitors the load and scales the control plane instances.",
        "sources": ["EKS-Control-Plane.pdf"],
    },
    {
        "id": 9,
        "question": "How do you configure a Terraform provider?",
        "expected": "With a provider block, declaring the source and version, "
        "usually inside a required_providers block.",
        "sources": ["Configure-Terraform-providers.pdf"],
    },
    {
        "id": 10,
        "question": "What is the purpose of the required_providers block in Terraform?",
        "expected": "To declare which providers a configuration needs, with their "
        "source addresses and version constraints.",
        "sources": ["Configure-Terraform-providers.pdf"],
    },
    {
        "id": 11,
        "question": "How do you delete an EKS cluster created with eksctl?",
        "expected": "eksctl delete cluster.",
        "sources": ["Amazon-EKS.pdf"],
    },
    {
        "id": 12,
        "question": "Summarise the EKS document, then what does it say about the control plane?",
        "expected": "A summary of the eksctl getting-started guide, plus control "
        "plane facts: managed service, multi-AZ API servers, unhealthy nodes "
        "replaced, load monitored and scaled.",
        "sources": ["Amazon-EKS.pdf", "EKS-Control-Plane.pdf"],
    },
    {
        "id": 13,
        "question": "Summarise the Lambda concepts document, then what is an execution role?",
        "expected": "A summary of how Lambda works, plus: the execution role is "
        "the IAM role the function assumes to access AWS services.",
        "sources": ["How-Lambda-works.pdf"],
    },
    {
        "id": 14,
        "question": "How much does it cost to run a Lambda function for a million requests?",
        "expected": "Not in these documents.",
        "sources": [],
    },
    {
        "id": 15,
        "question": "What is the default VPC CIDR block for an EKS cluster?",
        "expected": "Not in these documents.",
        "sources": [],
    },
]

JUDGE_PROMPT = (
    "You mark one answer against the answer key. Reply with JSON only:\n"
    '{"verdict": "right" | "partly" | "wrong", '
    '"citation_ok": true | false, "reason": "one short sentence"}\n\n'
    "Verdict rules:\n"
    "- right: the answer says what the expected answer says (same facts, "
    "own words fine). For an unanswerable question, right means it said "
    "'Not in these documents.'\n"
    "- partly: some expected facts are there but key ones are missing or wrong.\n"
    "- wrong: the expected facts are missing, contradicted, or invented.\n"
    "Citation rules: citation_ok is true if the answer names at least one of "
    "the expected source files. For an unanswerable question it is true if "
    "the answer refuses (no citation needed).\n\n"
    "Question: {question}\n"
    "Expected answer: {expected}\n"
    "Expected source file(s): {sources}\n\n"
    "Agent's answer:\n{answer}"
)


def judge(item, answer):
    """One judge call. Returns the verdict dict."""
    sources = ", ".join(item["sources"]) if item["sources"] else "(none - unanswerable)"
    # NOTE: .format() cannot be used here — the prompt itself contains
    # literal {curly braces} in the JSON example, which .format() mistakes
    # for placeholders (that crashed the first run on '"verdict"').
    # .replace() only swaps the four named slots and leaves braces alone.
    prompt = (
        JUDGE_PROMPT.replace("{question}", item["question"])
        .replace("{expected}", item["expected"])
        .replace("{sources}", sources)
        .replace("{answer}", answer)
    )
    raw = chat([{"role": "user", "content": prompt}])
    try:
        return json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
    except Exception:
        return {"verdict": "wrong", "citation_ok": False, "reason": "Judge reply unreadable."}


def main():
    print(f"Loaded {len(load_chunks())} chunks. Running {len(EVALS)} eval questions...\n")
    results = []
    for item in EVALS:
        print(f"Question {item['id']}/{len(EVALS)}: {item['question'][:60]}...")
        state = run_agent(item["question"])
        answer = state.get("answer", "(no answer)")
        verdict = judge(item, answer)
        results.append(
            {
                "id": item["id"],
                "question": item["question"],
                "expected": item["expected"],
                "sources": item["sources"],
                "answer": answer,
                "verdict": verdict["verdict"],
                "citation_ok": verdict["citation_ok"],
                "reason": verdict["reason"],
                "steps": state.get("steps", []),
                "retries": state.get("retries", 0),
            }
        )
        mark = {"right": "RIGHT", "partly": "PARTLY", "wrong": "WRONG"}[verdict["verdict"]]
        cit = "citation ok" if verdict["citation_ok"] else "citation MISSING/WRONG"
        print(f"  -> {mark}, {cit}: {verdict['reason']}\n")

    right = sum(1 for r in results if r["verdict"] == "right")
    partly = sum(1 for r in results if r["verdict"] == "partly")
    wrong = sum(1 for r in results if r["verdict"] == "wrong")
    cit_ok = sum(1 for r in results if r["citation_ok"])
    score = right + 0.5 * partly
    print("=== Eval score ===")
    print(f"Right: {right} | Partly: {partly} | Wrong: {wrong} | Score: {score}/{len(EVALS)}")
    print(f"Citations correct: {cit_ok}/{len(EVALS)}")
    if wrong or partly:
        print("Failed / partial:")
        for r in results:
            if r["verdict"] in ("wrong", "partly"):
                print(f"  Q{r['id']}: {r['question'][:70]}... -> {r['reason']}")

    RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved full results to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
