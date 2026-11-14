# Unit tests with a mock AWS provider: no account, no API call (tofu test). P1-T8b4.

mock_provider "aws" {
  mock_data "aws_ssm_parameter" {
    defaults = { insecure_value = "ami-0123456789abcdef0" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["ap-south-1a", "ap-south-1b"] }
  }
  mock_resource "aws_eip" {
    defaults = { public_ip = "203.0.113.20" }
  }
}

mock_provider "aws" {
  alias = "backup"
}

override_resource {
  target = aws_s3_bucket.care["files"]
  values = { arn = "arn:aws:s3:::indiehr-test-files", id = "indiehr-test-files" }
}
override_resource {
  target = aws_s3_bucket.care["facility"]
  values = { arn = "arn:aws:s3:::indiehr-test-facility", id = "indiehr-test-facility" }
}
override_resource {
  target = aws_s3_bucket.backups
  values = { arn = "arn:aws:s3:::indiehr-test-backups", id = "indiehr-test-backups" }
}
override_resource {
  target = aws_s3_bucket.dumps
  values = { arn = "arn:aws:s3:::indiehr-test-dumps", id = "indiehr-test-dumps" }
}

variables {
  state_passphrase     = "a test passphrase, not real"
  account_id           = "123456789012"
  name                 = "indiehr-test"
  ssh_public_key       = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGtestkeytestkeytestkeytestkeytestkeytest owner@test"
  tenant_domain_suffix = "app.example.in"
  buckets = {
    files    = "indiehr-test-files"
    facility = "indiehr-test-facility"
    backups  = "indiehr-test-backups"
    dumps    = "indiehr-test-dumps"
  }
}

run "firewall_lets_in_only_ssh_web_and_dns" {
  command = plan

  assert {
    condition     = toset([for r in aws_vpc_security_group_ingress_rule.server : "${r.ip_protocol}/${r.from_port}"]) == toset(["tcp/22", "tcp/80", "tcp/443", "tcp/53", "udp/53"])
    error_message = "inbound rules must be exactly 22, 80, 443 and 53 (tcp + udp)"
  }
  assert {
    condition     = alltrue([for r in aws_vpc_security_group_ingress_rule.server : r.from_port == r.to_port])
    error_message = "single ports only, no ranges"
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.server) == 5
    error_message = "one inbound rule per port"
  }
  assert {
    condition     = toset([for r in aws_vpc_security_group_egress_rule.server : "${r.ip_protocol}/${r.from_port}"]) == toset(["tcp/80", "tcp/443", "tcp/587", "tcp/53", "udp/53", "udp/123"])
    error_message = "outbound: only updates, images, mail, DNS and time"
  }
}

run "server_hardening" {
  command = plan

  assert {
    condition     = aws_instance.server.metadata_options[0].http_tokens == "required" && aws_instance.server.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "instance metadata: tokens required, one hop (containers cannot reach it)"
  }
  assert {
    condition     = aws_instance.server.root_block_device[0].encrypted == true && aws_instance.server.root_block_device[0].volume_type == "gp3"
    error_message = "the disk is encrypted gp3"
  }
  assert {
    condition     = aws_instance.server.root_block_device[0].delete_on_termination == false && aws_instance.server.disable_api_termination == true
    error_message = "the database disk survives a terminated instance, and termination is switched off"
  }
  assert {
    condition     = aws_instance.server.associate_public_ip_address == false && output.public_ip == "203.0.113.20"
    error_message = "the server is reached only through its Elastic IP"
  }
  assert {
    condition     = aws_instance.server.credit_specification[0].cpu_credits == "standard"
    error_message = "a burstable instance must never buy CPU credits"
  }
  assert {
    condition     = aws_instance.server.ami == "ami-0123456789abcdef0"
    error_message = "the image comes from Canonical's SSM pointer"
  }
}

run "ubuntu_image_is_amd64_from_canonical" {
  command = plan

  assert {
    condition     = data.aws_ssm_parameter.ubuntu.name == "/aws/service/canonical/ubuntu/server/26.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
    error_message = "Ubuntu 26.04, amd64, from Canonical's parameter"
  }
}

