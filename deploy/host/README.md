# Host setup (P1-T8b1)

`bootstrap.sh` turns a fresh **Ubuntu 26.04 LTS** server (24.04 LTS is accepted too, Q-E8) into
an IndieHR host. It is provider-neutral (ADR-011): whatever creates the server
(`infra/<provider>/`), the same script runs on it. It refuses any other OS or release.

It is **idempotent**: a second run changes nothing and prints `No changes.` It is proven on a
throwaway local VM (`scripts/local-vm.sh`, below) before it touches a real server.

## Run it on a new server

```bash
# 1. First contact: check the host key fingerprint against the provider's console, then
ssh <admin>@<address> true            # <admin>: the image's default user (or root)

# 2. Copy and run (as root). The key becomes the deploy user's key.
scp deploy/host/bootstrap.sh <admin>@<address>:/tmp/
scp ~/.ssh/<your-deploy-key>.pub <admin>@<address>:/tmp/ihr-deploy.pub
ssh <admin>@<address> sudo bash /tmp/bootstrap.sh --ssh-key-file /tmp/ihr-deploy.pub

# 3. Before closing this session, from a second terminal: both new logins work.
#    <new-admin>: the admin bootstrap printed at the end (ihradmin if you came in as root;
#    root is now refused)
ssh ihr@<address> true
ssh <new-admin>@<address> sudo true

# 4. Verify (read-only; exits non-zero on any FAIL)
ssh <new-admin>@<address> sudo /usr/local/sbin/ihr-bootstrap --check

# 5. Deploy the stack (from this machine, as the deploy user). deploy.sh refuses a host whose
#    key is not already in known_hosts (step 1), whatever ~/.ssh/config says.
scripts/deploy.sh --host ihr@<address>
```

