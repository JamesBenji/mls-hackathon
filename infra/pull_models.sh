#!/usr/bin/env bash
# Guardian model pulls + verification (MEDGEMMA_DO_SETUP.md section 4).
# Run ON the droplet after infra/bootstrap_droplet.sh (as root if it should update the env file):
#
#   bash /opt/guardian/infra/pull_models.sh [--env-file /opt/guardian/.env] [--with-27b] [--no-env-update]
#
# Model tags change, so this script verifies before it assumes:
#   1. pulls GEMMA4_MODEL (default gemma4:12b). Gemma 4 tags reported on Ollama: e2b, e4b, 12b,
#      26b, 31b. A BARE "gemma4" tag resolves to the small E4B variant, so explicit tags only.
#      QAT builds such as gemma4:12b-it-qat are smaller if VRAM is tight.
#   2. pulls MEDGEMMA_MODEL (default medgemma:4b, multimodal, ~3.3 GB). If that fails it prints
#      Fallback A (import a community GGUF as medgemma-local; needs a human) and applies
#      Fallback B (CLINICAL_PIPELINE=single, Gemma 4 for everything).
#   3. --with-27b: also pulls medgemma:27b (reported text-only; L40S/H100) and uses it for the
#      clinical reasoning stage.
#   4. writes the tags that actually work into the env file (GEMMA4_MODEL, MEDGEMMA_MODEL,
#      CLINICAL_PIPELINE) and restarts guardian-api if it is running.
#   5. warm-up: one request per model with keep_alive "24h" and an explicit options.num_ctx.
#      Context gotcha: Ollama reportedly defaults Gemma 4 to a 4K context, which silently
#      truncates long transcripts and history. Guardian always sends num_ctx (OLLAMA_NUM_CTX,
#      16384); a larger context costs VRAM.
#   6. prints `ollama list` / `ollama ps` and WARNs unless PROCESSOR says "100% GPU".
#   7. smoke-tests both models through POST /api/chat (stream false) and prints the latency.
#
# Tag sources, highest priority first: environment variable, env file, default.
if grep -qU $'\r' "$0" 2>/dev/null; then printf 'ERROR: %s has Windows (CRLF) line endings. Fix: sed -i "s/\\r$//" %s\n' "$0" "$0" >&2; exit 1; fi #

set -euo pipefail
export LC_ALL=C

DEFAULT_GEMMA4=gemma4:12b
DEFAULT_MEDGEMMA=medgemma:4b
LOCAL_MEDGEMMA=medgemma-local  # Fallback A: ollama create medgemma-local -f Modelfile
ENV_FILE=/opt/guardian/.env
WITH_27B=0
UPDATE_ENV=1

WARNINGS=()
FAILURES=0
CHANGED=()
PREV_FALLBACK_B=0
FALLBACK=none

log()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { WARNINGS+=("$*"); printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: bash $0 [--env-file PATH] [--with-27b] [--no-env-update]

  --env-file PATH   env file to read tags from and update (default /opt/guardian/.env)
  --with-27b        also pull medgemma:27b (L40S/H100) and use it for clinical reasoning
  --no-env-update   only pull/verify; print the env lines instead of writing them

Environment: GEMMA4_MODEL (default $DEFAULT_GEMMA4), MEDGEMMA_MODEL (default $DEFAULT_MEDGEMMA),
MEDGEMMA_27B_MODEL (default medgemma:27b), OLLAMA_URL (default http://127.0.0.1:11434, must be
loopback or an SSH tunnel), OLLAMA_NUM_CTX (default 16384).
EOF
}

# --------------------------------------------------------------------------- helpers

# env_get KEY FILE: value of KEY in a dotenv/systemd env file (last assignment wins,
# surrounding quotes stripped).
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

# set_env_var FILE KEY VALUE: replace every KEY= line, or append one. Rewrites the file in
# place (cat >) so its owner and mode (root:root 600) are kept.
set_env_var() {
  local file="$1" key="$2" value="$3" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$file"; then
    awk -v k="$key" -v v="$value" 'index($0, k "=") == 1 { print k "=" v; next } { print }' "$file" > "$tmp"
  else
    cat "$file" > "$tmp"
    if [[ -s "$tmp" && -n "$(tail -c1 "$tmp")" ]]; then
      printf '\n' >> "$tmp"
    fi
    printf '%s=%s\n' "$key" "$value" >> "$tmp"
  fi
  cat "$tmp" > "$file"
  rm -f "$tmp"
}

is_loopback_url() {
  case "$1" in
    http://127.0.0.1 | http://127.0.0.1:* | http://localhost | http://localhost:* | "http://[::1]:"*) return 0 ;;
    *) return 1 ;;
  esac
}

