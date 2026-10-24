#!/usr/bin/env bash
# One-command deploy of Guardian to the DigitalOcean GPU droplet.
# Runs on the control machine (Git Bash / macOS / Linux); everything is hosted
# on the droplet: Ollama (MedGemma + Gemma 4 on the GPU), FastAPI, Postgres and
# Caddy (HTTPS via sslip.io). The Flutter app is built/hosted separately and
# talks to https://<ip-with-dashes>.sslip.io with the X-API-Key header.
#
#   bash infra/deploy.sh <droplet-ip> [--seed] [--skip-models]
#
#   --seed            drop + recreate tables and load the fake demo data (first deploy)
#   --skip-models     don't (re)pull / warm up the models
#
# Only COMMITTED code is shipped (git archive HEAD). Secrets live in
# backend/.env.production (gitignored; created from backend/.env on first run)
# and are copied to /opt/guardian/.env (chmod 600) on the droplet.
set -euo pipefail

IP="${1:?usage: deploy.sh <droplet-ip> [--seed] [--skip-models]}"; shift
SEED=0; SKIP_MODELS=0
for arg in "$@"; do
  case "$arg" in
    --seed) SEED=1 ;; --skip-models) SKIP_MODELS=1 ;;
    *) echo "unknown option $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${IP//./-}.sslip.io"
REMOTE=/opt/guardian
PROD_ENV="$ROOT/backend/.env.production"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30)
ssh_do() { ssh "${SSH_OPTS[@]}" "root@$IP" "$@"; }

if [[ -x "$ROOT/backend/.venv/Scripts/python" ]]; then PY="$ROOT/backend/.venv/Scripts/python"
elif [[ -x "$ROOT/backend/.venv/bin/python" ]]; then PY="$ROOT/backend/.venv/bin/python"
else PY="python3"; fi

if [[ -n "$(git -C "$ROOT" status --porcelain -- backend infra)" ]]; then
  echo "!! uncommitted changes in backend/ infra/ will NOT be deployed (git archive HEAD)"
fi

echo "==> [1/8] production env ($PROD_ENV)"
[[ -f "$PROD_ENV" ]] || cp "$ROOT/backend/.env" "$PROD_ENV"
(cd "$ROOT/backend" && "$PY" - "$PROD_ENV" "$HOST" <<'PYEOF'
import re, secrets, sys
from pathlib import Path
path, host = Path(sys.argv[1]), sys.argv[2]
text = path.read_text(encoding="utf-8")
def get(k):
    m = re.search(rf"^{k}=(.*)$", text, re.M)
    return m.group(1).strip() if m else ""
def put(k, v):
    global text
    line = f"{k}={v}"
    text = re.sub(rf"^{k}=.*$", lambda _m: line, text, flags=re.M) if re.search(rf"^{k}=", text, re.M) \
        else text.rstrip("\n") + "\n" + line + "\n"
db_pw = get("DB_PASSWORD") or secrets.token_hex(16)
put("DB_PASSWORD", db_pw)
put("DATABASE_URL", f"postgresql+psycopg://guardian:{db_pw}@127.0.0.1:5432/guardian")
put("PUBLIC_HOST", host)
put("PUBLIC_BASE_URL", f"https://{host}")
put("OLLAMA_URL", "http://127.0.0.1:11434")
put("SCHEDULER_ENABLED", "true")
put("MOCK_TELEPHONY", "false")
if not get("GUARDIAN_API_KEY"): put("GUARDIAN_API_KEY", secrets.token_urlsafe(24))
path.write_text(text, encoding="utf-8")
PYEOF
)
envval() { grep -E "^$1=" "$PROD_ENV" | head -1 | cut -d= -f2-; }

echo "==> [2/8] ElevenLabs: point agent tools + webhooks at https://$HOST"
(cd "$ROOT/backend" && "$PY" -m scripts.provision_elevenlabs --env-file .env.production \
   --public-base-url "https://$HOST")

echo "==> [3/8] ship committed code to $REMOTE"
ssh_do "mkdir -p $REMOTE"
git -C "$ROOT" archive --format=tar HEAD | ssh_do "tar -x -C $REMOTE"
scp "${SSH_OPTS[@]}" -q "$PROD_ENV" "root@$IP:$REMOTE/.env"
ssh_do "chmod 600 $REMOTE/.env"

echo "==> [4/8] Postgres (localhost only)"
ssh_do "DB_PASSWORD='$(envval DB_PASSWORD)' bash $REMOTE/infra/setup_postgres.sh"

echo "==> [5/8] bootstrap (GPU check, Ollama loopback-only, ufw, Caddy, API service)"
ssh_do "PUBLIC_HOST='$HOST' REPO_DIR='$REMOTE' bash $REMOTE/infra/bootstrap_droplet.sh"

if [[ $SKIP_MODELS -eq 0 ]]; then
  echo "==> [6/8] pull + warm up models on the GPU"
  ssh_do "bash $REMOTE/infra/pull_models.sh --env-file $REMOTE/.env"
  # pull_models.sh may switch tags/pipeline (fallbacks): keep the local copy in sync.
  scp "${SSH_OPTS[@]}" -q "root@$IP:$REMOTE/.env" "$PROD_ENV"
else
  echo "==> [6/8] models: skipped"
fi

if [[ $SEED -eq 1 ]]; then
  echo "==> [7/8] seed demo data (drops and recreates tables)"
  ssh_do "cd $REMOTE/backend && set -a && . $REMOTE/.env && set +a && .venv/bin/python -m app.seed"
else
  echo "==> [7/8] seed: skipped (pass --seed on the first deploy)"
fi

echo "==> [8/8] restart API"
ssh_do "systemctl restart guardian-api && sleep 3 && systemctl is-active guardian-api"


echo
echo "==> checks"
curl -fsS "https://$HOST/health" && echo
curl -fsS -H "X-API-Key: $(envval GUARDIAN_API_KEY)" "https://$HOST/health/models" | head -c 1500 && echo
echo
echo "API:        https://$HOST   (X-API-Key: see GUARDIAN_API_KEY in backend/.env.production)"
echo "Flutter:    API_BASE=https://$HOST, send X-API-Key; for a hosted web build add its origin to CORS_ORIGINS"
echo "Ollama:     loopback only. Tunnel: ssh -L 11434:127.0.0.1:11434 root@$IP"
echo "Teardown:   destroy the droplet when done - powered-off droplets still bill."