After step 2 only `ihr` and the admin user can sign in over SSH (`AllowGroups ihr-ssh`). Any
other existing account (for example the image's `ubuntu` when `--admin-user` names someone else)
is refused from then on.

Options: `--ssh-key "<public key>"` (repeatable) or `IHR_SSH_PUBKEY`, `--admin-user NAME` (not
`root` or `ihr`), `--change-admin` (with `--admin-user`: add a different admin later),
`--no-upgrade` (skip `apt-get upgrade`; for every-boot re-runs), `--dry-run` (best effort: shows
the files and steps it would change), `--check`. Keys with options (`command=`, `restrict`,
`from=`…) are ignored: they are never copied and never count as a working login.

**The admin user is chosen once.** The first run picks it (`--admin-user`, else the user who ran
`sudo`, else `ubuntu`, else a new `ihradmin`) and saves it in `/etc/indiehr/admin-user`; every
later run, including the every-boot one (which has no `sudo` user), keeps it. A different
`--admin-user` is refused unless `--change-admin` is given; then the new admin is added with its
own `/etc/sudoers.d/90-indiehr-admin-<name>` and the old one keeps sudo (remove its file by hand
if it should not).

**Every-boot start scripts.** Some providers run a short start script on every boot (often with a
small size limit). The script installs itself as `/usr/local/sbin/ihr-bootstrap`, so after the
first run over SSH the start script can be one line:
`[ -x /usr/local/sbin/ihr-bootstrap ] && /usr/local/sbin/ihr-bootstrap --no-upgrade`.
A re-run takes seconds, changes nothing on a healthy host, and repairs drift. It takes a lock, so
a start-script run and an SSH run never overlap. At boot it only *starts* Docker and chrony if
they are not up yet (a no-op while they are still starting); it restarts them only for a changed
configuration.
Check what the provider really does: one provider's own tooling runs its start script only when
a server is created, before this script exists (`infra/README.md`); there, re-run it over SSH.

## What it does, and why

| Control | Setting | Why |
|---|---|---|
| Packages | `apt-get upgrade`; base packages | Start from a patched system |
| Security updates | `unattended-upgrades`, Ubuntu's security origins only, **no automatic reboot**; `--check` reports a pending reboot | Patches arrive daily; a hospital system restarts only when planned |
| Docker | Docker's own apt repository, key checked by fingerprint `9DC8 5822 … 0EBF CD88`, **exact versions pinned and held** (docs/VERSIONS.md) | No surprise engine upgrades; one known version everywhere |
| Docker daemon | `log-driver journald`, `live-restore`, `userland-proxy false`, `no-new-privileges`, iptables on | Container logs reach the 180-day journal; restarts of the daemon keep containers up; no proxy processes |
| Firewall | ufw (`IPV6=yes`, so every rule covers IPv6 too): deny incoming, allow outgoing, **`limit 22/tcp`** (every port sshd listens on), allow 80 and 443, allow 53 (udp+tcp, acme-dns: the wildcard certificate's DNS check, P1-T8b2), allow out 123/udp. A plain `allow` for the SSH port (`ufw allow OpenSSH`, `allow 22/tcp`) is removed, because it would match first and skip the rate limit; `--check` lists any other rule it does not manage | Only SSH (rate-limited: 6 new connections per 30 s per address), the web and the certificate's DNS reach the host |
| Docker ports | `DOCKER-USER` chain (in ufw's `after.rules`/`after6.rules`): from outside, only connections whose original port was 80 or 443 (Traefik) or 53 udp/tcp (acme-dns) reach a container; everything else is dropped and logged (`[IHR DOCKER-USER BLOCK]`) | Ports Docker publishes are DNAT-ed and forwarded, so ufw's own rules never see them. ufw loads this chain at boot, before Docker, so there is no open window |
| SSH | `/etc/ssh/sshd_config.d/10-indiehr.conf`: keys only, no root, `MaxAuthTries 3`, `LoginGraceTime 30`, no X11/agent forwarding, local forwarding only, `AllowGroups ihr-ssh` | Q-E9: key-only, rate-limited, open to the internet |
| fail2ban | `sshd` jail on the ports sshd really uses, journal backend, 5 failures in 10 min = 1 h ban (growing) | On top of ufw's rate limit |
| Users | `ihr`: deploys (groups `docker`, `ihr-ssh`; no sudo, no password). Admin: chosen on the first run and saved in `/etc/indiehr/admin-user` (above); groups `sudo`, `ihr-ssh`, passwordless sudo via `/etc/sudoers.d/90-indiehr-admin-<name>` | Deploys never need sudo; the admin is the way back in (below) |
| Time | chrony with **only** `samay1.nic.in`, `samay2.nic.in` (NIC) and `time.nplindia.org` (NPL), `iburst`; systemd-timesyncd removed; Ubuntu's NTS pool removed; time zone `Asia/Kolkata` | CERT-In Directions (28-04-2022) (i): sync to NIC or NPL. None of them offers NTS (checked 09-11-2026), so these sources are unauthenticated; that is the accepted trade-off |
| Host DNS (P1-T8b2) | systemd-resolved keeps running but without its local stub (`DNSStubListener=no`); `/etc/resolv.conf` points at `/run/systemd/resolve/resolv.conf` (the provider's resolvers) | The stub held port 53 on 127.0.0.53/54, so acme-dns could not publish it. `--check`: no other listener on 53, names still resolve |
| Logs | journald `Storage=persistent`, `MaxRetentionSec=1year`, `SystemMaxUse=20G`, `SystemKeepFree=15G`, compressed | CERT-In (iv): 180 days rolling, in India; DPDP Rules 2025: one year of processing logs |
| Swap | 2 GB `/swapfile` if the server has no swap | Headroom on an 8 GB server |
| Kernel | `/etc/sysctl.d/60-indiehr.conf` (keys this kernel lacks, e.g. IPv6 ones under `ipv6.disable=1`, are skipped): no ICMP redirects or source routing, SYN cookies, `kptr_restrict`, `dmesg_restrict`, no unprivileged BPF, `swappiness 10` | Common hardening that leaves Docker's forwarding alone |
| Directories | `/srv/indiehr` owned by `ihr`, mode 750 | Where `scripts/deploy.sh` puts the stack |
| Host metrics (P1-T9a) | `/var/lib/indiehr/textfile` owned by `ihr` (755); `ihr-host-metrics.timer` every minute runs `ihr-host-metrics.service` as `ihr` (extra group `systemd-journal`, `NoNewPrivileges`): `scripts/staging.sh monitoring-textfile` writes container states, journal size, chrony's source, backup markers and client-key ages for node_exporter. Skipped until the first deploy; does nothing until `.env` has `IHR_MONITORING`. `--check`: units as managed, timer active, the file fresh once monitoring is on | No container gets the Docker socket (it would control the whole server); the host itself reads `docker inspect` and the cgroup files |

**Chrony without editing Ubuntu's file.** Our sources live in `/etc/chrony/indiehr.conf`; a
systemd drop-in starts chronyd with `-f /etc/chrony/indiehr.conf`. Ubuntu's own `chrony.conf`
(its pools, DHCP-supplied servers) is not read, and package updates never ask about it.

**Disk space decides how long logs last, not the time limit.** journald deletes the oldest files
once the journal reaches its cap, even if they are younger than a year. The cap is the smaller of
`SystemMaxUse` (20 GB) and *used + free space − `SystemKeepFree` (15 GB)*: the journal shares the
disk with Docker (database, images), so as the disk fills the cap shrinks and logs go early.
20 GB compressed covers 180 days while the journal grows by about 110 MB a day as stored.
`scripts/deploy.sh` removes the images each deploy replaces (`docker image prune`). `--check`
**fails** when entries younger than 180 days are already gone (the oldest entry is younger than
both 180 days and the host's first run, saved in `/etc/indiehr/installed-at`), and warns when
free space already holds the cap below 20 GB. The warning sign is the cap being reached while
the oldest entry is younger than 180 days: then free disk space or grow the disk. A copy only on
the server disappears with the server: a daily export to an Indian bucket comes with the backups
(P1-T8c).

**Not covered here:** IPv6 to containers (the stack's networks are IPv4 only, and without the
userland proxy IPv6 does not reach them); journald sealing (`Seal=yes` needs a key kept
off-server); a private SSH tunnel (may come with P1-T8d).

## Never locked out

- The script stops before touching SSH unless **both** the deploy user and the admin user have
  at least one valid public key and are in `ihr-ssh`. Keys are only added, never removed.
- Keys with options (cloud-init puts a forced "Please login as ubuntu" command on root's key)
  are never copied and never count: such a key opens no shell.
- The new SSH file is checked with `sshd -t`, then with `sshd -T` for **both** users (from a
  test address and, when known, the operator's own): every managed setting must be in effect
  after any `Match` block, and no `AllowUsers`, `DenyUsers` or `DenyGroups` may be set elsewhere
  (our file cannot undo them). If anything fails, the previous file is restored and the run
  stops. Only then is sshd reloaded. Open sessions stay open. `--check` runs the same checks.
- The ufw SSH rules (`limit`, on every port sshd really uses) are added before ufw is switched
  on, and with `IPV6=yes`, so an IPv6 session is not cut. A plain allow for SSH is removed only
  after the limit rule is in place.
- The admin user is saved on the first run and never silently replaced (above).
- After the first run, sign in as `ihr` (deploys) or as the admin user. `root` and passwords no
  longer work, even if root still has keys.

**Break-glass:** the admin user (sudo) is the way back if `ihr`'s key is lost: sign in as the
admin and add a key to `/home/ihr/.ssh/authorized_keys`. If both keys are lost, use the
provider's serial/web console (no password is set, so that means the provider's recovery or
rescue mode) or rebuild the server and restore from backups (P1-T8c). A banned address clears
itself (1 h, growing), or: `sudo fail2ban-client unban <address>`.

## Prove it locally (no sudo, nothing paid)

`scripts/local-vm.sh` runs the official Ubuntu 26.04 cloud image (pinned serial, signature and
SHA-256 checked) under KVM with its own SSH key, all under `~/.cache/indiehr/vm/`:

```bash
scripts/deploy.sh --local-proof --build-only     # images first (RAM: before the VM starts)
scripts/local-vm.sh fetch && scripts/local-vm.sh up
scripts/local-vm.sh bootstrap    # run 1, run 2 ("No changes."), --check
scripts/local-vm.sh reboot       # --check again after a reboot
scripts/deploy.sh --local-proof --no-build --allow-dirty   # stack + smoke checks inside the VM
scripts/local-vm.sh verify       # SSH rules, ufw + DOCKER-USER probes (with mutations), HTTPS from outside
scripts/local-vm.sh destroy
```

Inside the test VM the `ubuntu` user has passwordless sudo from cloud-init; that is expected.
