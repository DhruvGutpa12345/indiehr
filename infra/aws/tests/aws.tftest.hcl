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

run "care_buckets_private_except_facility_pictures" {
  command = plan

  assert {
    condition     = alltrue([for k, b in aws_s3_bucket_public_access_block.care : b.block_public_acls && b.ignore_public_acls])
    error_message = "no ACLs anywhere"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.care["files"].block_public_policy && aws_s3_bucket_public_access_block.care["files"].restrict_public_buckets
    error_message = "the patient files bucket can never be made public"
  }
  assert {
    condition     = length(jsondecode(aws_s3_bucket_policy.care["files"].policy).Statement) == 1 && jsondecode(aws_s3_bucket_policy.care["files"].policy).Statement[0].Effect == "Deny"
    error_message = "the files bucket policy only denies plain HTTP"
  }
  assert {
    condition     = [for s in jsondecode(aws_s3_bucket_policy.care["facility"].policy).Statement : s.Action if s.Effect == "Allow"] == ["s3:GetObject"]
    error_message = "the facility bucket allows anonymous GetObject only (no listing)"
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

run "care_key_reaches_only_its_buckets_objects" {
  command = plan

  assert {
    condition     = toset(flatten([for s in jsondecode(aws_iam_user_policy.care.policy).Statement : s.Action])) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"])
    error_message = "CARE's key: read, write, delete objects and list, nothing else"
  }
  assert {
    condition     = alltrue([for r in flatten([for s in jsondecode(aws_iam_user_policy.care.policy).Statement : s.Resource]) : startswith(r, "arn:aws:s3:::indiehr-test-files") || startswith(r, "arn:aws:s3:::indiehr-test-facility")])
    error_message = "only the two CARE buckets (never the backups)"
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
