#!/usr/bin/env bash
# Track C: build Forge's image, deploy to Akash sandbox-2, run a task headless,
# retrieve the run's JSONL, and tear down.
#
#   ./scripts/akash_run.sh [task-file-or-string]
#
# Requires: docker (colima), provider-services v0.16+ (~/bin/provider-services-0.16),
# funded sandbox wallet (see docs/recon/akash.md), ANTHROPIC_API_KEY in .env.
# NOTE: the image is pushed to ttl.sh (PUBLIC, anonymous, expires in 4h) and the
# API key is sent to the (Akash-operated) sandbox provider via the manifest.
set -euo pipefail
cd "$(dirname "$0")/.."

PS="${PS:-$HOME/bin/provider-services-0.16}"
export AKASH_KEYRING_BACKEND=test AKASH_CHAIN_ID=sandbox-2 \
       AKASH_NODE=https://rpc.sandbox-2.aksh.pw:443 \
       AKASH_GAS=auto AKASH_GAS_ADJUSTMENT=1.5 AKASH_GAS_PRICES=0.025uakt
KEY_NAME=forge-hackathon
OWNER=$($PS keys show $KEY_NAME -a --keyring-backend test)
TASK="${1:-demo/task.md}"
API_KEY=$(grep '^ANTHROPIC_API_KEY=' .env | cut -d= -f2-)
[ -n "$API_KEY" ] || { echo "no ANTHROPIC_API_KEY in .env"; exit 1; }

IMAGE="ttl.sh/forge-$(uuidgen | tr 'A-Z' 'a-z'):4h"
echo "== build + push $IMAGE (linux/amd64 — Akash providers are x86_64)"
docker buildx build --platform linux/amd64 --load -q -f deploy/akash/Dockerfile -t "$IMAGE" .
docker push -q "$IMAGE"

SDL=$(mktemp /tmp/forge-sdl.XXXX.yaml)
sed -e "s|__IMAGE__|$IMAGE|" -e "s|__API_KEY__|$API_KEY|" -e "s|__TASK__|$TASK|" \
    deploy/akash/deploy.sdl.yaml > "$SDL"

echo "== create deployment"
$PS tx deployment create "$SDL" --from $KEY_NAME --deposit 500000uact -y >/dev/null
sleep 6
DSEQ=$($PS query deployment list --owner "$OWNER" --state active --node "$AKASH_NODE" -o json \
        | python3 -c "import json,sys; ds=json.load(sys.stdin)['deployments']; print(max(int(d['deployment']['id']['dseq']) for d in ds))")
echo "   dseq=$DSEQ"

echo "== wait for bid"
PROVIDER=""
for _ in $(seq 1 12); do
  PROVIDER=$($PS query market bid list --owner "$OWNER" --dseq "$DSEQ" --state open --node "$AKASH_NODE" -o json \
    | python3 -c "import json,sys; bs=json.load(sys.stdin).get('bids',[]); print(bs[0]['bid']['id']['provider'] if bs else '')" ) || true
  [ -n "$PROVIDER" ] && break
  sleep 5
done
[ -n "$PROVIDER" ] || { echo "no bids; closing"; $PS tx deployment close --dseq "$DSEQ" --from $KEY_NAME -y >/dev/null; exit 1; }
echo "   provider=$PROVIDER"

echo "== lease + manifest"
$PS tx market lease create --dseq "$DSEQ" --provider "$PROVIDER" --from $KEY_NAME -y >/dev/null
sleep 5
$PS send-manifest "$SDL" --dseq "$DSEQ" --provider "$PROVIDER" --from $KEY_NAME >/dev/null
rm -f "$SDL"

echo "== waiting for the run to finish (polling logs for FORGE_JSONL_END)"
OUT="runs/akash-$(date +%Y%m%d-%H%M%S).jsonl"
DONE=0
for i in $(seq 1 60); do
  sleep 15
  if [ "$i" = 4 ]; then  # one-time diagnostics once the image should be pulled
    $PS lease-status --dseq "$DSEQ" --provider "$PROVIDER" --from $KEY_NAME 2>&1 | head -20 || true
  fi
  LOGS=$($PS lease-logs --dseq "$DSEQ" --provider "$PROVIDER" --from $KEY_NAME --tail 10000 2>/dev/null \
         | python3 -c "import json,sys
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    try: print(json.loads(line).get('message',''))
    except Exception:
        # provider prefixes plain-text lines with '[lease][pod] ' — strip it
        brace = line.find('{')
        print(line[brace:] if brace >= 0 else line)") || continue
  if echo "$LOGS" | grep -q FORGE_JSONL_END; then
    echo "$LOGS" | sed -n '/FORGE_JSONL_BEGIN/,/FORGE_JSONL_END/p' | sed '1d;$d' > "$OUT"
    DONE=1
    break
  fi
done

echo "== teardown"
$PS tx deployment close --dseq "$DSEQ" --from $KEY_NAME -y >/dev/null

[ "$DONE" = 1 ] || { echo "run did not complete in time; no JSONL retrieved"; exit 1; }
echo "== retrieved $OUT ($(wc -l < "$OUT") events) — stats:"
uv run scripts/stats.py "$OUT"
