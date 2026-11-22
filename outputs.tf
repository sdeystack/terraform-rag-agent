output "api_url" {
  description = "Base URL of the HTTP API. Ask a question with POST {api_url}/ask and a JSON body {\"question\": \"...\"}."
  value       = aws_apigatewayv2_api.rag.api_endpoint
}

output "bucket" {
  description = "S3 bucket holding chunks.json."
  value       = aws_s3_bucket.docs.id
}
