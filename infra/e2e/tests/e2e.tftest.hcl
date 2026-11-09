# Unit tests with a mock E2E provider: no account, no API call (tofu test). P1-T8b4.

mock_provider "e2e" {
  mock_resource "e2e_objectstore" {
    defaults = { versioning_status = "Enabled" }
  }
  mock_resource "e2e_reserved_ip" {
    defaults = { ip_address = "203.0.113.10" }
  }
  mock_resource "e2e_security_groups" {
    defaults = { id = "4242" }
  }
}

variables {
  state_passphrase = "a test passphrase, not real"
  name             = "indiehr-test"
  project_id       = "12345"
  plan             = "C3-4vCPU-8RAM-100DISK-C3-Onetemplate-Ubuntu24.04-Delhi"
  ssh_public_key   = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGtestkeytestkeytestkeytestkeytestkeytest owner@test"
  buckets = {
    files    = "indiehr-test-files"
    facility = "indiehr-test-facility"
    backups  = "indiehr-test-backups"
  }
}

run "firewall_lets_in_only_ssh_web_and_dns" {
  command = plan

  assert {
    condition     = toset([for r in e2e_security_groups.server.rules : "${r.protocol_name}/${r.port_range}" if r.rule_type == "Inbound"]) == toset(["Custom_TCP/22", "Custom_TCP/80", "Custom_TCP/443", "Custom_TCP/53", "Custom_UDP/53"])
    error_message = "inbound rules must be exactly 22, 80, 443 and 53 (tcp + udp)"
  }
  assert {
    condition     = length([for r in e2e_security_groups.server.rules : r if r.rule_type == "Inbound"]) == 5
    error_message = "one inbound rule per port"
  }
  assert {
    condition     = toset([for r in e2e_security_groups.server.rules : "${r.protocol_name}/${r.port_range}" if r.rule_type == "Outbound"]) == toset(["Custom_TCP/80", "Custom_TCP/443", "Custom_TCP/587", "Custom_TCP/53", "Custom_UDP/53", "Custom_UDP/123"])
    error_message = "outbound: only updates, images, mail, DNS and time"
  }
  assert {
    condition     = length([for r in e2e_security_groups.server.rules : r if r.protocol_name == "All"]) == 0
    error_message = "no all-protocol rule in either direction"
  }
}

run "server_is_keys_only_with_the_fixed_address" {
  command = plan

  assert {
    condition     = e2e_node.server.disable_password == true
    error_message = "the server must start without password sign-in"
  }
  assert {
    condition     = length(e2e_node.server.ssh_keys) == 1 && e2e_node.server.ssh_keys[0] == e2e_ssh_key.owner.label
    error_message = "the owner's key is the only key"
  }
  assert {
    condition     = e2e_node.server.reserve_ip == "203.0.113.10" && output.public_ip == "203.0.113.10"
    error_message = "the server must use the reserved address"
  }
  assert {
    condition     = length(e2e_node.server.security_group_ids) == 1 && e2e_node.server.security_group_ids[0] == 4242
    error_message = "the server must use our firewall group"
  }
  assert {
    condition     = e2e_node.server.location == "Delhi" && e2e_node.server.backup == false
    error_message = "Delhi by default; backups are ours (pgBackRest), not E2E snapshots"
  }
}

run "no_start_script" {
  command = plan

  assert {
    condition     = e2e_node.server.start_script == null
    error_message = "no start script (E2E runs it only at creation, before bootstrap.sh exists)"
  }
}

run "buckets_in_india_with_versioning" {
  command = plan

  assert {
    condition     = alltrue([e2e_objectstore.files.enabling_versioning, e2e_objectstore.facility.enabling_versioning, e2e_objectstore.backups.enabling_versioning])
    error_message = "every bucket keeps versions"
  }
  assert {
    condition     = e2e_objectstore.files.region == "Delhi" && e2e_objectstore.backups.region == "Chennai"
    error_message = "files with the server (Delhi), backups in the other region (Chennai)"
  }
  assert {
    condition     = output.buckets.backups.endpoint == "https://in-south1-objectstore.e2enetworks.net" && output.s3_endpoint == "https://objectstore.e2enetworks.net"
    error_message = "each bucket's own regional endpoint (Chennai differs from Delhi)"
  }
}

run "ssh_narrowing_waits_for_the_api_format" {
  command = plan
  variables {
    ssh_cidrs = ["198.51.100.7/32"]
  }
  expect_failures = [var.ssh_cidrs]
}

run "versioning_must_really_be_on" {
  command = plan
  override_resource {
    target = e2e_objectstore.backups
    values = { versioning_status = "Disabled" }
  }
  expect_failures = [e2e_objectstore.backups]
}

run "rejects_a_region_outside_india" {
  command = plan
  variables {
    location = "Mumbai"
  }
  expect_failures = [var.location]
}

run "rejects_an_unsupported_os" {
  command = plan
  variables {
    image = "Ubuntu-22.04-Distro"
  }
  expect_failures = [var.image]
}

run "rejects_a_bad_ssh_key" {
  command = plan
  variables {
    ssh_public_key = "not a key"
  }
  expect_failures = [var.ssh_public_key]
}

run "rejects_duplicate_bucket_names" {
  command = plan
  variables {
    buckets = { files = "same-name", facility = "same-name", backups = "other-name" }
  }
  expect_failures = [var.buckets]
}

run "rejects_a_short_state_passphrase" {
  command = plan
  variables {
    state_passphrase = "short"
  }
  expect_failures = [var.state_passphrase]
}
