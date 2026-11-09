# One server for the staging stack (ADR-003 / ADR-011) with what the rest of the kit expects:
# a fixed public address, the firewall the host also enforces (deploy/host/bootstrap.sh), and
# Indian object stores. Everything else (Docker, users, SSH hardening, time, logs) is done by
# bootstrap.sh over SSH, the same on every provider.

locals {
  # Inbound from the internet: SSH (key-only, rate-limited by the host), the web, and acme-dns
  # for the wildcard certificate's DNS check (P1-T8b2). Nothing else.
  inbound = concat(
    [{ protocol = "Custom_TCP", port = "22", cidr = "0.0.0.0/0", description = "SSH (key-only, rate-limited)" }],
    [
      { protocol = "Custom_TCP", port = "80", cidr = "0.0.0.0/0", description = "HTTP (redirects to HTTPS)" },
      { protocol = "Custom_TCP", port = "443", cidr = "0.0.0.0/0", description = "HTTPS" },
      { protocol = "Custom_TCP", port = "53", cidr = "0.0.0.0/0", description = "acme-dns (certificate check)" },
      { protocol = "Custom_UDP", port = "53", cidr = "0.0.0.0/0", description = "acme-dns (certificate check)" },
    ],
  )

  # Outbound: only what the server uses (the same list as infra/aws)
  outbound = [
    { protocol = "Custom_TCP", port = "80", description = "Ubuntu updates" },
    { protocol = "Custom_TCP", port = "443", description = "Docker images, Let's Encrypt, S3" },
    { protocol = "Custom_TCP", port = "587", description = "mail (Zoho ZeptoMail, STARTTLS)" },
    { protocol = "Custom_TCP", port = "53", description = "DNS (incl. the registrar's servers)" },
    { protocol = "Custom_UDP", port = "53", description = "DNS" },
    { protocol = "Custom_UDP", port = "123", description = "time from NIC/NPL (CERT-In)" },
  ]
}

resource "e2e_ssh_key" "owner" {
  label      = "${var.name}-owner"
  ssh_key    = trimspace(var.ssh_public_key)
  project_id = var.project_id
  location   = var.location
}

resource "e2e_security_groups" "server" {
  name        = "${var.name}-server"
  description = "IndieHR ${var.name}: SSH, HTTP, HTTPS, DNS in; updates, images, DNS, time, mail out"
  location    = var.location
  project_id  = var.project_id

  dynamic "rules" {
    for_each = local.inbound
    content {
      rule_type     = "Inbound"
      protocol_name = rules.value.protocol
      port_range    = rules.value.port
      network       = rules.value.cidr == "0.0.0.0/0" ? "any" : "manual"
      network_cidr  = rules.value.cidr == "0.0.0.0/0" ? "--" : rules.value.cidr
      description   = rules.value.description
    }
  }
  dynamic "rules" {
    for_each = local.outbound
    content {
      rule_type     = "Outbound"
      protocol_name = rules.value.protocol
      port_range    = rules.value.port
      network       = "any"
      description   = rules.value.description
    }
  }
}

# The address the registrar's records point at (*.app, acme-ns): kept if the server is rebuilt
resource "e2e_reserved_ip" "server" {
  location   = var.location
  project_id = var.project_id

  lifecycle {
    prevent_destroy = true
  }
}

resource "e2e_node" "server" {
  name               = var.name
  label              = var.name
  plan               = var.plan
  image              = var.image
  location           = var.location
  project_id         = var.project_id
  ssh_keys           = [e2e_ssh_key.owner.label]
  security_group_ids = [tonumber(e2e_security_groups.server.id)]
  reserve_ip         = e2e_reserved_ip.server.ip_address
  # Keys only from the first boot; bootstrap.sh then switches passwords off in sshd too
  disable_password      = true
  enable_bitninja       = false
  is_encryption_enabled = var.disk_encryption
  # Backups are ours (pgBackRest to the backup bucket, P1-T8c), not E2E's node snapshots.
  # No start script: E2E's provider runs it only when the node is created, before bootstrap.sh
  # exists, and the host settings persist across reboots anyway.
  backup = false

  lifecycle {
    # The server holds the database: never replaced or removed by an apply
    prevent_destroy = true
    # A newer image must not rebuild a running server
    ignore_changes = [image]
  }
}

resource "e2e_objectstore" "files" {
  name                = var.buckets.files
  region              = var.location
  project_id          = tonumber(var.project_id)
  enabling_versioning = true

  lifecycle {
    prevent_destroy = true
    # Provider 2.2.12 ignores enabling_versioning when it creates a bucket (and then reports it
    # as on): fail loudly until versioning is really on (infra/README.md, manual steps)
    postcondition {
      condition     = self.versioning_status == "Enabled"
      error_message = "Versioning is not on for this bucket: switch it on in E2E's console (Object Storage > bucket > Versioning), then apply again."
    }
  }
}

resource "e2e_objectstore" "facility" {
  name                = var.buckets.facility
  region              = var.location
  project_id          = tonumber(var.project_id)
  enabling_versioning = true

  lifecycle {
    prevent_destroy = true
    # Provider 2.2.12 ignores enabling_versioning when it creates a bucket (and then reports it
    # as on): fail loudly until versioning is really on (infra/README.md, manual steps)
    postcondition {
      condition     = self.versioning_status == "Enabled"
      error_message = "Versioning is not on for this bucket: switch it on in E2E's console (Object Storage > bucket > Versioning), then apply again."
    }
  }
}

resource "e2e_objectstore" "backups" {
  name                = var.buckets.backups
  region              = var.backup_location
  project_id          = tonumber(var.project_id)
  enabling_versioning = true

  lifecycle {
    prevent_destroy = true
    # Provider 2.2.12 ignores enabling_versioning when it creates a bucket (and then reports it
    # as on): fail loudly until versioning is really on (infra/README.md, manual steps)
    postcondition {
      condition     = self.versioning_status == "Enabled"
      error_message = "Versioning is not on for this bucket: switch it on in E2E's console (Object Storage > bucket > Versioning), then apply again."
    }
  }
}