run "care_buckets_private_pictures_too" {
  command = plan

  assert {
    condition     = alltrue([for k, b in aws_s3_bucket_public_access_block.care : b.block_public_acls && b.ignore_public_acls])
    error_message = "no ACLs anywhere"
  }
  assert {
    condition     = alltrue([for k, b in aws_s3_bucket_public_access_block.care : b.block_public_policy && b.restrict_public_buckets])
    error_message = "neither CARE bucket can ever be made public, the pictures bucket included (P1-T20b)"
  }
  assert {
    condition     = alltrue([for k, p in aws_s3_bucket_policy.care : length(jsondecode(p.policy).Statement) == 1 && jsondecode(p.policy).Statement[0].Effect == "Deny"])
    error_message = "each CARE bucket policy only denies plain HTTP (no public-read on the pictures)"
  }
  assert {
    condition     = alltrue([for k, p in aws_s3_bucket_policy.care : contains([for s in jsondecode(p.policy).Statement : s.Sid], "HttpsOnly")])
    error_message = "every bucket refuses plain HTTP"
  }
  assert {
    condition     = alltrue([for k, b in aws_s3_bucket_server_side_encryption_configuration.care : one(b.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"])
    error_message = "encrypted at rest (SSE-S3)"
  }
  assert {
    condition     = alltrue([for k, v in aws_s3_bucket_versioning.care : v.versioning_configuration[0].status == "Enabled"])
    error_message = "versions kept"
  }
  assert {
    condition     = alltrue([for k, c in aws_s3_bucket_cors_configuration.care : one(c.cors_rule).allowed_origins == toset(["https://*.app.example.in"])])
    error_message = "only the hospitals' web app may upload and download from a browser"
  }
}

run "backups_in_hyderabad_and_private" {
  command = plan

  assert {
    condition     = aws_s3_bucket_public_access_block.backups.block_public_policy && aws_s3_bucket_public_access_block.backups.restrict_public_buckets
    error_message = "backups are never public"
  }
  assert {
    condition     = output.buckets.backups.region == "ap-south-2"
    error_message = "backups in the other Indian region"
  }
  assert {
    condition     = one(aws_s3_bucket_lifecycle_configuration.backups.rule).noncurrent_version_expiration[0].noncurrent_days == 30
    error_message = "versions hidden by pgBackRest's deletes are removed after 30 days"
  }
}

run "care_keys_one_per_bucket" {
  command = plan

  assert {
    condition     = toset(keys(aws_iam_user.care)) == toset(["files", "facility"]) && aws_iam_user.care["files"].name != aws_iam_user.care["facility"].name
    error_message = "two keys: patient files and pictures (P1-T20b)"
  }
  assert {
    condition     = alltrue([for k, p in aws_iam_user_policy.care : toset(flatten([for s in jsondecode(p.policy).Statement : s.Action])) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject"])])
    error_message = "each key: read, write and delete objects, nothing else (no listing)"
  }
  assert {
    condition     = flatten([for s in jsondecode(aws_iam_user_policy.care["files"].policy).Statement : s.Resource]) == ["arn:aws:s3:::indiehr-test-files/*"]
    error_message = "the files key reaches only the patient files bucket"
  }
  assert {
    condition     = flatten([for s in jsondecode(aws_iam_user_policy.care["facility"].policy).Statement : s.Resource]) == ["arn:aws:s3:::indiehr-test-facility/*"]
    error_message = "the pictures key reaches only the pictures bucket"
  }
}

run "rejects_graviton_instances" {
  command = plan
  variables {
    instance_type = "t4g.large"
  }
  expect_failures = [var.instance_type]
}

run "non_burstable_has_no_credit_setting" {
  command = plan
  variables {
    instance_type = "m7i.large"
  }
  assert {
    condition     = length(aws_instance.server.credit_specification) == 0
    error_message = "credit settings only for t-family instances"
  }
}

run "rejects_an_unsupported_ubuntu" {
  command = plan
  variables {
    ubuntu_release = "22.04"
  }
  expect_failures = [var.ubuntu_release]
}

run "rejects_a_bad_account_id" {
  command = plan
  variables {
    account_id = "12345"
  }
  expect_failures = [var.account_id]
}

run "rejects_a_bad_ssh_key" {
  command = plan
  variables {
    ssh_public_key = "not a key"
  }
  expect_failures = [var.ssh_public_key]
}

run "dumps_locked_in_hyderabad_with_a_key_that_never_deletes" {
  command = plan

  assert {
    condition     = output.buckets.dumps.region == "ap-south-2" && aws_s3_bucket.dumps.object_lock_enabled == true
    error_message = "the dumps bucket is in Hyderabad, created with Object Lock"
  }
  assert {
    condition     = one(aws_s3_bucket_object_lock_configuration.dumps.rule).default_retention[0].mode == "GOVERNANCE" && one(aws_s3_bucket_object_lock_configuration.dumps.rule).default_retention[0].days == 30
    error_message = "Governance lock of 30 days (longer than the 28-day retention; the owner's dump-expire lifts it)"
  }
  assert {
    condition     = aws_s3_bucket_versioning.dumps.versioning_configuration[0].status == "Enabled"
    error_message = "versioning on"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.dumps.block_public_policy && aws_s3_bucket_public_access_block.dumps.restrict_public_buckets && aws_s3_bucket_public_access_block.dumps.block_public_acls
    error_message = "the dumps bucket is private"
  }
  assert {
    condition     = length([for r in aws_s3_bucket_lifecycle_configuration.dumps.rule : r if length(r.expiration) > 0 || length(r.noncurrent_version_expiration) > 0]) == 0
    error_message = "no lifecycle expiry of dumps (only the owner's dump-expire erases, with a record)"
  }
  assert {
    condition = toset(flatten([for s in jsondecode(aws_iam_user_policy.dumper.policy).Statement : s.Action if s.Effect == "Allow"])) == toset(
    ["s3:PutObject", "s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket", "s3:ListBucketVersions", "s3:GetBucketObjectLockConfiguration", "s3:GetBucketVersioning"])
    error_message = "the dumper may add, read and list (versions too) only"
  }
  assert {
    condition = length([for s in jsondecode(aws_iam_user_policy.dumper.policy).Statement : s if s.Effect == "Deny" && contains(s.Action, "s3:PutObject")
    && try(s.Condition.Null["s3:if-none-match"], "") == "true" && try(s.Condition.Bool["s3:ObjectCreationOperation"], "") == "true" && contains(s.Resource, "arn:aws:s3:::indiehr-test-dumps/*")]) == 1
    error_message = "the store refuses any object write of the dumper without If-None-Match (P1-T22a review S1: a name is never written twice)"
  }
  assert {
    condition = length([for s in jsondecode(aws_iam_user_policy.dumper.policy).Statement : s if s.Effect == "Deny" && contains(s.Action, "s3:PutObject")
    && try(s.Condition.Null["s3:if-match"], "") == "false" && contains(s.Resource, "arn:aws:s3:::indiehr-test-dumps/*")]) == 1
    error_message = "the store refuses the dumper's writes with If-Match (replacing a version it can name)"
  }
  assert {
    condition     = toset(flatten([for s in jsondecode(aws_iam_user_policy.dumper.policy).Statement : s.Resource if s.Effect == "Allow" && contains(s.Action, "s3:PutObject")])) == toset(["arn:aws:s3:::indiehr-test-dumps/dumps/*"])
    error_message = "the dumper writes under dumps/ only (allowed there alone; the denies below cover the whole bucket)"
  }
  assert {
    condition     = alltrue([for a in ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:BypassGovernanceRetention", "s3:PutObjectRetention"] : contains(flatten([for s in jsondecode(aws_iam_user_policy.dumper.policy).Statement : s.Action if s.Effect == "Deny"]), a)])
    error_message = "the dumper is explicitly denied deleting and unlocking"
  }
  assert {
    condition     = !contains(flatten([for s in jsondecode(aws_iam_user_policy.care["files"].policy).Statement : s.Resource]), "arn:aws:s3:::indiehr-test-dumps/*")
    error_message = "CARE's keys never reach the dumps"
  }
}

run "rejects_one_bucket_for_dumps_and_backups" {
  command = plan
  variables {
    buckets = {
      files    = "indiehr-test-files"
      facility = "indiehr-test-facility"
      backups  = "indiehr-test-backups"
      dumps    = "indiehr-test-backups"
    }
  }
  expect_failures = [var.buckets]
}
