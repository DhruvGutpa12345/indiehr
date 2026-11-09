# Unit tests with a mock AWS provider (P1-T8b4).

mock_provider "aws" {}

override_resource {
  target = aws_s3_bucket.anchors
  values = { arn = "arn:aws:s3:::indiehr-test-anchors", id = "indiehr-test-anchors" }
}

variables {
  state_passphrase = "a test passphrase, not real"
  account_id       = "210987654321"
  bucket           = "indiehr-test-anchors"
}

run "compliance_lock_for_ten_years" {
  command = plan

  assert {
    condition     = aws_s3_bucket.anchors.object_lock_enabled == true
    error_message = "the bucket is created with Object Lock"
  }
  assert {
    condition     = aws_s3_bucket_object_lock_configuration.anchors.rule[0].default_retention[0].mode == "COMPLIANCE" && aws_s3_bucket_object_lock_configuration.anchors.rule[0].default_retention[0].years == 10
    error_message = "COMPLIANCE, 10 years"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.anchors.block_public_policy && aws_s3_bucket_public_access_block.anchors.restrict_public_buckets
    error_message = "never public"
  }
}

run "writer_only_adds" {
  command = plan

  assert {
    condition     = [for s in jsondecode(aws_iam_user_policy.writer.policy).Statement : s.Action if s.Effect == "Allow"] == [["s3:PutObject"]]
    error_message = "the writer may only add objects"
  }
  assert {
    condition     = alltrue([for a in ["s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:BypassGovernanceRetention", "s3:PutBucketObjectLockConfiguration"] : contains(flatten([for s in jsondecode(aws_iam_user_policy.writer.policy).Statement : s.Action if s.Effect == "Deny"]), a)])
    error_message = "removing, unlocking and re-locking are denied explicitly"
  }
  assert {
    condition     = [for s in jsondecode(aws_iam_user_policy.writer.policy).Statement : s.Resource if s.Effect == "Allow"] == [["arn:aws:s3:::indiehr-test-anchors/anchors/*"]]
    error_message = "only under anchors/"
  }
}

run "reader_only_reads" {
  command = plan

  assert {
    condition     = alltrue([for a in flatten([for s in jsondecode(aws_iam_user_policy.reader.policy).Statement : s.Action]) : startswith(a, "s3:Get") || startswith(a, "s3:List")])
    error_message = "the reader may only read and list"
  }
}

run "rejects_short_retention" {
  command = plan
  variables {
    retention_years = 5
  }
  expect_failures = [var.retention_years]
}
