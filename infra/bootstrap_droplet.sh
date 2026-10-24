#!/usr/bin/env bash
# Guardian droplet bootstrap (MEDGEMMA_DO_SETUP.md section 3). Idempotent: safe to re-run.
#
# Run as root ON the DigitalOcean GPU droplet:
#   bash /opt/guardian/infra/bootstrap_droplet.sh 203-0-113-10.sslip.io [/opt/guardian]
#   PUBLIC_HOST=203-0-113-10.sslip.io REPO_DIR=/opt/guardian bash infra/bootstrap_droplet.sh
#
# What it configures (and nothing else):
#   1. checks that an NVIDIA GPU is visible (nvidia-smi)
#   2. installs Ollama with the official script; re-runs it to upgrade if older than
#      OLLAMA_MIN_VERSION
#   3. ollama.service override: 127.0.0.1:11434 ONLY, keep-alive 24h, 2 loaded models,
#      2 parallel requests, 16K context
#   4. ufw: deny incoming, allow 22,80,443/tcp; removes old allow rules for 11434/8000
#   5. Caddy from its official apt repo: https://PUBLIC_HOST -> 127.0.0.1:8000
#      (the only public door)
#   6. Python venv + guardian-api.service (uvicorn on 127.0.0.1:8000 as a non-root user,
#      EnvironmentFile=$REPO_DIR/.env)
#   7. prints a summary and the verification commands
#
# Security invariants (MEDGEMMA_DO_SETUP.md sections 0 and 11): Ollama has no auth, so it is
# never bound to 0.0.0.0, OLLAMA_ORIGINS is never "*", and 11434/8000 are never opened in
# the firewall. Teammates reach Ollama through an SSH tunnel only.
#
# Model pulls / warm-up are a separate step: infra/pull_models.sh.
if grep -qU $'\r' "$0" 2>/dev/null; then printf 'ERROR: %s has Windows (CRLF) line endings. Fix: sed -i "s/\\r$//" %s\n' "$0" "$0" >&2; exit 1; fi #

set -euo pipefail
export LC_ALL=C
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_SUSPEND=1 NEEDRESTART_MODE=l

# Minimum Ollama version for Gemma 4. Reported as 0.22 or newer in MEDGEMMA_DO_SETUP.md;
# CONFIRM it in Ollama's release notes (https://github.com/ollama/ollama/releases) and
# override with OLLAMA_MIN_VERSION=x.y.z if it differs.
OLLAMA_MIN_VERSION="${OLLAMA_MIN_VERSION:-0.22.0}"
SERVICE_USER="${SERVICE_USER:-guardian}"  # dedicated non-root user for the API
PUBLIC_HOST="${PUBLIC_HOST:-}"
REPO_DIR="${REPO_DIR:-}"

API_PORT=8000
OLLAMA_PORT=11434
OLLAMA_OVERRIDE=/etc/systemd/system/ollama.service.d/override.conf
API_UNIT=/etc/systemd/system/guardian-api.service
CADDYFILE=/etc/caddy/Caddyfile
STATE_DIR=/var/lib/guardian

WARNINGS=()
REMOVED_RULES=()
GPU_NAME="unknown"
OLLAMA_VERSION=""
REPO_PRESENT=0
ENV_OK=0
TABLES_OK=0
API_STATE="not started"
HTTPS_STATE="not checked"

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { WARNINGS+=("$*"); printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: bash $0 [--public-host] <PUBLIC_HOST> [[--repo-dir] <REPO_DIR>]

  PUBLIC_HOST  host name Caddy serves with a real TLS certificate (required; may also come
               from the PUBLIC_HOST env var or from PUBLIC_HOST in REPO_DIR/.env).
               No domain? Use <ip-with-dashes>.sslip.io, e.g. 203-0-113-10.sslip.io
  REPO_DIR     where the repo lives on the droplet (default /opt/guardian); the API reads
               its configuration from REPO_DIR/.env (chmod 600)

Environment overrides: OLLAMA_MIN_VERSION (default 0.22.0), SERVICE_USER (default guardian),
PYTHON_BIN (a specific python3 >= 3.11 for the venv).

Before running: GPU Droplet (AI/ML-ready or inference-optimized image) and a DigitalOcean
Cloud Firewall allowing inbound 22, 80, 443 only (never 11434 / 8000).
EOF
}

