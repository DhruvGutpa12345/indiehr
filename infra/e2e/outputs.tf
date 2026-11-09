# The same outputs as infra/aws (ADR-011): what deploy.sh, the registrar and secrets/*.env need.

output "public_ip" {
  description = "The server's fixed public IPv4 address: the registrar's A records (*.app, acme-ns) point here."
  value       = e2e_reserved_ip.server.ip_address
}

output "ssh" {
  description = "First sign-in, then deploy/host/README.md (bootstrap) and scripts/deploy.sh."
  value       = "ssh root@${e2e_reserved_ip.server.ip_address}"
}

locals {
  # E2E object storage is regional: each region has its own S3 endpoint
  s3_endpoints = {
    Delhi   = "https://objectstore.e2enetworks.net"
    Chennai = "https://in-south1-objectstore.e2enetworks.net"
  }
}

output "s3_endpoint" {
  description = "S3 endpoint for CARE's two buckets (BUCKET_ENDPOINT / BUCKET_EXTERNAL_ENDPOINT). Access keys are made in E2E's console."
  value       = local.s3_endpoints[var.location]
}

output "buckets" {
  description = "Bucket names, regions and each one's S3 endpoint (the backups bucket's is pgBackRest's repo-s3-endpoint)."
  value = {
    files    = { name = e2e_objectstore.files.name, region = var.location, endpoint = local.s3_endpoints[var.location] }
    facility = { name = e2e_objectstore.facility.name, region = var.location, endpoint = local.s3_endpoints[var.location] }
    backups  = { name = e2e_objectstore.backups.name, region = var.backup_location, endpoint = local.s3_endpoints[var.backup_location] }
  }
}

output "monthly_price" {
  description = "E2E's price for the server as it reports it (before GST)."
  value       = e2e_node.server.price
}
