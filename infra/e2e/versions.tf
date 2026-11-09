# E2E Networks (ADR-011: target host, Delhi NCR; backups in Chennai). OpenTofu only.
terraform {
  required_version = ">= 1.13.9, < 1.14.0"

  required_providers {
    e2e = {
      source  = "e2eterraformprovider/e2e"
      version = "2.2.12"
    }
  }

  # State lives in an Indian S3-compatible bucket (an E2E object store), set at `tofu init
  # -backend-config=backend.hcl` (see backend.hcl.example). It is encrypted before it leaves
  # this machine: it holds every resource id and the reserved address.
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

# Credentials only from the environment: SERVICE_API_KEY and SERVICE_AUTH_TOKEN (E2E MyAccount,
# API tokens). Never in a file. The provider sends the API key in the request URL: TF_LOG output
# and some network errors show it, so never share such output unredacted (infra/README.md).
provider "e2e" {}