valid_tag() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._/:-]*$ ]]
}

# ensure_model TAG: pull from the registry; if that fails but the model exists locally
# (e.g. created from a Modelfile with `ollama create`), use the local copy.
ensure_model() {
  local tag="$1"
  info "ollama pull $tag"
  if ollama pull "$tag"; then
    return 0
  fi
  if ollama show "$tag" >/dev/null 2>&1; then
    warn "pulling $tag failed, but it exists locally (ollama create?) - using the local copy"
    return 0
  fi
  return 1
}

BODY_FILE="$(mktemp)"
ERR_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE" "$ERR_FILE"' EXIT
REPLY_CODE=""
REPLY_TIME=""

# chat_request MODEL PROMPT NUM_PREDICT MAX_SECONDS [FORMAT_JSON]
# POST /api/chat with stream=false, keep_alive "24h" and an explicit num_ctx. The reply body
# lands in $BODY_FILE; sets REPLY_CODE / REPLY_TIME (curl -w). PROMPT must not contain quotes.
chat_request() {
  local model="$1" prompt="$2" num_predict="$3" max_s="$4" format="${5:-}" extra="" payload out
  if [[ -n "$format" ]]; then
    extra=",\"format\":${format}"
  fi
  payload="$(printf '{"model":"%s","messages":[{"role":"user","content":"%s"}],"stream":false,"keep_alive":"24h","options":{"num_ctx":%s,"temperature":0,"num_predict":%s}%s}' \
    "$model" "$prompt" "$NUM_CTX" "$num_predict" "$extra")"
  : > "$BODY_FILE"
  out="$(curl -sS -o "$BODY_FILE" -w '%{http_code} %{time_total}' --max-time "$max_s" \
    -H 'Content-Type: application/json' -d "$payload" "$OLLAMA_URL/api/chat" 2> "$ERR_FILE" || true)"
  REPLY_CODE="${out%% *}"
  REPLY_TIME="${out##* }"
  [[ "$REPLY_CODE" == "200" ]]
}

# First ~160 chars of the model reply (or the error) from $BODY_FILE.
reply_excerpt() {
  local text=""
  if command -v python3 >/dev/null 2>&1; then
    text="$(python3 - "$BODY_FILE" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    sys.exit(1)
msg = data.get("message") or {}
text = msg.get("content") or ""
if not text and msg.get("thinking"):
    text = "[thinking only] " + msg["thinking"]
text = text or data.get("error") or ""
print(" ".join(text.split())[:160])
PY
)"
  fi
  if [[ -z "$text" ]]; then
    text="$(head -c 160 "$BODY_FILE" 2>/dev/null | tr '\n' ' ' || true)"
  fi
  if [[ -z "$text" ]]; then
    text="$(head -c 160 "$ERR_FILE" 2>/dev/null | tr '\n' ' ' || true)"
  fi
  printf '%s' "${text:-<empty>}"
}

print_fallbacks() {
  cat <<EOF

    MedGemma tag '$MEDGEMMA_MODEL' is not available on this Ollama. Options:

    First check the library for the current tag: https://ollama.com/library and
    https://ollama.com/search?q=medgemma - then re-run with MEDGEMMA_MODEL=<tag>
    (community uploads are unofficial; a human should decide whether to trust one).

    Fallback A - import a community GGUF of google/medgemma-4b-it (needs a HUMAN):
      1. Accept Google's Health AI Developer Foundations terms on
         https://huggingface.co/google/medgemma-4b-it and create a read token at
         https://huggingface.co/settings/tokens (community GGUF repos may be gated too).
      2. Download a GGUF build (e.g. Q4_K_M) without putting the token in shell history:
           read -rs HF_TOKEN && export HF_TOKEN
           mkdir -p /opt/models && cd /opt/models
           curl -fL -H "Authorization: Bearer \$HF_TOKEN" -o medgemma-4b-it.gguf \\
             "https://huggingface.co/<community-repo>/resolve/main/<file>.gguf"
      3. printf 'FROM ./medgemma-4b-it.gguf\n' > Modelfile
         (if replies look garbled, add the chat TEMPLATE shown by: ollama show --modelfile $GEMMA4_MODEL)
      4. ollama create $LOCAL_MEDGEMMA -f Modelfile
      5. re-run: MEDGEMMA_MODEL=$LOCAL_MEDGEMMA bash $0 --env-file $ENV_FILE
      HF_TOKEN is only for this download - never put it in the app's .env.

    Fallback B (applied now; see the SCOPE.md risks table): Gemma 4 alone for everything,
      CLINICAL_PIPELINE=single and MEDGEMMA_MODEL=$GEMMA4_MODEL in the env file.
      Record it in README.md:
        Model fallback in use: B (CLINICAL_PIPELINE=single, $GEMMA4_MODEL for everything)
EOF
}

