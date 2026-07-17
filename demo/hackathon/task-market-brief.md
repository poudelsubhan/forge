Produce a live crypto market brief. Fetch the current BTC and ETH prices in USD
and their 24h percentage changes, determine whether ETH is outperforming BTC
right now, fetch the current Crypto Fear & Greed index, and produce a
one-paragraph market brief that combines all three signals and ends with a
single-word stance: accumulate, hold, or avoid.

<!--
SPONSOR BEAT MAP (not part of the agent task)
- step "fetch prices": capability gap → make-or-buy → Zero.xyz x402 market-data
  tool is the BUY path (Zero beat). Fallback synthesis: CoinGecko simple/price.
- step "fetch fear & greed": second external fetch → alternative.me API →
  synthesized tool (BUILD path), so transcript shows both paths.
- step "24h change comparison": pure-python synthesized tool (no egress).
- Pomerium beat: tier0 denies all non-LLM egress → first fetch attempt is
  DENIED; agent does the pure-python planning/synthesis work, earns promotions,
  tier1 unlocks read-only egress → retry succeeds (denial→ratchet→retry arc).
- Autonomy beat: prices + index are real-time; run is headless, zero input.
- Expected: ≥2 syntheses + ≥1 acquisition, < 90s runtime.
-->
