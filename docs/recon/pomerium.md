# Recon 0.2 — Pomerium ✅

## Install (done)
```bash
brew install pomerium        # 0.33.0, native binary — no Docker needed (none on this machine)
```

## Working experiment (reproduced end-to-end)
Config: `deploy/pomerium/recon-config.yaml` — no IdP; gating is done by
toggling `allow_public_unauthenticated_access` per route.

```bash
python3 -m http.server 8900 &                       # upstream
pomerium -config deploy/pomerium/recon-config.yaml &
curl -H 'Host: fetch.localhost:8901' http://127.0.0.1:8901/   # → 200 (allow)
# edit config: allow_public_unauthenticated_access: true → false
# wait ~2-4s, NO restart:
curl -H 'Host: fetch.localhost:8901' http://127.0.0.1:8901/   # → 403 (deny)
```

## Reload mechanism (the load-bearing finding)
**Pomerium hot-reloads on config file change via file watch.** Log proof:
```
{"message":"config: updated config","checksum":"2ea8baf11421e113"}
{"message":"outbound client connection has changed meaningfully, reloading"}
```
Latency: under ~4s from file write to enforcement. No restart, no signal.
→ `trust.apply(tier)` = render config template + atomic write. Done.

## Tier design for Track B
- Run Pomerium as a local forward-gate on `:8901` with one route per
  capability class; agent tool egress goes to `http://<class>.localhost:8901`.
- tier0: all routes `allow_public_unauthenticated_access: false` (403 = the
  denial event). tier1: read-only routes flipped true. tier2: write routes too.
- Denials are observable: tool gets HTTP 403 with Pomerium error body — the
  loop sees the failure in the tool_result, which is the self-correct signal.
