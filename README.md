# terraform-rag-agent

RAG document Q&A: ask a question, get an answer grounded **only** in five public AWS/HashiCorp docs — with citations, and a refusal instead of an invention when the answer isn't there.

Think of it as a library. The 5 documents are cut into sticky notes (chunks). The librarian (retrieval) finds the 5 notes whose fingerprints are closest to your question. The student (generation) reads **only** those 5 notes and writes the answer, naming which note each fact came from. If the answer isn't in the notes, the student says so instead of guessing.

Session 2 adds a LangGraph agent: the student grows up and does the job in steps, using tools, carrying a notebook (state) between steps.

## How it works

```
POST /ask  ->  API Gateway HTTP API
            ->  Lambda (Python 3.12, lambda_function.py)
            ->  chunks.json in S3 (93 chunks, loaded into memory)
            ->  OpenAI text-embedding-3-small (fingerprint the question)
            ->  cosine similarity, top 5 chunks
            ->  OpenAI gpt-4o-mini (answer from only those chunks)

POST /agent ->  API Gateway HTTP API
            ->  Lambda rag-agent (Python 3.12, agent_lambda.py + agent.py)
            ->  chunks.json in S3 (93 chunks, loaded into memory)
            ->  LangGraph: plan -> summarise / search -> answer
            ->  OpenAI text-embedding-3-small + gpt-4o-mini
```

## Session 2 — the agent (LangGraph)

```
question -> plan -> summarise -> search -> answer
question -> plan -> search -> answer
search -> search (once, rephrased, if nothing useful was found)
```

- **plan** — reads the question, writes the plan: which tool first.
- **search_docs** — the librarian: top 5 chunks by cosine similarity.
- **summarise_doc** — reads a whole document (all its chunks, in order) and writes a short summary into the notebook.
- **answer** — writes the final answer from the notebook only, with citations, or "Not in these documents."
- **State (the notebook)** carries the question, the plan, the chunks found, the summary, the retry count, and the final answer. If a search comes back empty, the agent retries once with a rephrased question, then reports honestly. No inventing.

## Verified (Nov 22, 2026)

Session 1 — RAG Q&A, live through the deployed API (3/3):

| Question | Result |
|---|---|
| What is a Lambda function handler? | Answered from 1 chunk, cited `How-Lambda-works.pdf`. |
| What is a Lambda execution role, and what permission must a function's resource-based policy grant another AWS service to invoke it? | Answered from 2 chunks, cited `How-Lambda-works.pdf`. |
| How much does it cost to run a Lambda function for a million requests? | Answered **"Not in these documents."** Pricing isn't in the docs, and the system refuses rather than inventing a price. |

Session 2 — agent, local run: the two-step question "Summarise the EKS document, then what does it say about the control plane?" ran plan → summarise (`Amazon-EKS.pdf`, 7 chunks) → search (top 5 all from `EKS-Control-Plane.pdf`, best score 0.62) → answer, both parts correct with citation.

## Eval score (Nov 22, 2026) — 13.0/15

15 questions with an answer key, run through the agent and marked by gpt-4o-mini as judge (right / partly / wrong, plus citation correct or not). Right: 11. Partly: 4. Wrong: 0. Citations correct: 15/15.

The 4 partials, published honestly:

- Q4 (what the API Gateway tutorial builds): right facts, plus extra detail about CRUD operations.
- Q6 (the `eksctl create cluster` command): right command, plus extra detail.
- Q9 (configuring a Terraform provider): missing detail about the `required_providers` block.
- Q11 (deleting an EKS cluster): right facts, missed the simpler expected command.

Both unanswerable questions were refused correctly ("Not in these documents"), and both two-step questions scored right. Full per-question results in `eval_results.json`.

## Honest limits

- **S3 + in-memory search is a demo, not a production vector DB.** All 93 chunks load into the Lambda's memory on a cold start. That stops scaling at thousands of chunks; a real system would use a vector database.
- **Only these 5 documents.** Answers come from them and nothing else.
- **The judge is also gpt-4o-mini.** The eval score is a model marking a model, disclosed here rather than hidden. The answer key and expected sources are fixed in `eval.py`, so anyone can re-run it.
- **Dev only.** Deploy, verify, destroy in the same session. Never leave it running overnight.

## The 5 documents

