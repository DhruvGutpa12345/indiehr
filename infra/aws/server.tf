# One server for the staging stack (ADR-003 / ADR-011). Docker, users, SSH hardening, time and
# logs come from deploy/host/bootstrap.sh over SSH, the same on every provider.

# Canonical's own pointer to the current Ubuntu image (owner 099720109477 publishes it)
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/${var.ubuntu_release}/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

locals {
  # Inbound from the internet: SSH (key-only, rate-limited by the host), the web, and acme-dns
  # for the wildcard certificate's DNS check (P1-T8b2). Nothing else.
  inbound = merge(
    { for i, cidr in var.ssh_cidrs : "ssh-${i}" => { proto = "tcp", port = 22, cidr = cidr, what = "SSH (key-only, rate-limited)" } },
    {
      http    = { proto = "tcp", port = 80, cidr = "0.0.0.0/0", what = "HTTP (redirects to HTTPS)" }
      https   = { proto = "tcp", port = 443, cidr = "0.0.0.0/0", what = "HTTPS" }
      dns-tcp = { proto = "tcp", port = 53, cidr = "0.0.0.0/0", what = "acme-dns (certificate check)" }
      dns-udp = { proto = "udp", port = 53, cidr = "0.0.0.0/0", what = "acme-dns (certificate check)" }
    },
  )
  burstable = startswith(var.instance_type, "t")

  # Outbound: only what the server uses (the same list as infra/e2e)
  outbound = {
    http    = { proto = "tcp", port = 80, what = "Ubuntu updates" }
    https   = { proto = "tcp", port = 443, what = "Docker images, Let's Encrypt, S3, Keycloak themes" }
    smtp    = { proto = "tcp", port = 587, what = "mail (Zoho ZeptoMail, STARTTLS)" }
    dns-tcp = { proto = "tcp", port = 53, what = "DNS (incl. Traefik's check at the registrar's servers)" }
    dns-udp = { proto = "udp", port = 53, what = "DNS" }
    ntp     = { proto = "udp", port = 123, what = "time from NIC/NPL (CERT-In)" }
  }
}

resource "aws_key_pair" "owner" {
  key_name   = "${var.name}-owner"
  public_key = trimspace(var.ssh_public_key)
}

resource "aws_security_group" "server" {
  name        = "${var.name}-server"
  description = "IndieHR ${var.name}: SSH, HTTP, HTTPS, DNS in; updates, images, DNS, time, mail out"
  vpc_id      = aws_vpc.main.id
  tags        = { Name = "${var.name}-server" }
}

resource "aws_vpc_security_group_ingress_rule" "server" {
  for_each          = local.inbound
  security_group_id = aws_security_group.server.id
  ip_protocol       = each.value.proto
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = each.value.cidr
  description       = each.value.what
}

resource "aws_vpc_security_group_egress_rule" "server" {
  for_each          = local.outbound
  security_group_id = aws_security_group.server.id
  ip_protocol       = each.value.proto
  from_port         = each.value.port
  to_port           = each.value.port
  cidr_ipv4         = "0.0.0.0/0"
  description       = each.value.what
}

resource "aws_instance" "server" {
  ami                         = data.aws_ssm_parameter.ubuntu.insecure_value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.public.id
  vpc_security_group_ids      = [aws_security_group.server.id]
  key_name                    = aws_key_pair.owner.key_name
  associate_public_ip_address = false
  ebs_optimized               = true
  disable_api_termination     = true
  monitoring                  = false

  # Instance metadata: tokens required, and one hop only, so containers cannot reach it
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.disk_gb
    encrypted             = true
    delete_on_termination = false # the database lives here
  }

  # No surprise bills: a burstable instance never buys CPU credits
  dynamic "credit_specification" {
    for_each = local.burstable ? [1] : []
    content {
      cpu_credits = "standard"
    }
  }

  tags = { Name = var.name }

  lifecycle {
    prevent_destroy = true
    # A newer Ubuntu image must not rebuild a running server
    ignore_changes = [ami]
  }
}

# The address the registrar's records point at (*.app, acme-ns): kept if the server is rebuilt
resource "aws_eip" "server" {
  domain   = "vpc"
  instance = aws_instance.server.id
  tags     = { Name = var.name }
  # An Elastic IP can only be associated once the VPC has its internet gateway
  depends_on = [aws_internet_gateway.main]

  lifecycle {
    prevent_destroy = true
  }
}
