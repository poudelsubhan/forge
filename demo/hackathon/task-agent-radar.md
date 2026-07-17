Build an "agent-land radar" brief of what is moving right now. Fetch the
current Hacker News front page and extract the top 10 stories (title, points,
domain). Separately, search GitHub for the most recently pushed public
repositories matching "agent" and extract name, stars, and language for the
top 10. Cross-reference the two lists, rank the 3 hottest items overall by
momentum, and produce a short brief explaining each pick.

<!--
SPONSOR BEAT MAP (not part of the agent task)
- step "GitHub search": capability gap → make-or-buy → Zero.xyz search/API
  tool is the BUY candidate (Zero beat). Fallback synthesis: GitHub REST
  search API, unauthenticated.
- step "HN front page": synthesized scraper/API tool (BUILD path —
  hn.algolia.com API or HTML parse).
- step "rank by momentum": pure-python synthesized tool (no egress).
- Pomerium beat: tier0 denial on first fetch → promotions ratchet to tier1 →
  retry succeeds.
- Autonomy beat: both feeds are live and change minute-to-minute; headless.
- Expected: ≥2 syntheses + ≥1 acquisition, < 90s runtime.
-->
