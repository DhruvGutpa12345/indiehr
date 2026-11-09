# The same outputs as infra/e2e (ADR-011), plus CARE's key (AWS can make it here).

output "public_ip" {
  description = "The server's fixed public IPv4 address (Elastic IP): the registrar's A records (*.app, acme-ns) point here."
  value       = aws_eip.server.public_ip
}

output "ssh" {
  description = "First sign-in, then deploy/host/README.md (bootstrap) and scripts/deploy.sh."
  value       = "ssh ubuntu@${aws_eip.server.public_ip}"
}

output "s3_endpoint" {
  description = "S3 endpoint for CARE's buckets (BUCKET_ENDPOINT / BUCKET_EXTERNAL_ENDPOINT); BUCKET_REGION=ap-south-1."
  value       = "https://s3.ap-south-1.amazonaws.com"
}

output "buckets" {
  description = "Bucket names and where each lives."
  value = {
    files    = { name = aws_s3_bucket.care["files"].id, region = "ap-south-1" }
    facility = { name = aws_s3_bucket.care["facility"].id, region = "ap-south-1" }
    backups  = { name = aws_s3_bucket.backups.id, region = "ap-south-2" }
  }
}

output "care_access_key_id" {
  description = "BUCKET_KEY in secrets/app.env."
  value       = aws_iam_access_key.care.id
}

output "care_secret_access_key" {
  description = "BUCKET_SECRET in secrets/app.env (tofu output -raw care_secret_access_key)."
  value       = aws_iam_access_key.care.secret
  sensitive   = true
}