# --------------------------------------------------------------------------- arguments

while (($#)); do
  case "$1" in
    -h | --help) usage; exit 0 ;;
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file needs a path"
      ENV_FILE="$2"; shift 2 ;;
    --with-27b) WITH_27B=1; shift ;;
    --no-env-update) UPDATE_ENV=0; shift ;;
    *) die "unknown argument: $1 (see --help)" ;;
  esac
done

OLLAMA_URL="${OLLAMA_URL:-$(env_get OLLAMA_URL "$ENV_FILE")}"
OLLAMA_URL="${OLLAMA_URL:-$(env_get MEDGEMMA_URL "$ENV_FILE")}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
OLLAMA_URL="${OLLAMA_URL%/}"
NUM_CTX="${OLLAMA_NUM_CTX:-$(env_get OLLAMA_NUM_CTX "$ENV_FILE")}"
NUM_CTX="${NUM_CTX:-16384}"
[[ "$NUM_CTX" =~ ^[0-9]+$ ]] || die "OLLAMA_NUM_CTX must be a number (got '$NUM_CTX')"

if [[ -z "${GEMMA4_MODEL:-}" ]]; then
  GEMMA4_MODEL="$(env_get GEMMA4_MODEL "$ENV_FILE")"
fi
GEMMA4_MODEL="${GEMMA4_MODEL:-$DEFAULT_GEMMA4}"

if [[ -z "${MEDGEMMA_MODEL:-}" ]]; then
  MEDGEMMA_MODEL="$(env_get MEDGEMMA_MODEL "$ENV_FILE")"
  if [[ -n "$MEDGEMMA_MODEL" && "$MEDGEMMA_MODEL" == "$GEMMA4_MODEL" ]]; then
    info "env file has MEDGEMMA_MODEL=$GEMMA4_MODEL (an earlier Fallback B) - retrying $DEFAULT_MEDGEMMA"
    MEDGEMMA_MODEL=""
    PREV_FALLBACK_B=1
  fi
fi
MEDGEMMA_MODEL="${MEDGEMMA_MODEL:-$DEFAULT_MEDGEMMA}"
MEDGEMMA_27B_MODEL="${MEDGEMMA_27B_MODEL:-medgemma:27b}"

for tag in "$GEMMA4_MODEL" "$MEDGEMMA_MODEL" "$MEDGEMMA_27B_MODEL"; do
  valid_tag "$tag" || die "invalid model tag '$tag'"
done
if [[ "$GEMMA4_MODEL" != *:* || "$GEMMA4_MODEL" == *:latest ]]; then
  die "GEMMA4_MODEL='$GEMMA4_MODEL' is a bare tag. A bare gemma4 resolves to the small E4B variant;
       use an explicit tag such as $DEFAULT_GEMMA4 (or gemma4:e4b if you really want E4B)."
fi

# --------------------------------------------------------------------------- preflight

command -v ollama >/dev/null 2>&1 || die "ollama CLI not found - run this on the droplet after infra/bootstrap_droplet.sh"
command -v curl >/dev/null 2>&1 || die "curl not found"
if ! is_loopback_url "$OLLAMA_URL"; then
  warn "OLLAMA_URL=$OLLAMA_URL is not loopback. Ollama has no auth: keep it on 127.0.0.1 and use an SSH tunnel (ssh -L 11434:127.0.0.1:11434 root@<droplet-ip>)"
