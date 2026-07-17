# Recon 0.3 — Akash ✅ (full deploy cycle completed on sandbox-2, zero manual steps)

## The three traps (each cost a retry — do not rediscover these)
1. **Brew tap version is stale.** `akash-network/tap` installs provider-services
   v0.11.1; the sandbox-2 chain runs node v2.1.0 and rejects its deposits. Use
   the GitHub release binary **v0.16.0-a4** (installed at
   `~/bin/provider-services-0.16`). Also run `brew trust akash-network/tap`
   before brew install (untrusted-tap guard).
2. **Deposits are in `uact`, not `uakt`.** Chain v2 has the BME (Burn-Mint
   Equilibrium) token migration. The faucet pays uakt; deployment deposits and
   SDL pricing must be denominated `uact`. Convert first:
   `tx bme mint-act 20000000uakt` (burns AKT → mints ACT, ~0.55 uact/uakt).
   The error for getting this wrong is an unhelpful "Deposit invalid".
3. **SDL pricing denom must match deposit denom** (`uact`), else the CLI
   client-side errors "Mismatched denominations".

## Network + wallet (done)
```bash
export AKASH_KEYRING_BACKEND=test AKASH_CHAIN_ID=sandbox-2 \
       AKASH_NODE=https://rpc.sandbox-2.aksh.pw:443 \
       AKASH_GAS=auto AKASH_GAS_ADJUSTMENT=1.5 AKASH_GAS_PRICES=0.025uakt
provider-services keys add forge-hackathon --keyring-backend test
# addr: akash1kly8kaeay6ks0f7tar9dlduulmkqnss0qzaqv4  (test keyring, throwaway)
# faucet (autonomous, 25 AKT/claim, repeatable):
curl -X POST https://faucet.sandbox-2.aksh.pw/faucet -d 'address=<addr>'
```

## Verified deploy flow (wall-clock ~90s total)
```bash
PS=~/bin/provider-services-0.16
$PS tx cert generate client --from forge-hackathon -y
$PS tx cert publish client --from forge-hackathon -y
$PS tx bme mint-act 20000000uakt --from forge-hackathon -y
$PS tx deployment create deploy/akash/recon.sdl.yaml \
    --from forge-hackathon --deposit 500000uact -y        # dseq 4391189
$PS query market bid list --owner <addr> --dseq <dseq>    # bid in <10s
$PS tx market lease create --dseq <dseq> --provider akash1rk090...srxh -y
$PS send-manifest deploy/akash/recon.sdl.yaml --dseq <dseq> --provider ... # PASS
$PS lease-status ...   # ready_replicas: 1, uri: rulpn3ehs9ckj2ljgoo47ihroo.
                       #   ingress.provider-01.sandbox-2.aksh.pw
curl http://<uri>/     # "Welcome to nginx!" — served from Akash
$PS tx deployment close --dseq <dseq> --from forge-hackathon -y   # torn down
```
Provider that bid: `akash1rk090a6mq9gvm0h6ljf8kz8mrxglwwxsk4srxh` at
~0.73 uact/block. Balance remaining after full cycle: ~10 ACT + ~30 AKT.

## Notes for Track C
- Sandbox providers pull public images (Docker Hub). Forge's image must be
  pushed somewhere public (ghcr.io or Docker Hub) before deploy.
- `mainnet` deploy for demo day only needs env swap (chain id, node, real AKT
  or Console credits) — flow is identical.
- No local Docker on this machine: build the image in CI (GitHub Actions) or
  demo the sandbox flow; `docker run` local-fallback requires installing
  Docker Desktop/OrbStack (user decision).
