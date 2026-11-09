variable "state_passphrase" {
  description = "Encrypts the state and plans (16+ characters). Give it as TF_VAR_state_passphrase; keep it in the owner's password manager: without it the state cannot be read."
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "state_passphrase must have at least 16 characters."
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

variable "project_id" {
  description = "E2E MyAccount project id (a number)."
  type        = string
  validation {
    condition     = can(regex("^[0-9]+$", var.project_id))
    error_message = "project_id is the project's number."
  }
}

variable "location" {
  description = "Where the server and its file buckets live: Delhi (Delhi NCR, ADR-011) or Chennai."
  type        = string
  default     = "Delhi"
  validation {
    condition     = contains(["Delhi", "Chennai"], var.location)
    error_message = "location must be Delhi or Chennai (E2E's Indian regions; Mumbai is discontinued)."
  }
}

variable "backup_location" {
  description = "Where the backup bucket lives: the other Indian region (ADR-011: Chennai)."
  type        = string
  default     = "Chennai"
  validation {
    condition     = contains(["Delhi", "Chennai"], var.backup_location)
    error_message = "backup_location must be Delhi or Chennai."
  }
}

variable "plan" {
  description = "E2E plan string for the server (4 vCPU / 8 GB, C3 series, ADR-011), as E2E's API lists it for the image, e.g. C3-4vCPU-8RAM-100DISK-C3-Onetemplate-Ubuntu24.04-Delhi. Look it up before the first apply (infra/README.md)."
  type        = string
  validation {
    condition     = length(trimspace(var.plan)) > 0
    error_message = "plan is required (see infra/README.md)."
  }
}

variable "image" {
  description = "E2E image name. deploy/host/bootstrap.sh accepts Ubuntu 26.04 and 24.04; E2E offered 24.04 (Ubuntu-24.04-Distro) when this was written."
  type        = string
  default     = "Ubuntu-24.04-Distro"
  validation {
    condition     = can(regex("^Ubuntu-(24|26)\\.04-", var.image))
    error_message = "image must be Ubuntu 24.04 or 26.04 (what deploy/host/bootstrap.sh accepts)."
  }
}

variable "ssh_public_key" {
  description = "The owner's SSH public key for the first sign-in (ed25519 preferred). bootstrap.sh copies it to the deploy and admin users."
  type        = string
  validation {
    condition     = can(regex("^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+( [^\\n]*)?$", trimspace(var.ssh_public_key)))
    error_message = "ssh_public_key must be one OpenSSH public key line."
  }
}

variable "ssh_cidrs" {
  description = "Who may reach SSH (22/tcp). Owner, 09-11-2026 (Q-E9): open to the internet, key-only and rate-limited by bootstrap.sh. Narrowing it on E2E needs the API's \"manual\" network form (address plus size) confirmed first, so only 0.0.0.0/0 is accepted for now; the host firewall can still narrow SSH."
  type        = list(string)
  default     = ["0.0.0.0/0"]
  validation {
    condition     = length(var.ssh_cidrs) == 1 && var.ssh_cidrs[0] == "0.0.0.0/0"
    error_message = "On E2E, ssh_cidrs is [\"0.0.0.0/0\"] for now (see the description)."
  }
}

variable "disk_encryption" {
  description = "E2E node encryption. Off until E2E confirms it needs no passphrase at boot (an unattended restart must work); cannot be changed after creation (QUESTIONS.md Q-E11)."
  type        = bool
  default     = false
}

variable "buckets" {
  description = "Object store names: CARE's files and pictures (in location) and the backups (in backup_location). The audit fingerprint bucket is not here: it lives in a separate E2E account, with its lock set in the console (infra/README.md)."
  type = object({
    files    = string
    facility = string
    backups  = string
  })
  validation {
    condition     = alltrue([for b in values(var.buckets) : can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", b))]) && length(distinct(values(var.buckets))) == 3
    error_message = "buckets: three different names, 3-63 characters, a-z, 0-9 and -."
  }
}
