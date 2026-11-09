# Cloud modules (P1-T8b4, ADR-011)

OpenTofu 1.13.9 roots that create the server and its buckets at a provider. Everything on the
server itself (Docker, users, firewall, time, logs) is `deploy/host/bootstrap.sh`, the same on
every provider; the stack is `scripts/deploy.sh`. **Nothing here has been applied**: no account
exists yet, and every test runs against mock providers (`tofu test`, no network calls).

| Root | Creates | Who runs it |
|---|---|---|
| `e2e/` (target, Delhi NCR) | SSH key, firewall group, reserved IP, server, object stores: files + pictures (Delhi), backups (Chennai), each with its regional S3 endpoint in the outputs | the owner, with E2E's API key |
| `aws/` (alternative, Mumbai) | own VPC, firewall group, key, server (x86, encrypted gp3, IMDSv2), Elastic IP, S3: files + pictures (Mumbai), backups (Hyderabad), CARE's own key | the owner, with the server account's profile |
| `aws/anchor/` | the audit fingerprint bucket: Object Lock **COMPLIANCE 10 years**, add-only and read-only keys | the owner, with a **separate** account's profile (ADR-011 §3) |

Both server roots take the same kind of inputs (name, region, size, SSH key, bucket names) and
give the same outputs (`public_ip`, `ssh`, `s3_endpoint`, `buckets`), so switching provider is:
apply the other root, run bootstrap, restore the database, copy the buckets, change `.env` and
`secrets/*.env`, move the two A records at the registrar, `*.app` and `acme-ns` (the record table:
`deploy/staging/README.md`).

**Firewall (both):** in 22 (owner decision Q-E9; on AWS `ssh_cidrs` can narrow it; on E2E only
`0.0.0.0/0` until the API's form for a narrowed rule is confirmed), 80, 443, 53 tcp+udp
(acme-dns); out only 80, 443, 587 (mail), 53, 123/udp (NIC/NPL time). The host's own firewall
(`bootstrap.sh`) allows the same in and limits SSH; it does not limit out, the cloud's does.

**Safety:** the server, its address, its disk and every bucket have `prevent_destroy`; a newer
OS image never rebuilds a running server. State and plans are encrypted (`state_passphrase`,
PBKDF2 + AES-GCM) before they reach the state bucket. No credential is ever in a file here:
E2E's from `SERVICE_API_KEY` / `SERVICE_AUTH_TOKEN`, AWS's from `AWS_PROFILE`; `account_id`
stops a wrong AWS profile. **E2E's provider sends the API key in the request URL**: debug logs
(`TF_LOG`) and some network errors show it. Never paste such output anywhere unredacted; if it
leaks, make a new key in MyAccount and delete the old one.

## First time (owner, when paying)

1. **Accounts:** E2E MyAccount (or AWS), and for fingerprints a second account kept apart (its
   credentials never on the server or with its admins). On AWS, switch on the Hyderabad region
   first (Account > AWS Regions > Asia Pacific (Hyderabad) > Enable; a new account has it off,
   and the plan then fails with an "invalid token" error).
2. **State bucket** in India, by hand, versioning on (E2E object store or S3 in ap-south-1), and
   its access key. Copy `backend.hcl.example` to `backend.hcl` and fill it in.
3. **Settings:** copy `terraform.tfvars.example` to `terraform.tfvars` (git-ignored).
   On E2E, look up the plan string first, since E2E's API names plans per image, e.g.
   `C3-4vCPU-8RAM-100DISK-C3-Onetemplate-Ubuntu24.04-Delhi`: MyAccount > API >
   "List Plans & Pricing for an Image" (Ubuntu-24.04-Distro, Delhi). E2E offered Ubuntu 24.04
   (not 26.04) when this was written; bootstrap accepts both.
4. Run, from the root's folder:
   ```bash
   export TF_VAR_state_passphrase='…'   # from the password manager; lose it and the state is unreadable
   export SERVICE_API_KEY='…' SERVICE_AUTH_TOKEN='…'   # E2E (or: export AWS_PROFILE=…)
   tofu init -backend-config=backend.hcl
   tofu plan -out=plan.tfplan     # read it: it is encrypted on disk
   tofu apply plan.tfplan
   ```
5. Then `deploy/host/README.md` (bootstrap over SSH), `deploy/staging/README.md` (DNS records,
   certificate, mail, deploy).

**What stays manual on E2E** (its provider cannot do it):
- **versioning on each bucket** (console: Object Storage > bucket > Versioning): provider 2.2.12
  ignores it when it creates a bucket. The apply fails on purpose until it is on; then apply again;
- on the backups bucket, a lifecycle rule removing old (noncurrent) versions after 30 days, as
  `infra/aws` sets: pgBackRest's deletes only hide objects in a versioned bucket;
- object store access keys (console), then CORS and the pictures bucket's public-read policy:
  `deploy/s3/setup_buckets.py` works against any S3 endpoint (each region has its own, see the
  `buckets` output);
- the fingerprint bucket: create it in the separate account's console **with Object Lock**
  (E2E offers Governance mode only; ADR-011 §3 accepts it with `IHR_AUDIT_ANCHOR_LOCK_MODE`,
  P1-T8c), 10 years; its add-only and read-only keys.

**Costs** (before 18% GST): E2E about ₹4,050 a month (server ₹3,422 + storage ₹625, ADR-011);
AWS about ₹6,600 + storage with the default x86 `t3.large` (instance ≈ ₹5,500, 100 GB gp3
≈ ₹770, public IPv4 ≈ ₹310; ADR-011's ₹4,300 assumed an ARM t4g, which needs multi-arch
images). Nothing is created until step 4.

## Tests

```bash
cd infra/e2e && tofu init -backend=false && tofu test   # likewise infra/aws, infra/aws/anchor
```

26 tests: firewall exactly as above in both directions, keys-only server on the reserved
address, each E2E bucket's regional endpoint, versioning really on (the check fails a bucket
whose versioning is off), IMDSv2, encrypted disk, no CPU-credit
bills, buckets private except pictures (GetObject only), HTTPS-only bucket policies, CORS for
the hospitals only, CARE's key limited to its two buckets, COMPLIANCE 10 years with an add-only
writer that is explicitly denied every way to remove or unlock, and every input rule
(regions in India, x86 only, Ubuntu 24.04/26.04, key format, retention ≥ 7 years). CI job
"Infra modules" runs them. Mutations (weaker metadata tokens, a public files bucket, an extra
port) each made a test fail.

Provider packages are pinned in `.terraform.lock.hcl` (linux_amd64 hashes; add others with
`tofu providers lock -platform=…`). The AWS provider's checksums are signed by OpenTofu's key,
E2E's by E2E's own (0915 4369 65FE BBA4 428B A77C 3EF4 0241 C2FB 9A1B). The E2E provider's
repository has no licence file: it is a tool we run, never shipped with the product.
