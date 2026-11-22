# Project 4 — all cloud resources in Terraform (Sessions 1 + 2).
#
# What this builds (dev only — deploy, verify, destroy in one session):
#   - S3 bucket rag-docs-sdeystack (created fresh by this apply; Session 1's
#     bucket was destroyed after that session, so NO import this time).
#     After apply, upload chunks.json before testing:
#         aws s3 cp chunks.json s3://rag-docs-sdeystack/chunks.json
#   - Lambda rag-agent-ask (Python 3.12, lambda_function.py) — Session 1 RAG Q&A
#   - Lambda rag-agent (Python 3.12, agent_lambda.py + agent.py) — Session 2
#     LangGraph agent. Its dependencies (langgraph, openai) are NOT in the
#     Lambda runtime, so package them by hand first:
#         pip install --target agent_package langgraph openai
#         Copy-Item agent.py, agent_lambda.py agent_package/
#     Terraform zips agent_package/ at apply time.
#   - IAM role letting both Lambdas read only chunks.json and write logs
#   - API Gateway HTTP API: POST /ask -> ask Lambda, POST /agent -> agent Lambda
#
# State is local for this dev-only demo (terraform.tfstate, gitignored).
# The other portfolio projects use S3 remote state; see their repos.

terraform {
  required_version = ">= 1.2"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# --- S3: the docs bucket (created fresh by this apply) ----------------------

resource "aws_s3_bucket" "docs" {
  bucket = "rag-docs-sdeystack"

  tags = {
    Name    = "rag-docs-sdeystack"
    Project = "terraform-rag-agent"
  }
}

# --- Lambda: the code + its permissions -------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/lambda_function.py"
  output_path = "${path.module}/lambda_function.zip"
}

data "archive_file" "agent_zip" {
  type        = "zip"
  source_dir  = "${path.module}/agent_package"
  output_path = "${path.module}/agent.zip"
}

resource "aws_iam_role" "lambda" {
  name = "rag-agent-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  tags = {
    Project = "terraform-rag-agent"
  }
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3" {
  name = "rag-agent-lambda-s3"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action   = ["s3:GetObject"]
      Effect   = "Allow"
      Resource = "${aws_s3_bucket.docs.arn}/chunks.json"
    }]
  })
}

resource "aws_lambda_function" "ask" {
  function_name = "rag-agent-ask"
  role          = aws_iam_role.lambda.arn
  handler       = "lambda_function.lambda_handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      BUCKET         = aws_s3_bucket.docs.id
      CHUNKS_KEY     = "chunks.json"
      OPENAI_API_KEY = var.openai_api_key
    }
  }

  tags = {
    Project = "terraform-rag-agent"
  }
}

resource "aws_lambda_function" "agent" {
  function_name = "rag-agent"
  role          = aws_iam_role.lambda.arn
  handler       = "agent_lambda.lambda_handler"
  runtime       = "python3.12"
  timeout       = 60
  memory_size   = 512

  filename         = data.archive_file.agent_zip.output_path
  source_code_hash = data.archive_file.agent_zip.output_base64sha256

  environment {
    variables = {
      CHUNKS_S3_BUCKET = aws_s3_bucket.docs.id
      OPENAI_API_KEY   = var.openai_api_key
    }
  }

  tags = {
    Project = "terraform-rag-agent"
  }
}

# --- API Gateway HTTP API: POST /ask and POST /agent -------------------------

resource "aws_apigatewayv2_api" "rag" {
  name          = "rag-agent-api"
  protocol_type = "HTTP"

  tags = {
    Project = "terraform-rag-agent"
  }
}

resource "aws_apigatewayv2_integration" "ask" {
  api_id                 = aws_apigatewayv2_api.rag.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.ask.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "ask" {
  api_id    = aws_apigatewayv2_api.rag.id
  route_key = "POST /ask"
  target    = "integrations/${aws_apigatewayv2_integration.ask.id}"
}

resource "aws_apigatewayv2_integration" "agent" {
  api_id                 = aws_apigatewayv2_api.rag.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.agent.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "agent" {
  api_id    = aws_apigatewayv2_api.rag.id
  route_key = "POST /agent"
  target    = "integrations/${aws_apigatewayv2_integration.agent.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.rag.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ask.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rag.execution_arn}/*/*"
}

resource "aws_lambda_permission" "api_agent" {
  statement_id  = "AllowAPIGatewayInvokeAgent"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rag.execution_arn}/*/*"
}
