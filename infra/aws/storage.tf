# CARE's file buckets (Mumbai) and the backup bucket (Hyderabad), all private except that anyone
# may read an object in the facility bucket (hospital pictures, as CARE expects). Encryption is
# S3's own (SSE-S3): no provider KMS without a new ADR (ADR-011 rules).

locals {
  care_buckets = {
    files    = var.buckets.files
    facility = var.buckets.facility
  }
  # CARE's web app uploads and downloads straight from the browser with signed links
  web_origins = ["https://*.${var.tenant_domain_suffix}"]
}

resource "aws_s3_bucket" "care" {
  for_each = local.care_buckets
  bucket   = each.value

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "care" {
  for_each = aws_s3_bucket.care
  bucket   = each.value.id
  rule {
    object_ownership = "BucketOwnerEnforced" # no ACLs at all
  }
}

resource "aws_s3_bucket_public_access_block" "care" {
  for_each           = aws_s3_bucket.care
  bucket             = each.value.id
  block_public_acls  = true
  ignore_public_acls = true
  # Only the facility bucket has a public-read policy (see below)
  block_public_policy     = each.key != "facility"
  restrict_public_buckets = each.key != "facility"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "care" {
  for_each = aws_s3_bucket.care
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "care" {
  for_each = aws_s3_bucket.care
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "care" {
  for_each = aws_s3_bucket.care
  bucket   = each.value.id
  rule {
    id     = "old-versions-and-unfinished-uploads"
    status = "Enabled"
    filter {}
    # A deleted or replaced file can be brought back for 30 days, then it is gone
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.care]
}

resource "aws_s3_bucket_cors_configuration" "care" {
  for_each = aws_s3_bucket.care
  bucket   = each.value.id
  cors_rule {
    allowed_origins = local.web_origins
    allowed_methods = ["GET", "PUT", "HEAD"]
    allowed_headers = ["*"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

# Bucket policies as plain JSON (testable without AWS): HTTPS only; the facility bucket's objects
# readable by anyone (not listable)
locals {
  https_only = { for k, b in aws_s3_bucket.care : k => {
    Sid       = "HttpsOnly"
    Effect    = "Deny"
    Principal = "*"
    Action    = "s3:*"
    Resource  = [b.arn, "${b.arn}/*"]
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  } }
  care_bucket_policy = { for k, b in aws_s3_bucket.care : k => {
    Version = "2012-10-17"
    Statement = concat([local.https_only[k]], k == "facility" ? [{
      Sid       = "PublicReadObjectsOnly"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${b.arn}/*"
    }] : [])
  } }
}

resource "aws_s3_bucket_policy" "care" {
  for_each   = aws_s3_bucket.care
  bucket     = each.value.id
  policy     = jsonencode(local.care_bucket_policy[each.key])
  depends_on = [aws_s3_bucket_public_access_block.care]
}

# ---- Backups (pgBackRest, P1-T8c): Hyderabad, private, versioned ---------------------------------
resource "aws_s3_bucket" "backups" {
  provider = aws.backup
  bucket   = var.buckets.backups

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "backups" {
  provider = aws.backup
  bucket   = aws_s3_bucket.backups.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  provider                = aws.backup
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  provider = aws.backup
  bucket   = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  provider = aws.backup
  bucket   = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

# pgBackRest expires old backups itself (P1-T8c); in a versioned bucket a delete only hides the
# object, so the hidden versions go after 30 days (longer than any retention gap between runs)
resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  provider = aws.backup
  bucket   = aws_s3_bucket.backups.id
  rule {
    id     = "expired-backups-and-unfinished-uploads"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
    expiration {
      expired_object_delete_marker = true
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.backups]
}

locals {
  backups_bucket_policy = {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "HttpsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.backups.arn, "${aws_s3_bucket.backups.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  }
}

resource "aws_s3_bucket_policy" "backups" {
  provider   = aws.backup
  bucket     = aws_s3_bucket.backups.id
  policy     = jsonencode(local.backups_bucket_policy)
  depends_on = [aws_s3_bucket_public_access_block.backups]
}

# ---- CARE's own key: its two buckets' objects, nothing else --------------------------------------
resource "aws_iam_user" "care" {
  name = "${var.name}-care"
  path = "/indiehr/"
}

locals {
  care_user_policy = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "Objects", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = [for b in aws_s3_bucket.care : "${b.arn}/*"] },
      { Sid = "List", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [for b in aws_s3_bucket.care : b.arn] },
    ]
  }
}

resource "aws_iam_user_policy" "care" {
  name   = "care-buckets"
  user   = aws_iam_user.care.name
  policy = jsonencode(local.care_user_policy)
}

# The secret is kept only in the encrypted state; read it once with
# `tofu output -raw care_secret_access_key` into secrets/app.env (BUCKET_SECRET)
resource "aws_iam_access_key" "care" {
  user = aws_iam_user.care.name
}