# --------------------------------------------------------------------------- helpers

# version_ge A B: true if version A >= version B.
version_ge() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# Installed Ollama version. Prefers the client (binary) version when both are printed
# ("ollama version is X" / "Warning: client version is Y").
ollama_version() {
  local out v
  out="$(ollama --version 2>&1 || true)"
  v="$(printf '%s\n' "$out" | sed -n 's/.*client version is \([0-9][0-9.]*\).*/\1/p' | head -n1)"
  if [[ -z "$v" ]]; then
    v="$(printf '%s\n' "$out" | grep -Eo '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1 || true)"
  fi
  printf '%s' "$v"
}

# env_get KEY FILE: value of KEY in a dotenv/systemd env file (last assignment wins,
# surrounding quotes stripped). Never echo the result for secret keys.
env_get() {
  local key="$1" file="$2" line val
  [[ -r "$file" ]] || return 0
  line="$(grep -E "^${key}=" "$file" | tail -n1 || true)"
  [[ -n "$line" ]] || return 0
  val="${line#*=}"
  val="${val%$'\r'}"
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  if [[ ${#val} -ge 2 && ( "$val" == \"*\" || "$val" == \'*\' ) ]]; then
    val="${val:1:${#val}-2}"
  fi
  printf '%s' "$val"
}

# wait_for_http URL SECONDS: poll until the URL answers 2xx.
wait_for_http() {
  local url="$1" secs="$2" i
  for ((i = 0; i < secs; i++)); do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# assert_loopback_only PORT: die if anything listens on PORT on a non-loopback address.
assert_loopback_only() {
  local port="$1" addrs bad
  addrs="$(ss -Hltn "sport = :${port}" 2>/dev/null | awk '{print $4}' || true)"
  if [[ -z "$addrs" ]]; then
    warn "nothing is listening on port ${port}"
    return 0
  fi
  bad="$(printf '%s\n' "$addrs" | grep -Ev "^(127\.0\.0\.1|\[::1\]):${port}\$" || true)"
  if [[ -n "$bad" ]]; then
    die "port ${port} is listening on a non-loopback address: $(printf '%s' "$bad" | tr '\n' ' ')
       Stop whatever binds it (e.g. a manual 'OLLAMA_HOST=0.0.0.0 ollama serve' or
       'uvicorn --host 0.0.0.0' from the old README) and re-run this script."
  fi
  info "port ${port}: loopback only ($(printf '%s' "$addrs" | tr '\n' ' '))"
}

# port_in_spec PORT SPEC: SPEC is a ufw port list such as "8000", "22,80,443", "8000:8010".
port_in_spec() {
  local port="$1" part lo hi
  local -a parts
  IFS=',' read -ra parts <<< "$2"
  for part in "${parts[@]}"; do
    if [[ "$part" == *:* ]]; then
      lo="${part%%:*}"
      hi="${part##*:}"
      if [[ -n "$lo" && -n "$hi" ]] && ((port >= lo && port <= hi)); then
        return 0
      fi
    elif [[ "$part" == "$port" ]]; then
      return 0
    fi
  done
  return 1
}

# remove_ufw_rules PORT...: delete every allow/limit rule whose port covers one of PORTs
# (e.g. "ufw allow 11434" or "ufw allow 8000/tcp" from the old README advice).
# Uses `ufw show added`, which also works while ufw is still inactive.
remove_ufw_rules() {
  local line rule tok spec port covers
  local rule_re='^ufw (allow|limit) '
  local -a toks
  while IFS= read -r line; do
    [[ "$line" =~ $rule_re ]] || continue
    rule="${line#ufw }"
    rule="${rule%% comment *}"  # ufw matches rules without their comment
    read -ra toks <<< "$rule"
    covers=0
    for tok in "${toks[@]}"; do
      spec="${tok%%/*}"
      [[ "$spec" =~ ^[0-9][0-9,:]*$ ]] || continue
      for port in "$@"; do
        if port_in_spec "$port" "$spec"; then
          covers=1
        fi
      done
    done
    if ((covers)); then
      info "removing firewall rule: ufw ${rule}"
      # Word-splitting is intended: the rule text becomes ufw's arguments again.
      # shellcheck disable=SC2086
      if ufw --force delete ${rule} >/dev/null; then
        REMOVED_RULES+=("ufw ${rule}")
      else
        warn "could not delete 'ufw ${rule}' - remove it by hand: ufw status numbered; ufw delete <N>"
      fi
    fi
  done < <(ufw show added 2>/dev/null || true)
}

APT_UPDATED=0
apt_install() {
  if ((!APT_UPDATED)); then
    apt-get -o DPkg::Lock::Timeout=300 update -y
    APT_UPDATED=1
  fi
  apt-get -o DPkg::Lock::Timeout=300 install -y --no-install-recommends "$@"
}

# Newest python3 >= 3.11 on PATH (the backend uses datetime.UTC), or $PYTHON_BIN.
find_python() {
  local c
  for c in ${PYTHON_BIN:-} python3.14 python3.13 python3.12 python3.11 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
      command -v "$c"
      return 0
    fi
  done
  return 1
}

service_user_can_run() {
  runuser -u "$SERVICE_USER" -- test -r "$BACKEND_DIR/app/main.py" &&
    runuser -u "$SERVICE_USER" -- test -x "$VENV/bin/uvicorn"
}

detect_public_ip() {
  local ip
  ip="$(curl -fsS --max-time 2 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || true)"
  if [[ -z "$ip" && "$PUBLIC_HOST" =~ ^([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})-([0-9]{1,3})\.sslip\.io$ ]]; then
    ip="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.${BASH_REMATCH[3]}.${BASH_REMATCH[4]}"
  fi
  printf '%s' "${ip:-<droplet-ip>}"
}

# --------------------------------------------------------------------------- arguments

positional=()
while (($#)); do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --public-host)
      [[ $# -ge 2 ]] || die "--public-host needs a value"
      PUBLIC_HOST="$2"; shift 2 ;;
    --repo-dir)
      [[ $# -ge 2 ]] || die "--repo-dir needs a value"
      REPO_DIR="$2"; shift 2 ;;
    -*) die "unknown option: $1 (see --help)" ;;
    *) positional+=("$1"); shift ;;
  esac
done
if ((${#positional[@]} > 2)); then
  die "too many arguments (see --help)"
fi
if ((${#positional[@]} >= 1)); then PUBLIC_HOST="${positional[0]}"; fi
if ((${#positional[@]} >= 2)); then REPO_DIR="${positional[1]}"; fi

if [[ $EUID -ne 0 ]]; then
  die "run as root, e.g.: sudo bash $0 ${PUBLIC_HOST:-<PUBLIC_HOST>}"
fi
if ! command -v apt-get >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1; then
  die "this script expects Ubuntu with apt + systemd (DigitalOcean AI/ML-ready or inference-optimized GPU image)"
fi

REPO_DIR="${REPO_DIR:-/opt/guardian}"
REPO_DIR="${REPO_DIR%/}"
if [[ ! "$REPO_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
  die "REPO_DIR must be an absolute path made of [A-Za-z0-9._/-] (got '$REPO_DIR')"
fi
case "$REPO_DIR/" in
  /root/* | /home/*)
    die "REPO_DIR=$REPO_DIR is inside a home directory, which the '$SERVICE_USER' service user cannot read (and ProtectHome blocks). Use /opt/guardian." ;;
esac
BACKEND_DIR="$REPO_DIR/backend"
VENV="$BACKEND_DIR/.venv"
ENV_FILE="$REPO_DIR/.env"

if [[ -z "$PUBLIC_HOST" ]]; then
  PUBLIC_HOST="$(env_get PUBLIC_HOST "$ENV_FILE")"
fi
if [[ -z "$PUBLIC_HOST" ]]; then
  usage >&2
  die "PUBLIC_HOST is required (no domain? use <ip-with-dashes>.sslip.io, e.g. 203-0-113-10.sslip.io)"
fi
if [[ "$PUBLIC_HOST" =~ ^[0-9]+(\.[0-9]+){3}$ ]]; then
  die "PUBLIC_HOST=$PUBLIC_HOST is a bare IP; Caddy cannot get a public certificate for it. Use ${PUBLIC_HOST//./-}.sslip.io"
fi
host_re='^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$'
if [[ ! "$PUBLIC_HOST" =~ $host_re ]]; then
  die "PUBLIC_HOST='$PUBLIC_HOST' must be a plain host name (no scheme, port or path), e.g. 203-0-113-10.sslip.io"
fi

printf 'Guardian bootstrap: PUBLIC_HOST=%s REPO_DIR=%s SERVICE_USER=%s\n' "$PUBLIC_HOST" "$REPO_DIR" "$SERVICE_USER"

# --------------------------------------------------------------------------- 1. GPU

log "1/7 GPU check (nvidia-smi)"
if ! command -v nvidia-smi >/dev/null 2>&1 || ! nvidia-smi >/dev/null 2>&1; then
  die "no NVIDIA GPU visible (nvidia-smi is missing or failing). Create a DigitalOcean GPU Droplet with
       the AI/ML-ready image (NVIDIA drivers preinstalled) or the inference-optimized image and re-run.
       Without a GPU, Ollama would silently run the models on CPU."
fi
GPU_NAME="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -n1)"
info "GPU: $GPU_NAME"

# zstd: recent Ollama installers ship .tar.zst archives.
apt_install curl ca-certificates gnupg iproute2 zstd

# --------------------------------------------------------------------------- 2. Ollama

log "2/7 Ollama (official installer, minimum $OLLAMA_MIN_VERSION)"
OLLAMA_INSTALLED_NOW=0
if ! command -v ollama >/dev/null 2>&1; then
  info "Ollama not installed - running https://ollama.com/install.sh"
  curl -fsSL https://ollama.com/install.sh | sh
  OLLAMA_INSTALLED_NOW=1
fi
OLLAMA_VERSION="$(ollama_version)"
if [[ -z "$OLLAMA_VERSION" ]] || ! version_ge "$OLLAMA_VERSION" "$OLLAMA_MIN_VERSION"; then
  info "Ollama '${OLLAMA_VERSION:-unknown}' is older than $OLLAMA_MIN_VERSION - upgrading by re-running the installer"
  curl -fsSL https://ollama.com/install.sh | sh
  OLLAMA_INSTALLED_NOW=1
  OLLAMA_VERSION="$(ollama_version)"
fi
if [[ -n "$OLLAMA_VERSION" ]] && version_ge "$OLLAMA_VERSION" "$OLLAMA_MIN_VERSION"; then
  info "Ollama $OLLAMA_VERSION OK"
else
  warn "Ollama is still '${OLLAMA_VERSION:-unknown}' (< $OLLAMA_MIN_VERSION) after the installer; Gemma 4 may not load. Check https://github.com/ollama/ollama/releases and set OLLAMA_MIN_VERSION if the minimum differs."
fi

# --------------------------------------------------------------------------- 3. Ollama override

log "3/7 ollama.service override (127.0.0.1:$OLLAMA_PORT only)"
install -d -m 755 "$(dirname "$OLLAMA_OVERRIDE")"
override_tmp="$(mktemp)"
cat > "$override_tmp" <<'EOF'
# Managed by infra/bootstrap_droplet.sh (MEDGEMMA_DO_SETUP.md section 3).
# Loopback only: never OLLAMA_HOST=0.0.0.0, never OLLAMA_ORIGINS=*.
[Service]
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_CONTEXT_LENGTH=16384"
EOF
OVERRIDE_CHANGED=0
if ! cmp -s "$override_tmp" "$OLLAMA_OVERRIDE"; then
  install -m 644 "$override_tmp" "$OLLAMA_OVERRIDE"
  OVERRIDE_CHANGED=1
  info "wrote $OLLAMA_OVERRIDE"
else
  info "$OLLAMA_OVERRIDE already up to date"
fi
rm -f "$override_tmp"
systemctl daemon-reload

# Another drop-in could still override ours; refuse to continue rather than expose Ollama.
ollama_env="$(systemctl show ollama --property=Environment --value 2>/dev/null || true)"
if [[ " $ollama_env " != *" OLLAMA_HOST=127.0.0.1:11434 "* ]]; then
  die "the effective OLLAMA_HOST of ollama.service is not 127.0.0.1:11434 - another file in
       $(dirname "$OLLAMA_OVERRIDE")/ (or the unit itself) overrides it. Remove that setting and re-run.
       Effective environment: $ollama_env"
fi
if [[ "$ollama_env" == *OLLAMA_ORIGINS=* ]]; then
  if [[ "$ollama_env" =~ OLLAMA_ORIGINS=[^[:space:]]*\* ]]; then
    die "OLLAMA_ORIGINS contains '*' for ollama.service - remove it (never OLLAMA_ORIGINS=*)"
  fi
  warn "OLLAMA_ORIGINS is set for ollama.service; nothing browser-based should talk to Ollama - consider removing it"
fi

systemctl enable ollama >/dev/null 2>&1 || true
if ((OVERRIDE_CHANGED || OLLAMA_INSTALLED_NOW)) || ! systemctl is-active --quiet ollama; then
  info "restarting ollama (models will cold-load; run infra/pull_models.sh to warm them up)"
  systemctl restart ollama
else
  info "ollama already running with this configuration - not restarting (keeps models warm)"
fi
if ! wait_for_http "http://127.0.0.1:${OLLAMA_PORT}/api/version" 60; then
  die "Ollama did not answer on 127.0.0.1:${OLLAMA_PORT} (check: journalctl -u ollama -n 50 --no-pager)"
fi
assert_loopback_only "$OLLAMA_PORT"

# --------------------------------------------------------------------------- 4. Firewall

log "4/7 Firewall (ufw): deny incoming, allow 22,80,443/tcp only"
apt_install ufw
remove_ufw_rules "$OLLAMA_PORT" "$API_PORT"
if [[ -z "$(ss -Hltn 'sport = :22' 2>/dev/null || true)" ]]; then
  die "nothing listens on port 22: enabling ufw with only 22/80/443 open would lock you out.
       Allow your SSH port manually (ufw allow <port>/tcp) and re-run."
fi
ufw default deny incoming
ufw default allow outgoing
ufw allow 22,80,443/tcp
ufw --force enable
ufw status verbose | sed 's/^/    /'

# --------------------------------------------------------------------------- 5. Caddy

log "5/7 Caddy: https://${PUBLIC_HOST} -> 127.0.0.1:${API_PORT}"
if ! command -v caddy >/dev/null 2>&1; then
  # Official instructions: https://caddyserver.com/docs/install#debian-ubuntu-raspbian
  apt_install debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  if [[ ! -s /usr/share/keyrings/caddy-stable-archive-keyring.gpg ]]; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
      | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  fi
  if [[ ! -s /etc/apt/sources.list.d/caddy-stable.list ]]; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
      > /etc/apt/sources.list.d/caddy-stable.list
  fi
  chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
  APT_UPDATED=0
  apt_install caddy
fi
install -d -m 755 "$(dirname "$CADDYFILE")"
if [[ -f "$CADDYFILE" && ! -f "$CADDYFILE.orig" ]] && ! grep -q 'Managed by infra/bootstrap_droplet.sh' "$CADDYFILE"; then
  cp -p "$CADDYFILE" "$CADDYFILE.orig"
  info "kept the original Caddyfile as $CADDYFILE.orig"
fi
{
  printf '# Managed by infra/bootstrap_droplet.sh - the ONLY public entry point.\n'
  printf '# FastAPI listens on 127.0.0.1:%s; Ollama (127.0.0.1:11434) is never proxied.\n' "$API_PORT"
  printf '%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$PUBLIC_HOST" "$API_PORT"
} > "$CADDYFILE"
caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null
systemctl enable caddy >/dev/null 2>&1 || true
if systemctl is-active --quiet caddy; then
  systemctl reload caddy
else
  systemctl restart caddy
fi
info "Caddy serves https://${PUBLIC_HOST} (certificate is obtained automatically on first request)"

# --------------------------------------------------------------------------- 6. API service

log "6/7 Python venv + guardian-api.service"
if [[ "$SERVICE_USER" == "root" ]]; then
  warn "SERVICE_USER=root: the API will run as root (a dedicated user is safer)"
elif ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --user-group --home-dir "$STATE_DIR" --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
  info "created system user '$SERVICE_USER'"
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"
install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 750 "$STATE_DIR"

if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
  REPO_PRESENT=1
  PY="$(find_python || true)"
  if [[ -z "$PY" ]]; then
    info "no Python >= 3.11 on PATH (Ubuntu 22.04 ships 3.10) - installing one with apt"
    apt_install python3.12 python3.12-venv || apt_install python3.11 python3.11-venv || true
    PY="$(find_python || true)"
  fi
  if [[ -z "$PY" ]]; then
    die "need Python >= 3.11 for the backend. Install one (e.g. the deadsnakes PPA:
       add-apt-repository -y ppa:deadsnakes/ppa && apt-get install -y python3.12 python3.12-venv)
       or point PYTHON_BIN at it, then re-run."
  fi
  py_ver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    info "creating $VENV with $PY (Python $py_ver)"
    rm -rf "$VENV"
    # Debian/Ubuntu split ensurepip into pythonX.Y-venv.
    apt_install "python${py_ver}-venv" >/dev/null 2>&1 || true
    "$PY" -m venv "$VENV" || die "could not create $VENV (install python${py_ver}-venv and re-run)"
  else
    info "reusing $VENV ($("$VENV/bin/python" --version 2>&1))"
  fi
  info "installing backend/requirements.txt (quiet; takes a minute)"
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --upgrade pip
  "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check -r "$BACKEND_DIR/requirements.txt"

  # The service user must be able to read the code and run the venv.
  if [[ "$SERVICE_USER" != "root" ]] && ! service_user_can_run; then
    info "making $BACKEND_DIR readable for '$SERVICE_USER'"
    chmod o+rx "$REPO_DIR"
    chmod -R o+rX "$BACKEND_DIR"
  fi
  if [[ -f "$BACKEND_DIR/.env" ]]; then
    warn "$BACKEND_DIR/.env exists: the app would read it in addition to $ENV_FILE (values from systemd win). Remove it on the droplet to avoid confusion."
  fi
else
  warn "repo not found: $BACKEND_DIR/requirements.txt is missing. Copy the repo to $REPO_DIR, then re-run this script:
       macOS/Linux : rsync -az --exclude .git --exclude .venv --exclude __pycache__ --exclude .env ./ root@<droplet-ip>:$REPO_DIR/
       Windows     : git -c core.autocrlf=false archive --format=tar HEAD | ssh root@<droplet-ip> \"mkdir -p $REPO_DIR && tar -xf - -C $REPO_DIR\"
       on droplet  : git clone <repo-url> $REPO_DIR"
fi

# Environment file: systemd (PID 1) reads it before dropping privileges, so root:root 600 is fine.
if [[ -f "$ENV_FILE" ]]; then
  chown root:root "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  info "$ENV_FILE: root:root, mode 600"
  inline="$(grep -nE '^[A-Za-z_][A-Za-z0-9_]*=[^#]*[[:space:]]#' "$ENV_FILE" | cut -d= -f1 || true)"
  if [[ -n "$inline" ]]; then
    warn "inline '# comments' after values in $ENV_FILE (line:KEY = $(printf '%s' "$inline" | tr '\n' ' ')). systemd keeps them as part of the value - move comments to their own line."
  fi
  if [[ -z "$(env_get GUARDIAN_API_KEY "$ENV_FILE")" ]]; then
    warn "GUARDIAN_API_KEY is empty in $ENV_FILE: guardian-api stays stopped (auth would be disabled on a public host). Generate one with: openssl rand -hex 32"
  else
    ENV_OK=1
  fi
  pbu="$(env_get PUBLIC_BASE_URL "$ENV_FILE")"
  if [[ "${pbu%/}" != "https://${PUBLIC_HOST}" ]]; then
    warn "PUBLIC_BASE_URL is '${pbu:-<unset>}' but should be https://${PUBLIC_HOST} (Twilio signatures and the ElevenLabs URLs depend on it)"
  fi
  ollama_url="$(env_get OLLAMA_URL "$ENV_FILE")"
  ollama_url="${ollama_url:-$(env_get MEDGEMMA_URL "$ENV_FILE")}"
  case "${ollama_url:-http://127.0.0.1:11434}" in
    http://127.0.0.1:* | http://localhost:*) ;;
    *) warn "OLLAMA_URL=$ollama_url is not loopback; on the droplet it must be http://127.0.0.1:11434" ;;
  esac
  db_url="$(env_get DATABASE_URL "$ENV_FILE")"
  if [[ "$db_url" == sqlite* && "$db_url" != *:////* ]]; then
    warn "DATABASE_URL is a relative SQLite path; '$SERVICE_USER' cannot write to $BACKEND_DIR. Use sqlite:///${STATE_DIR}/guardian.db (four slashes in total) or Postgres."
  fi
  for key in ELEVEN_WEBHOOK_SECRET ELEVEN_TOOL_AUTH_TOKEN TWILIO_AUTH_TOKEN; do
    if [[ -z "$(env_get "$key" "$ENV_FILE")" ]]; then
      warn "$key is empty: the matching webhooks will reject every request until it is set (docs/agent_setup.md)"
    fi
  done
else
  warn "$ENV_FILE is missing: copy .env.example there (chmod 600) and fill it in (GUARDIAN_API_KEY, PUBLIC_BASE_URL=https://${PUBLIC_HOST}, DATABASE_URL, ElevenLabs, Twilio), then re-run this script"
fi

api_unit_tmp="$(mktemp)"
cat > "$api_unit_tmp" <<EOF
# Managed by infra/bootstrap_droplet.sh - re-run it instead of editing this file.
[Unit]
Description=Guardian API (FastAPI on 127.0.0.1:${API_PORT}; Caddy is the only public door)
Wants=network-online.target
After=network-online.target ollama.service

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_GROUP}
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=${ENV_FILE}
# Never serve the API with X-API-Key auth disabled on a public host.
ExecStartPre=/bin/sh -c 'test -n "\$\${GUARDIAN_API_KEY}" || { echo "GUARDIAN_API_KEY is empty in ${ENV_FILE}: refusing to start" >&2; exit 1; }'
# One process on purpose: the APScheduler (callbacks, retries) runs in-process.
ExecStart=${VENV}/bin/uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}
Restart=on-failure
RestartSec=5
StateDirectory=guardian
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
install -m 644 "$api_unit_tmp" "$API_UNIT"
rm -f "$api_unit_tmp"
systemctl daemon-reload
info "wrote $API_UNIT"

if ((REPO_PRESENT && ENV_OK)); then
  info "creating missing database tables (non-destructive create_all; demo data: see summary)"
  if systemd-run --quiet --wait --pipe --collect \
      -p "User=${SERVICE_USER}" -p "EnvironmentFile=${ENV_FILE}" -p "WorkingDirectory=${BACKEND_DIR}" \
      "${VENV}/bin/python" -c 'import app.models; from app.database import Base, engine; Base.metadata.create_all(engine)'; then
    TABLES_OK=1
  else
    warn "could not create the database tables - is DATABASE_URL in $ENV_FILE reachable? guardian-api not started."
  fi
fi

if ((REPO_PRESENT && ENV_OK && TABLES_OK)); then
  systemctl enable guardian-api >/dev/null 2>&1
  systemctl restart guardian-api
  if wait_for_http "http://127.0.0.1:${API_PORT}/health" 45; then
    API_STATE="running"
    info "guardian-api answers on http://127.0.0.1:${API_PORT}/health"
  else
    API_STATE="started but /health does not answer"
    warn "guardian-api did not answer on 127.0.0.1:${API_PORT}/health - check: journalctl -u guardian-api -n 80 --no-pager"
  fi
  assert_loopback_only "$API_PORT"
else
  info "guardian-api not started (see warnings); after fixing them re-run this script"
fi

# --------------------------------------------------------------------------- 7. Summary

if [[ "$API_STATE" == "running" ]]; then
  code=""
  for ((i = 0; i < 30; i++)); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "https://${PUBLIC_HOST}/health" 2>/dev/null || true)"
    if [[ -n "$code" && "$code" != "000" ]]; then break; fi
    sleep 2
  done
  case "$code" in
    200) HTTPS_STATE="https://${PUBLIC_HOST}/health -> 200 (TLS certificate OK)" ;;
    "" | 000)
      HTTPS_STATE="not reachable yet"
      warn "https://${PUBLIC_HOST} is not reachable yet (TLS/DNS). Caddy needs inbound 80 + 443 (ufw AND the DigitalOcean Cloud Firewall) and a name resolving to this droplet. Check: journalctl -u caddy -n 50 --no-pager" ;;
    *) HTTPS_STATE="https://${PUBLIC_HOST}/health -> HTTP $code" ;;
  esac
fi

DROPLET_IP="$(detect_public_ip)"
log "7/7 Summary"
cat <<EOF
    GPU            : ${GPU_NAME}
    Ollama         : ${OLLAMA_VERSION:-unknown} on 127.0.0.1:${OLLAMA_PORT} only
                     ($OLLAMA_OVERRIDE: KEEP_ALIVE=24h, MAX_LOADED_MODELS=2,
                      NUM_PARALLEL=2, CONTEXT_LENGTH=16384)
    Firewall (ufw) : default deny incoming; allow 22,80,443/tcp
                     removed rules: ${REMOVED_RULES[*]:-none}
    Caddy          : https://${PUBLIC_HOST} -> 127.0.0.1:${API_PORT}   [${HTTPS_STATE}]
    API            : guardian-api.service as '${SERVICE_USER}', ${BACKEND_DIR}, venv ${VENV},
                     EnvironmentFile=${ENV_FILE}   [${API_STATE}]

Next steps
    1. Pull, warm up and smoke-test the models (add --with-27b on an L40S/H100):
         bash ${REPO_DIR}/infra/pull_models.sh --env-file ${ENV_FILE}
    2. Optional fake demo data (DROPS and recreates all tables):
         systemd-run --pty --wait --collect -p User=${SERVICE_USER} -p EnvironmentFile=${ENV_FILE} \\
           -p WorkingDirectory=${BACKEND_DIR} ${VENV}/bin/python -m app.seed
    3. Point ElevenLabs and Twilio at https://${PUBLIC_HOST} (docs/agent_setup.md)

Verify on the droplet
    nvidia-smi
    ollama ps                               # after step 1: both models, PROCESSOR = 100% GPU
    ss -ltnp | grep -E ':(${OLLAMA_PORT}|${API_PORT})\\b'   # must show 127.0.0.1 only
    ufw status verbose
    curl -s -o /dev/null -w '%{http_code}\\n' https://${PUBLIC_HOST}/health/models   # 401 without the key
    curl -s -H "X-API-Key: <GUARDIAN_API_KEY>" https://${PUBLIC_HOST}/health/models  # 200 with the key

Verify from OUTSIDE (your laptop) - both must FAIL / time out
    nc -zv -w 5 ${DROPLET_IP} ${OLLAMA_PORT}
    nc -zv -w 5 ${DROPLET_IP} ${API_PORT}

Teammates who need Ollama directly: SSH tunnel only, never a firewall rule
    ssh -N -L ${OLLAMA_PORT}:127.0.0.1:${OLLAMA_PORT} root@${DROPLET_IP}
    (then OLLAMA_URL=http://127.0.0.1:${OLLAMA_PORT} on the laptop)
EOF

if ((${#WARNINGS[@]})); then
  printf '\nNeeds your attention (%d):\n' "${#WARNINGS[@]}"
  for w in "${WARNINGS[@]}"; do
    printf '  - %s\n' "$w"
  done
fi