fi
export OLLAMA_HOST="$OLLAMA_URL"  # the CLI and the curl checks talk to the same server
version_json="$(curl -fsS --max-time 5 "$OLLAMA_URL/api/version" 2>/dev/null || true)"
[[ -n "$version_json" ]] || die "Ollama is not answering at $OLLAMA_URL (systemctl status ollama; journalctl -u ollama -n 50)"
server_version="$(printf '%s' "$version_json" | sed -n 's/.*"version" *: *"\([^"]*\)".*/\1/p')"
printf 'Ollama %s at %s | GEMMA4_MODEL=%s MEDGEMMA_MODEL=%s num_ctx=%s | env file %s\n' \
  "${server_version:-?}" "$OLLAMA_URL" "$GEMMA4_MODEL" "$MEDGEMMA_MODEL" "$NUM_CTX" "$ENV_FILE"

# --------------------------------------------------------------------------- 1. Gemma 4

log "1/6 Gemma 4: $GEMMA4_MODEL"
if ! ensure_model "$GEMMA4_MODEL"; then
  die "could not pull $GEMMA4_MODEL. Check: ollama --version (Gemma 4 needs a recent Ollama, reported
       >= 0.22 - confirm in the release notes), the current tags on https://ollama.com/library/gemma4
       (e2b, e4b, 12b, 26b, 31b; QAT builds like gemma4:12b-it-qat are smaller), then re-run with
       GEMMA4_MODEL=<tag>. Gemma 4 is required in both pipelines."
fi

# --------------------------------------------------------------------------- 2. MedGemma

log "2/6 MedGemma: $MEDGEMMA_MODEL"
if ensure_model "$MEDGEMMA_MODEL"; then
  info "$MEDGEMMA_MODEL OK"
elif [[ "$MEDGEMMA_MODEL" != "$LOCAL_MEDGEMMA"* ]] && ollama show "$LOCAL_MEDGEMMA" >/dev/null 2>&1; then
  warn "$MEDGEMMA_MODEL is not available, but '$LOCAL_MEDGEMMA' (Fallback A) exists locally - using it"
  MEDGEMMA_MODEL="$LOCAL_MEDGEMMA"
else
  print_fallbacks
  FALLBACK=B
  MEDGEMMA_MODEL="$GEMMA4_MODEL"
  warn "Fallback B applied: CLINICAL_PIPELINE=single, MEDGEMMA_MODEL=$GEMMA4_MODEL (update the README 'Model fallback in use' line)"
fi

if ((WITH_27B)); then
  log "2b/6 MedGemma 27B: $MEDGEMMA_27B_MODEL (reported text-only on Ollama - verify)"
  vram_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -dc '0-9' || true)"
  if [[ -n "$vram_mib" ]] && ((vram_mib < 40000)); then
    warn "GPU has ${vram_mib} MiB VRAM; $MEDGEMMA_27B_MODEL next to $GEMMA4_MODEL needs an L40S (48 GB) or H100 - expect a CPU/GPU split"
  fi
  if ensure_model "$MEDGEMMA_27B_MODEL"; then
    MEDGEMMA_MODEL="$MEDGEMMA_27B_MODEL"
    FALLBACK=none
    info "using $MEDGEMMA_27B_MODEL for the clinical reasoning stage (keep medgemma:4b for Phase-2 vision)"
  else
    warn "could not pull $MEDGEMMA_27B_MODEL - keeping MEDGEMMA_MODEL=$MEDGEMMA_MODEL"
  fi
fi

if [[ "$FALLBACK" == B ]]; then
  PIPELINE=single
  FALLBACK_LINE="B (CLINICAL_PIPELINE=single, $GEMMA4_MODEL for everything)"
else
  PIPELINE=two_stage
  prev_pipeline="${CLINICAL_PIPELINE:-$(env_get CLINICAL_PIPELINE "$ENV_FILE")}"
  if [[ "$prev_pipeline" == single ]] && ((!PREV_FALLBACK_B)); then
    PIPELINE=single
    info "keeping CLINICAL_PIPELINE=single as configured (set two_stage to use MedGemma)"
  fi
  if [[ "$MEDGEMMA_MODEL" == "$LOCAL_MEDGEMMA"* ]]; then
    FALLBACK_LINE="A ($LOCAL_MEDGEMMA imported from a community GGUF)"
  else
    FALLBACK_LINE="none"
  fi
fi

# --------------------------------------------------------------------------- 3. env file

