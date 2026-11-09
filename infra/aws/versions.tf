# AWS (ADR-011: the documented alternative; Mumbai ap-south-1, backups in Hyderabad ap-south-2;
# never the Delhi Local Zone). OpenTofu only.
terraform {
  required_version = ">= 1.13.9, < 1.14.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.72.0"
    }
  }

  # State in an Indian S3 bucket, set at `tofu init -backend-config=backend.hcl`; encrypted
  # before it leaves this machine (it holds CARE's storage key).
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

# Credentials only from the environment or an AWS profile (AWS_PROFILE), never in a file.
provider "aws" {
  region              = "ap-south-1"
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { project = "indiehr", environment = var.name, managed_by = "opentofu (infra/aws)" }
  }
}

provider "aws" {
  alias               = "backup"
  region              = "ap-south-2"
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { project = "indiehr", environment = var.name, managed_by = "opentofu (infra/aws)" }
  }
}
