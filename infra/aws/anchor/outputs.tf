# For secrets/anchor-write.env (Celery) and secrets/anchor-read.env (backend):
# IHR_AUDIT_ANCHOR_ENDPOINT stays empty (AWS), IHR_AUDIT_ANCHOR_BUCKET, _REGION=ap-south-1.

output "bucket" {
  value = aws_s3_bucket.anchors.id
}

output "writer_access_key_id" {
  description = "IHR_AUDIT_ANCHOR_WRITE_KEY"
  value       = aws_iam_access_key.writer.id
}

output "writer_secret_access_key" {
  description = "IHR_AUDIT_ANCHOR_WRITE_SECRET (tofu output -raw writer_secret_access_key)"
  value       = aws_iam_access_key.writer.secret
  sensitive   = true
}

output "reader_access_key_id" {
  description = "IHR_AUDIT_ANCHOR_READ_KEY"
  value       = aws_iam_access_key.reader.id
}

output "reader_secret_access_key" {
  description = "IHR_AUDIT_ANCHOR_READ_SECRET (tofu output -raw reader_secret_access_key)"
  value       = aws_iam_access_key.reader.secret
  sensitive   = true
}
