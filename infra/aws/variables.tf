variable "state_passphrase" {
  description = "Encrypts the state and plans (16+ characters). Give it as TF_VAR_state_passphrase; keep it in the owner's password manager."
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "state_passphrase must have at least 16 characters."
  }
}

variable "account_id" {
  description = "The AWS account this may touch (12 digits): a wrong profile then fails instead of creating things elsewhere."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id is the 12-digit AWS account number."
  }
}

variable "name" {
  description = "Prefix of every resource, e.g. indiehr-staging."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.name))
    error_message = "name: 3-31 characters, a-z, 0-9 and -, starting with a letter."
  }
}

variable "instance_type" {
  description = "x86 only (our images are built for amd64 on the owner's machine): 8 GB for staging, e.g. t3.large or m7i.large. ARM (t4g) would need multi-arch images."
  type        = string
  default     = "t3.large"
  validation {
    condition     = !can(regex("^[a-z0-9]+g[a-z]*\\.", var.instance_type))
    error_message = "instance_type must be an x86 type (not Graviton: t4g, m7g, ...): the images are amd64."
  }
}

variable "ubuntu_release" {
  description = "Ubuntu LTS for the server (deploy/host/bootstrap.sh accepts 26.04 and 24.04). Owner, 09-11-2026: 26.04."
  type        = string
  default     = "26.04"
  validation {
    condition     = contains(["26.04", "24.04"], var.ubuntu_release)
    error_message = "ubuntu_release must be 26.04 or 24.04."
  }
}

variable "disk_gb" {
  description = "Root disk (gp3, encrypted): database, SeaweedFS if used, 180-day journal, images."
  type        = number
  default     = 100
  validation {
    condition     = var.disk_gb >= 60 && var.disk_gb <= 1000
    error_message = "disk_gb between 60 and 1000."
  }
}

variable "ssh_public_key" {
  description = "The owner's SSH public key for the first sign-in (ed25519 preferred)."
  type        = string
  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+( [^\\n]*)?$", trimspace(var.ssh_public_key)))
    error_message = "ssh_public_key must be one OpenSSH public key line."
  }
}

variable "ssh_cidrs" {
  description = "Who may reach SSH (22/tcp). Owner, 09-11-2026 (Q-E9): open to the internet, key-only and rate-limited by bootstrap.sh."
  type        = list(string)
  default     = ["0.0.0.0/0"]
  validation {
    condition     = length(var.ssh_cidrs) > 0 && alltrue([for c in var.ssh_cidrs : can(cidrhost(c, 0))])
    error_message = "ssh_cidrs must be a non-empty list of IPv4 CIDRs."
  }
}

variable "tenant_domain_suffix" {
  description = "Where hospitals live (IHR_TENANT_DOMAIN_SUFFIX, e.g. app.amhcloud.in): the web app's origins allowed to upload and download (CORS)."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.tenant_domain_suffix))
    error_message = "tenant_domain_suffix must be a domain name."
  }
}

variable "buckets" {
  description = "S3 bucket names (globally unique): CARE's files and pictures (Mumbai), the backups and the nightly dumps (Hyderabad). The audit fingerprint bucket is in infra/aws/anchor, in a separate account."
  type = object({
    files    = string
    facility = string
    backups  = string
    dumps    = string
  })
  validation {
    condition     = alltrue([for b in values(var.buckets) : can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", b))]) && length(distinct(values(var.buckets))) == 4
    error_message = "buckets: four different names, 3-63 characters, a-z, 0-9 and -."
  }
}
