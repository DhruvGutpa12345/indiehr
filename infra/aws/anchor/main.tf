resource "aws_s3_bucket" "anchors" {
  bucket              = var.bucket
  object_lock_enabled = true # only possible at creation; turns versioning on

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "anchors" {
  bucket = aws_s3_bucket.anchors.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Every new object is locked: no user, the account's root included, can delete it or shorten the
# lock until the time is up
resource "aws_s3_bucket_object_lock_configuration" "anchors" {
  bucket = aws_s3_bucket.anchors.id
  rule {
    default_retention {
      mode  = "COMPLIANCE"
      years = var.retention_years
    }
  }
  depends_on = [aws_s3_bucket_versioning.anchors]
}

resource "aws_s3_bucket_ownership_controls" "anchors" {
  bucket = aws_s3_bucket.anchors.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "anchors" {
  bucket                  = aws_s3_bucket.anchors.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "anchors" {
  bucket = aws_s3_bucket.anchors.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

locals {
  arn     = aws_s3_bucket.anchors.arn
  objects = "${aws_s3_bucket.anchors.arn}/anchors/*"

  bucket_policy = {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "HttpsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [local.arn, "${local.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  }

  # Celery's hourly job: add a fingerprint, nothing else. The explicit deny also covers any
  # policy attached by mistake later.
  writer_policy = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "AddOnly", Effect = "Allow", Action = ["s3:PutObject"], Resource = [local.objects] },
      {
        Sid    = "NeverRemoveOrUnlock"
        Effect = "Deny"
        Action = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:PutObjectLegalHold",
        "s3:BypassGovernanceRetention", "s3:PutBucketObjectLockConfiguration", "s3:PutBucketVersioning", "s3:PutBucketPolicy", "s3:DeleteBucket"]
        Resource = [local.arn, "${local.arn}/*"]
      },
    ]
  }

  # The verifier and the export: read and list, nothing else
  reader_policy = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "ReadObjects", Effect = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"], Resource = [local.objects] },
      { Sid = "ListAndLockSettings", Effect = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketObjectLockConfiguration", "s3:GetBucketVersioning"], Resource = [local.arn] },
    ]
  }
}

resource "aws_s3_bucket_policy" "anchors" {
  bucket     = aws_s3_bucket.anchors.id
  policy     = jsonencode(local.bucket_policy)
  depends_on = [aws_s3_bucket_public_access_block.anchors]
}

resource "aws_iam_user" "writer" {
  name = "ihr-anchor-writer"
  path = "/indiehr/"
}

resource "aws_iam_user_policy" "writer" {
  name   = "add-fingerprints-only"
  user   = aws_iam_user.writer.name
  policy = jsonencode(local.writer_policy)
}

resource "aws_iam_access_key" "writer" {
  user = aws_iam_user.writer.name
}

resource "aws_iam_user" "reader" {
  name = "ihr-anchor-reader"
  path = "/indiehr/"
}

resource "aws_iam_user_policy" "reader" {
  name   = "read-fingerprints-only"
  user   = aws_iam_user.reader.name
  policy = jsonencode(local.reader_policy)
}

resource "aws_iam_access_key" "reader" {
  user = aws_iam_user.reader.name
}
