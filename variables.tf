variable "aws_region" {
  description = "AWS region for the demo resources."
  type        = string
  default     = "us-east-1"
}

variable "openai_api_key" {
  description = "OpenAI API key for embeddings and answers. Pass it as an environment variable, never in a file: $env:TF_VAR_openai_api_key = \"...\" (PowerShell, session only)."
  type        = string
  sensitive   = true
}
