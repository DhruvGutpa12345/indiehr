# CARE's file buckets (Mumbai) and the backup bucket (Hyderabad), all private: the facility bucket
# (staff photos, facility covers) too, read through 24-hour signed links (P1-T20b, owner answer
# Q-T3). Encryption is S3's own (SSE-S3): no provider KMS without a new ADR (ADR-011 rules).

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
  for_each                = aws_s3_bucket.care
  bucket                  = each.value.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
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

# Bucket policies as plain JSON (testable without AWS): HTTPS only, nothing for anyone
locals {
  care_bucket_policy = { for k, b in aws_s3_bucket.care : k => {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "HttpsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [b.arn, "${b.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
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

# ---- CARE's keys: one per bucket (P1-T20b), each its own bucket's objects, nothing else ------------
# files: patient files and reports (FILE_UPLOAD_KEY); pictures: staff photos and facility covers
# (FACILITY_S3_KEY). A picture key can never reach a patient file; the backend refuses to start
# with one key for both (ihr_platform/settings/secure.py).
resource "aws_iam_user" "care" {
  for_each = local.care_buckets
  name     = "${var.name}-care-${each.key}"
  path     = "/indiehr/"
}

locals {
  care_user_policy = { for k, b in aws_s3_bucket.care : k => {
    Version = "2012-10-17"
    Statement = [
      { Sid = "Objects", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${b.arn}/*"] },
    ]
  } }
}

resource "aws_iam_user_policy" "care" {
  for_each = aws_iam_user.care
  name     = "care-${each.key}-bucket"
  user     = each.value.name
  policy   = jsonencode(local.care_user_policy[each.key])
}

# The secrets are kept only in the encrypted state; read each once with
# `tofu output -json care_keys` into secrets/app.env (FILE_UPLOAD_* and FACILITY_S3_*)
resource "aws_iam_access_key" "care" {
  for_each = aws_iam_user.care
  user     = each.value.name
}

# ---- Nightly per-hospital dumps (P1-T22a): Hyderabad, a bucket of their own, locked -----------------
# Built like the log archive's (ADR-014): versioning and a Governance Object Lock of 30 days, a
# little longer than the longest retention (4 Sundays, 28 days). The dumper's key adds and reads
# under dumps/ and reads erasure/, never deletes; the owner's dump-expire (deploy/dumps/expire.py)
# erases past 14 nights / 4 Sundays with the account's own admin rights (it lifts Governance),
# after writing an authenticated erasure record. No lifecycle expiry of dumps: a silent delete
# would break the per-hospital chain. The pgBackRest key (backups bucket) never reaches it.
resource "aws_s3_bucket" "dumps" {
  provider            = aws.backup
  bucket              = var.buckets.dumps
  object_lock_enabled = true # only possible at creation; turns versioning on

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "dumps" {
  provider = aws.backup
  bucket   = aws_s3_bucket.dumps.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "dumps" {
  provider = aws.backup
  bucket   = aws_s3_bucket.dumps.id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 30
    }
  }
  depends_on = [aws_s3_bucket_versioning.dumps]
}

resource "aws_s3_bucket_ownership_controls" "dumps" {
  provider = aws.backup
  bucket   = aws_s3_bucket.dumps.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "dumps" {
  provider                = aws.backup
  bucket                  = aws_s3_bucket.dumps.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dumps" {
  provider = aws.backup
  bucket   = aws_s3_bucket.dumps.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Only unfinished multipart uploads go by themselves (a dump over 4 GiB goes up in parts)
resource "aws_s3_bucket_lifecycle_configuration" "dumps" {
  provider = aws.backup
  bucket   = aws_s3_bucket.dumps.id
  rule {
    id     = "unfinished-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.dumps]
}

locals {
  dumps_arn = aws_s3_bucket.dumps.arn
  dumps_bucket_policy = {
    Version = "2012-10-17"
    Statement = [{
      Sid       = "HttpsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [local.dumps_arn, "${local.dumps_arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  }
  # The dumper: add and read under dumps/ (the read-back check), read the erasure records (the
  # chain goes through them), list (versions too: a name written twice or hidden shows; P1-T22a
  # review S1), read the lock settings. The explicit denies also cover any policy attached by
  # mistake later: never delete or unlock, and never write a name again. Every object write
  # must carry If-None-Match (S3's conditional-write keys; CreateMultipartUpload and UploadPart
  # are not object-creation operations and cannot carry it, CompleteMultipartUpload can), and
  # never If-Match (which would replace a named version)
  dumper_policy = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "AddAndReadDumps", Effect = "Allow", Action = ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion"], Resource = ["${local.dumps_arn}/dumps/*"] },
      { Sid = "ReadErasureRecords", Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.dumps_arn}/erasure/*"] },
      { Sid = "ListAndLockSettings", Effect = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketObjectLockConfiguration", "s3:GetBucketVersioning"], Resource = [local.dumps_arn] },
      {
        Sid       = "NeverOverwriteANameWithoutIfNoneMatch"
        Effect    = "Deny"
        Action    = ["s3:PutObject"]
        Resource  = ["${local.dumps_arn}/*"]
        Condition = { Null = { "s3:if-none-match" = "true" }, Bool = { "s3:ObjectCreationOperation" = "true" } }
      },
      {
        Sid       = "NeverReplaceAVersionWithIfMatch"
        Effect    = "Deny"
        Action    = ["s3:PutObject"]
        Resource  = ["${local.dumps_arn}/*"]
        Condition = { Null = { "s3:if-match" = "false" } }
      },
      {
        Sid    = "NeverRemoveOrUnlock"
        Effect = "Deny"
        Action = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:PutObjectLegalHold",
          "s3:BypassGovernanceRetention", "s3:PutBucketObjectLockConfiguration", "s3:PutBucketVersioning",
        "s3:PutLifecycleConfiguration", "s3:PutBucketPolicy", "s3:DeleteBucket"]
        Resource = [local.dumps_arn, "${local.dumps_arn}/*"]
      },
    ]
  }
}

resource "aws_s3_bucket_policy" "dumps" {
  provider   = aws.backup
  bucket     = aws_s3_bucket.dumps.id
  policy     = jsonencode(local.dumps_bucket_policy)
  depends_on = [aws_s3_bucket_public_access_block.dumps]
}

resource "aws_iam_user" "dumper" {
  name = "${var.name}-dumper"
  path = "/indiehr/"
}

resource "aws_iam_user_policy" "dumper" {
  name   = "add-and-read-dumps-only"
  user   = aws_iam_user.dumper.name
  policy = jsonencode(local.dumper_policy)
}

# Read once with `tofu output -json dumper_key` into scripts/staging.sh dumps-init (stdin)
resource "aws_iam_access_key" "dumper" {
  user = aws_iam_user.dumper.name
}
