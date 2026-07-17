# Gate 0 decisions — 2026-07-17

## In scope (all three recons passed live)
- **Zero.xyz — GO.** Unattended agent auth works; live search returns 27
  x402 tools for the demo gap; execution pipeline verified to the payment
  step. *Pending user step:* fund wallet ~$1 USDC (`zero wallet fund`,
  browser-only) before the Phase 2 end-to-end run.
- **Pomerium — GO.** Native binary (no Docker). Allow→deny flip enforced
  ~2-4s after config file write via built-in file-watch hot reload. Trust
  ratchet mechanism = template render + file write. IdP-free design using
  per-route `allow_public_unauthenticated_access`.
- **Akash — GO.** Full cycle proven on sandbox-2: faucet → BME uakt→uact
  conversion → deploy → provider bid → lease → manifest → live HTTP → close.
  Zero manual steps. Use release binary v0.16.0-a4, NOT the brew-tap v0.11.1.

## Scope adjustments vs spec
- Track C local-Docker fallback is unavailable (no Docker on machine).
  Fallback instead: run Forge headless *locally* while Pomerium+Zero beats
  play, with the Akash deploy shown from the recon transcript. Building the
  Forge image needs CI or a machine with Docker — decide at Track C start.
- Demo task primary: `demo/hackathon/task-market-brief.md` (crypto market
  brief — richest Zero.xyz tool coverage). Backup: `task-agent-radar.md`.

## Environment facts
- No Docker, no node (now installed via brew), pomerium 0.33.0, zero 1.26.0,
  provider-services v0.16.0-a4 at `~/bin/provider-services-0.16`.
- Akash wallet: test keyring `forge-hackathon`,
  `akash1kly8kaeay6ks0f7tar9dlduulmkqnss0qzaqv4` (sandbox only, throwaway).
- Zero agent wallet: `0xA38a50a3de607b574f6a919834C815d7b664A12f` (Base).
