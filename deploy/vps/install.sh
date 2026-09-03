#!/usr/bin/env bash
# Strategy Lab VPS Installation and Deployment Script (R-OPS-4)
# Target OS: Ubuntu 22.04 / 24.04 LTS or Debian 12
set -Eeuo pipefail

echo "=== [Strategy Lab] Starting Headless VPS Installation ==="

if [[ "$EUID" -ne 0 ]]; then
  echo "Error: This script must be run as root (or with sudo)." >&2
  exit 1
fi

# 1. Update and install core system dependencies
echo "--> Installing system dependencies..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  curl \
  git \
  jq

# 2. Create service user and group
if ! id -u strategylab >/dev/null 2>&1; then
  echo "--> Creating service user 'strategylab'..."
  useradd --system --no-create-home --user-group --shell /usr/sbin/nologin strategylab
fi

# 3. Create required directories with hardened permissions
echo "--> Creating directories..."
mkdir -p /opt/strategy-lab
mkdir -p /var/lib/strategy-lab
mkdir -p /var/log/strategy-lab
mkdir -p /etc/strategy-lab

chown -R strategylab:strategylab /var/lib/strategy-lab
chown -R strategylab:strategylab /var/log/strategy-lab
chmod 0750 /var/lib/strategy-lab
chmod 0750 /var/log/strategy-lab

# 4. Initialize environment file with 0600 permissions
ENV_FILE="/etc/strategy-lab/env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "--> Generating initial /etc/strategy-lab/env..."
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$SCRIPT_DIR/env.example" ]]; then
    cp "$SCRIPT_DIR/env.example" "$ENV_FILE"
  else
    cat <<'EOF' > "$ENV_FILE"
STRATEGY_LAB_ENV=vps
IQ_EMAIL=""
IQ_PASSWORD=""
SUPABASE_DB_URL=""
SUPABASE_URL=""
SUPABASE_SERVICE_ROLE_KEY=""
STRATEGY_LAB_DATA_DIR="/var/lib/strategy-lab"
STRATEGY_LAB_LOG_DIR="/var/log/strategy-lab"
EOF
  fi
fi

chown strategylab:strategylab "$ENV_FILE"
chmod 0600 "$ENV_FILE"
echo "--> Configured $ENV_FILE with 0600 permissions owned by strategylab:strategylab."

# 5. Set up Python virtual environment
echo "--> Setting up Python 3 virtual environment..."
VENV_DIR="/opt/strategy-lab/venv"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

# 6. Install Strategy Lab package into virtual environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
if [[ -d "$REPO_ROOT/strategy-lab" ]]; then
  echo "--> Installing strategy-lab packages from local checkout..."
  mkdir -p /opt/strategy-lab/current
  cp -r "$REPO_ROOT/strategy-lab" /opt/strategy-lab/current/
  cd /opt/strategy-lab/current/strategy-lab
  "$VENV_DIR/bin/pip" install -e packages/primitives
  "$VENV_DIR/bin/pip" install -e packages/manifest_schema
  "$VENV_DIR/bin/pip" install -e packages/sprt
  "$VENV_DIR/bin/pip" install -e .
  chown -R strategylab:strategylab /opt/strategy-lab
fi

# 7. Install systemd service and timer units
echo "--> Installing systemd services and timers..."
SYSTEMD_SRC="$SCRIPT_DIR"
cp "$SYSTEMD_SRC/strategy-lab.service" /etc/systemd/system/
cp "$SYSTEMD_SRC/strategy-lab-collect.service" /etc/systemd/system/
cp "$SYSTEMD_SRC/strategy-lab-collect.timer" /etc/systemd/system/
cp "$SYSTEMD_SRC/strategy-lab-payout.service" /etc/systemd/system/
cp "$SYSTEMD_SRC/strategy-lab-payout.timer" /etc/systemd/system/
cp "$SYSTEMD_SRC/strategy-lab-backup.service" /etc/systemd/system/
cp "$SYSTEMD_SRC/strategy-lab-backup.timer" /etc/systemd/system/

systemctl daemon-reload

echo "--> Enabling systemd timers..."
systemctl enable --now strategy-lab-collect.timer
systemctl enable --now strategy-lab-payout.timer
systemctl enable --now strategy-lab-backup.timer

echo "=== [Strategy Lab] Installation Complete! ==="
echo "Active timers:"
systemctl list-timers strategy-lab*
echo ""
echo "Next step: edit /etc/strategy-lab/env with production credentials."
