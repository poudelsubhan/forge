Produce a live crypto market brief. Do these in order:
1. Fetch the current Crypto Fear & Greed index (api.alternative.me/fng/).
2. Fetch current BTC and ETH prices in USD and their 24h percentage changes
   (CoinGecko public API).
3. Sanity-check step 2: obtain the current BTC spot price in USD from one
   INDEPENDENT market-data source (a paid marketplace price feed is
   acceptable) and report the deviation between the two BTC quotes.
4. Determine whether ETH is outperforming BTC right now, then produce a
   one-paragraph market brief combining all signals, ending with a
   single-word stance: accumulate, hold, or avoid.

<!--
SPONSOR BEAT MAP (not part of the agent task)
- step 1 (F&G): first gap → buy attempt DENIED by Pomerium at tier0 (the
  denial beat) → built via synthesis → promotion earns tier1.
- step 2 (prices): buy attempt allowed at tier1; marketplace feeds lack
  eth/24h so the adversarial gate rejects the candidate (a caught bad
  purchase — may demote a tier, which the loop re-earns) → built.
- step 3 (BTC cross-check): contract matches the Coinbase x402 feed exactly
  → BOUGHT via Zero.xyz, real x402 payment, promoted through the same gate.
- step 4: pure reasoning; convergence halt via final_answer.
- Autonomy: all data live; denial→earn→(revoke→re-earn)→buy, zero manual steps.
-->