| Document | Source |
|---|---|
| How Lambda works | https://docs.aws.amazon.com/lambda/latest/dg/concepts-basics.html |
| Tutorial: Using Lambda with API Gateway | https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway-tutorial.html |
| Get started with Amazon EKS – eksctl | https://docs.aws.amazon.com/eks/latest/userguide/getting-started-eksctl.html |
| EKS Control Plane (best practices) | https://docs.aws.amazon.com/eks/latest/best-practices/control-plane.html |
| Configure Terraform providers | https://developer.hashicorp.com/terraform/tutorials/configuration-language/configure-providers |

Never upload internal or customer documents. The demo answers questions about the documents themselves, which makes the citations easy to check.

## Run it

Prerequisites: AWS CLI configured, Terraform >= 1.2, Python 3.12, an OpenAI API key with a small credit balance.

**1. Ingest (once): save the 5 docs as PDFs in `docs/`, then**

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install pypdf openai boto3
$env:OPENAI_API_KEY = "your-key-here"
aws s3 mb s3://rag-docs-sdeystack --region us-east-1
python ingest.py
```

That writes `chunks.json` (93 chunks) to disk and to S3. Save each page with Ctrl+P in the browser — **not** the site's own PDF button, which downloads the whole guide (22–36 MB per file).

**2. Deploy:**

Package the agent's dependencies first — LangGraph and the OpenAI SDK are not in the Lambda runtime. On Windows, install the **Linux** versions or Lambda cannot load them (the Windows build of pydantic-core fails on Lambda with an import error):

```powershell
pip install --target agent_package --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: langgraph openai
Copy-Item agent.py, agent_lambda.py agent_package/
```

Then apply (the bucket is created fresh — Session 1's was destroyed, so no import), and upload `chunks.json` after apply, before testing:

```powershell
$env:TF_VAR_openai_api_key = "your-key-here"
terraform init
terraform apply
aws s3 cp chunks.json s3://rag-docs-sdeystack/chunks.json
```

**3. Ask it (PowerShell):**

```powershell
$api = terraform output -raw api_url
Invoke-RestMethod -Method Post -Uri "$api/ask" -ContentType "application/json" -Body '{"question": "What is a Lambda function handler?"}'
```

**3b. Ask the agent (PowerShell):**

```powershell
$r = Invoke-RestMethod -Method Post -Uri "$api/agent" -ContentType "application/json" -Body '{"question": "Summarise the EKS document, then what does it say about the control plane?"}'
$r.steps
$r.sources
$r.answer
```

The agent call makes several model calls, so it can take around 20 seconds; API Gateway caps at 30.

**3c. Run the agent (local):**

```powershell
pip install langgraph
python agent.py "Summarise the EKS document, then what does it say about the control plane?"
```

It prints every step it takes (plan, summarise, search, answer) so you can see the graph working.

**3d. Run the evals (local):**

```powershell
python eval.py
```

Runs the 15 questions through the agent and marks them with gpt-4o-mini as judge. Takes a few minutes, a few cents. Results save to `eval_results.json`.

**4. Destroy (same session):**

```powershell
aws s3 rm s3://rag-docs-sdeystack --recursive
terraform destroy
```

Empty the bucket first — Terraform cannot delete a bucket that still holds `chunks.json`. Verify afterwards: `aws s3 ls` should no longer list `rag-docs-sdeystack`, and `terraform state list` should print nothing.

Then verify nothing is left running (`aws s3 ls`, Lambda console) so the next month's bill stays $0.

## Cost

A few cents per session. Embeddings are ~$0.02 per million tokens; gpt-4o-mini is pennies per question. Lambda, API Gateway and S3 stay inside the AWS Free Tier at this volume.

## Repo layout

| File | What it is |
|---|---|
| `lambda_function.py` | The Lambda handler (same logic as `ask.py`, stdlib only) |
| `ask.py` | Local test version — no Lambda needed |
| `agent.py` | LangGraph agent (plan / search / summarise / answer, state + one retry) |
| `eval.py` | 15-question eval set + gpt-4o-mini judge |
| `eval_results.json` | Full per-question eval results |
| `ingest.py` | PDFs → chunks → embeddings → `chunks.json` in S3 |
| `main.tf`, `variables.tf`, `outputs.tf` | All cloud resources |
| `docs/` | The 5 PDFs (not committed — save them yourself) |
| `chunks.json` | Generated by `ingest.py` (not committed) |
