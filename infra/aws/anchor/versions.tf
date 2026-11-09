# The audit fingerprint bucket (ADR-010 §9, ADR-011 §3) on AWS: Object Lock in COMPLIANCE mode
# for 10 years, in its OWN AWS account, whose credentials neither the server nor its admins
# hold. Run by the owner only, with that account's profile. Own state, separate from infra/aws.
terraform {
  required_version = ">= 1.13.9, < 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.72.0"
    }
  }

  backend "s3" {}

  encryption {
    key_provider "pbkdf2" "state" {
      passphrase = var.state_passphrase
    }
    method "aes_gcm" "state" {
      keys = key_provider.pbkdf2.state
    }
    state {
      method   = method.aes_gcm.state
      enforced = true
    }
    plan {
      method   = method.aes_gcm.state
      enforced = true
    }
  }
}

provider "aws" {
  region              = "ap-south-1"
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { project = "indiehr", purpose = "audit-fingerprints", managed_by = "opentofu (infra/aws/anchor)" }
  }
}