log "3/6 Env file: $ENV_FILE"
ENV_LINES=("GEMMA4_MODEL=$GEMMA4_MODEL" "MEDGEMMA_MODEL=$MEDGEMMA_MODEL" "CLINICAL_PIPELINE=$PIPELINE")
if ((UPDATE_ENV)) && [[ -f "$ENV_FILE" && -w "$ENV_FILE" ]]; then
  for kv in "${ENV_LINES[@]}"; do
    key="${kv%%=*}"
    val="${kv#*=}"
    if [[ "$(env_get "$key" "$ENV_FILE")" != "$val" ]]; then
      set_env_var "$ENV_FILE" "$key" "$val"
      CHANGED+=("$kv")
    fi
  done
  if ((${#CHANGED[@]})); then
    ENV_STATE="updated: ${CHANGED[*]}"
  else
    ENV_STATE="already up to date"
  fi
  info "$ENV_STATE"
else
  if ((UPDATE_ENV)); then
    warn "$ENV_FILE is missing or not writable (run as root?) - set these lines yourself"
  fi
  ENV_STATE="NOT updated - set: ${ENV_LINES[*]}"
  printf '    %s\n' "${ENV_LINES[@]}"
fi

# --------------------------------------------------------------------------- 4. warm-up

MODELS=("$GEMMA4_MODEL")
if [[ "$MEDGEMMA_MODEL" != "$GEMMA4_MODEL" ]]; then
  MODELS+=("$MEDGEMMA_MODEL")
fi

log "4/6 Warm-up: keep_alive 24h, options.num_ctx $NUM_CTX (first load can take a minute)"
for m in "${MODELS[@]}"; do
  if chat_request "$m" "Reply with the single word: ready" 1 600; then
    info "$m loaded and answering (${REPLY_TIME}s incl. cold load)"
  else
    warn "warm-up of $m failed (HTTP ${REPLY_CODE:-?}): $(reply_excerpt)"
    FAILURES=$((FAILURES + 1))
  fi
done

# --------------------------------------------------------------------------- 5. list / ps

log "5/6 ollama list / ollama ps"
ollama list 2>&1 | sed 's/^/    /' || true
ps_out="$(ollama ps 2>&1 || true)"
printf '%s\n' "$ps_out" | sed 's/^/    /'
for m in "${MODELS[@]}"; do
  # Untagged local models (medgemma-local) are listed as <name>:latest.
  line="$(printf '%s\n' "$ps_out" | awk -v n="$m" '$1 == n || $1 == n ":latest"' | head -n1)"
  if [[ -z "$line" ]]; then
    warn "$m is not loaded according to ollama ps (evicted? check OLLAMA_MAX_LOADED_MODELS and VRAM)"
  elif [[ "$line" != *"100% GPU"* ]]; then
    warn "$m is NOT on 100% GPU ($line). The model does not fit: drop to a smaller tag or a QAT build (e.g. gemma4:12b-it-qat)"
  else
    info "$m: 100% GPU"
  fi
done

# --------------------------------------------------------------------------- 6. smoke tests

log "6/6 Smoke test: POST $OLLAMA_URL/api/chat (stream false)"
# Gemma 4 with a JSON schema in `format`, the way the app calls it (chat_json).
if chat_request "$GEMMA4_MODEL" "Return a JSON object with ok set to true." 32 120 \
     '{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}'; then
  info "$GEMMA4_MODEL (JSON schema): ${REPLY_TIME}s -> $(reply_excerpt)"
else
  warn "smoke test of $GEMMA4_MODEL failed (HTTP ${REPLY_CODE:-?}): $(reply_excerpt)"
  FAILURES=$((FAILURES + 1))
fi
if [[ "$MEDGEMMA_MODEL" != "$GEMMA4_MODEL" ]]; then
  if chat_request "$MEDGEMMA_MODEL" "In one short sentence, what does SOAP stand for in clinical notes?" 64 120; then
    info "$MEDGEMMA_MODEL (free text): ${REPLY_TIME}s -> $(reply_excerpt)"
  else
    warn "smoke test of $MEDGEMMA_MODEL failed (HTTP ${REPLY_CODE:-?}): $(reply_excerpt)"
    FAILURES=$((FAILURES + 1))
  fi
fi

# --------------------------------------------------------------------------- summary

if ((${#CHANGED[@]})) && command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet guardian-api 2>/dev/null; then
  if [[ $EUID -eq 0 ]]; then
    systemctl restart guardian-api
    info "restarted guardian-api to pick up the new model settings"
  else
    warn "restart the API to pick up the new model settings: sudo systemctl restart guardian-api"
  fi
fi

log "Summary"
cat <<EOF
    GEMMA4_MODEL      = $GEMMA4_MODEL
    MEDGEMMA_MODEL    = $MEDGEMMA_MODEL
    CLINICAL_PIPELINE = $PIPELINE
    num_ctx           = $NUM_CTX (explicit on every request; Ollama's Gemma 4 default is reported as 4K)
    env file          = $ENV_STATE
    README.md line    : Model fallback in use: $FALLBACK_LINE

Notes
    - A bare "gemma4" tag is the small E4B model; keep explicit tags.
    - The first request after an Ollama restart is a cold load: re-run this script (or at
      least its warm-up) before a demo. OLLAMA_KEEP_ALIVE=24h keeps models resident after that.
    - CPU/GPU split in 'ollama ps' = the model does not fit; use a smaller tag or a QAT build.
EOF

if ((${#WARNINGS[@]})); then
  printf '\nNeeds your attention (%d):\n' "${#WARNINGS[@]}"
  for w in "${WARNINGS[@]}"; do
    printf '  - %s\n' "$w"
  done
fi
if ((FAILURES)); then
  printf '\n%d check(s) failed.\n' "$FAILURES" >&2
  exit 1
fi
