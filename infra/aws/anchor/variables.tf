variable "state_passphrase" {
  description = "Encrypts the state (it holds the two keys). TF_VAR_state_passphrase; owner's password manager."
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.state_passphrase) >= 16
    error_message = "state_passphrase must have at least 16 characters."
  }
}

variable "account_id" {
  description = "The separate fingerprint account (12 digits), not the server's."
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "account_id is the 12-digit AWS account number."
  }
}

variable "bucket" {
  description = "Fingerprint bucket name (globally unique), e.g. indiehr-audit-anchors-<account>."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.bucket))
    error_message = "bucket: 3-63 characters, a-z, 0-9 and -."
  }
}

variable "retention_years" {
  description = "How long nobody can delete or change a fingerprint (ADR-010: 10 years; the verifier wants 7 or more)."
  type        = number
  default     = 10
  validation {
    condition     = var.retention_years >= 7 && var.retention_years <= 100
    error_message = "retention_years between 7 (the verifier's minimum) and 100."
  }
}
