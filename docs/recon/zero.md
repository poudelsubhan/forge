# Recon 0.1 — Zero.xyz ✅ (one user step pending: wallet funding)

## Install (done)
```bash
brew install node            # env had no node
npm i -g @zeroxyz/cli        # zero 1.26.0
zero init                    # managed runner at ~/.zero/runtime/bin/zero
```

## Auth (done, fully unattended)
```bash
zero auth agent register --json
# → {"status":"ok","registrationId":"agent_reg_01KXRQ7YP4NGBGGQ0T3YEJFGBH",
#    "userId":"usr_nd7tyiD3AvMg4Rng7S5cw",
#    "walletAddress":"0xA38a50a3de607b574f6a919834C815d7b664A12f", ...}
zero auth whoami --json      # verifies
```
No browser needed for agent identity. It provisions an x402 wallet on Base.

## Discovery (done, live)
```bash
zero search "bitcoin price current USD" --json
# → 27 capabilities. Top hit:
#   token z_aU4WAA.1 — "Coinbase BTC Spot Price (USD)"
#   GET https://proxy.suverse.io/v1/data/coinbase-btc-spot
#   $0.001 USDC/call, protocol x402, availability healthy
```
Output is clean JSON: `capabilities[]` with `token`, `url`, `method`, `cost`,
`pricing.summary`, `rating`, `availabilityStatus`. Search is free/read-only.

## Execution (verified to the payment step)
```bash
zero fetch --capability z_aU4WAA.1
# → "Calling https://proxy.suverse.io/v1/data/coinbase-btc-spot...
#    Insufficient funds: wallet has 0 USDC on Base, needs 0.05."
```
The whole pipeline (resolve → call → x402 payment attempt) works; it fails
only on balance. **Minimum balance 0.05 USDC; calls are ~$0.001.**

## USER STEP REQUIRED
`zero wallet fund --start` is browser-only. Fund ~$1 USDC before Phase 2's
end-to-end run. Everything else in Track A (discover/wrap/promote) can be
built and tested against `zero search` (free) in the meantime.

## Integration notes for Track A (`forge/acquire.py`)
- Shell out to `zero search "<gap>" --json`; parse `capabilities[]`.
- Wrapper tool body: `subprocess.run(["zero","fetch","--capability",token])`.
- `zero fetch` prints human text, not JSON — parse stdout after the
  "Calling …" line, or hit the capability URL pattern via the CLI only.
- Rank candidates by `rating.successRate`, `availabilityStatus == "healthy"`,
  then lowest cost.
