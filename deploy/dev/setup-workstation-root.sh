#!/usr/bin/env bash
# P0-T2: system-level dev tools for IndieHR on Ubuntu 26.04 (resolute). Run once with sudo.
# Installs Docker Engine + Compose from Docker's official apt repo, and GitHub CLI from GitHub's apt repo.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo bash $0" >&2
  exit 1
fi

CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
install -m 0755 -d /etc/apt/keyrings

# Docker official repository
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${CODENAME}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

# GitHub CLI official repository
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod a+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
cat > /etc/apt/sources.list.d/github-cli.sources <<EOF
Types: deb
URIs: https://cli.github.com/packages
Suites: stable
Components: main
Signed-By: /etc/apt/keyrings/githubcli-archive-keyring.gpg
EOF

# Ubuntu Pro (ESM) ships an old gh at priority 510; prefer GitHub's own package.
cat > /etc/apt/preferences.d/github-cli.pref <<EOF
Package: gh
Pin: origin cli.github.com
Pin-Priority: 990
EOF

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin gh
systemctl enable --now docker

echo
docker version --format 'Docker Engine {{.Server.Version}}'
docker compose version
gh --version | head -1
echo "Done. Tell Claude: 'setup script finished'."
