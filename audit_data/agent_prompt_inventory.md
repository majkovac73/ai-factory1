# Agent Prompt & Reasoning Inventory (DEEP AUDIT V2, 2026-07-21)

Scope: every agent in `app/agents/registry.py` (20 registered) PLUS the agents the
live autonomy→listing path actually uses that are **not** registered. Each entry:
prompt source, abstain/fail path, downstream schema enforcement, QA/critic coverage.

## Registry vs. reality (finding A0)
`registry.py` registers 20 agents (planner, executor, generator, critic, fixer, qa,
research, intelligence, design, optimization, planning, copywriter, brand,
visual_director, consistency, fact_check, completeness, product_generator,
seo_generator, listing_generator). **The live autonomy pipeline's primary driver,
`TrendResearchAgent`, is NOT in the registry**, nor are `ProductViabilityCritic`,
`ProductTypeSelectorAgent`, or `ProductScoreService`. Several registered agents
(`planner/executor/generator/critic/fixer/qa` from `app/core/agents/`) appear to be
a legacy generic-task architecture not on the product path — **dead-code risk;
auditing "all registered agents" ≠ auditing what runs.** Confidence: High (grepped
the live call graph from `autonomy_worker → TrendResearchAgent → ResearchAgent +
IntelligenceAgent → ProductScoreService → pod_design/product_image → seo/listing
generators → ContentQualityService`).

---

## LIVE-PATH AGENTS (the ones that shape shipped product)

### 1. TrendResearchAgent (`app/agents/trend_research_agent.py`) — the driver
- **Prompt**: builds a concept prompt (`_build_concept_prompt`) constraining output
  to one of a fixed format list, injecting real trend data, insights, margin
  guidance, seasonal block, dedup note. Output JSON: product_name, product_format,
  page_count, description, target_audience, confidence, text_led, display_text.
- **Abstain path**: STRONG. `run()` hard-aborts the whole cycle on `TrendDataFetchError`
  (no LLM-guess fallback) — verified. Returns None if no concept passes.
- **Schema enforcement**: JSON is schema/dedup validated; bad JSON consumes a cheap
  `raw` budget, not the scored budget.
- **QA coverage**: concepts scored by ProductScoreService (shadow) + ProductViabilityCritic.
- **Gap**: the research step's LLM call is where the **402 halts the entire factory**
  with only a `logger.error` — no abstain-to-alert. (See main doc P0 #1.)

### 2. ResearchAgent (`app/agents/market_intelligence/research.py`)
- Produces market research text from a topic + real trend data.
- **Abstain**: exceptions bubble to TrendResearchAgent which aborts the cycle (good).
- **Gap**: free-text output, not schema-validated at this stage; feeds Intelligence.

### 3. IntelligenceAgent (`app/agents/market_intelligence/intelligence.py`)
- Synthesizes research → `{summary, opportunities[], threats[], recommendations[], confidence}`.
- **Abstain/fallback (finding A3)**: on JSON-parse failure returns
  `{"opportunities": [], ... "confidence":"low"}` (lines 61-67). Not fabrication, but
  a malformed LLM reply **silently yields zero opportunities** → the cycle dies with
  no product and no distinct signal. This is a soft silent-failure: it degrades to
  "nothing" quietly rather than surfacing "intelligence output was unparseable."
  Confidence: High (read source).
- **Schema enforcement**: `opportunities` is `.get("opportunities", [])` downstream —
  hoped for, not validated (no length/shape check). An LLM returning opportunities as
  a dict or malformed strings would pass through.

### 4. ProductViabilityCritic (`app/agents/product_viability_critic.py`)
- Scores a concept 1-10 on "would a real stranger buy THIS?"; pass/fail derived in
  **code** from `settings.VIABILITY_CRITIC_MIN_SCORE` (=6), NOT the model's own
  `passed` field (good, deliberate — comment lines 25-29).
- **Abstain**: explicit — trademark hits scored 1; out-of-window seasonal scored 1-2.
- **QA coverage**: this IS a critic. Independent of ProductScoreService.
- **Gap**: single-model judgment; no second critic cross-check at this layer.

### 5. ProductScoreService (`app/services/product_score_service.py`) — the gate
- Composite 0-100 = deterministic(0-40) + 6×judge(0-60). Floors-based pass.
- **Finding A5 (CONFIRMED, full population)**: 83/83 scored concepts FAILED
  (mean 62.9, max 71, floor is 90). 0% pass across the entire history. In shadow
  mode so it doesn't block, but as configured it would reject everything.
- **Feeds on** EtsyMarketService for the market/competition axis — which **403s 100%**
  (header bug, see main doc), so that evidence axis is dead weight.

### 6. pod_design_agent / product_image_agent (delivery + listing images)
- Design prompt now includes the #19 no-IP instruction. Delivery assets are the
  #1 block source: **71/90 blocks are generation/readback failures here.**
- **QA coverage**: ContentQualityService (vision) reviews delivery; consistency gate
  checks marketing vs. delivery. Both fire — but 8 content-QA + 9 consistency blocks
  show they reject a lot AFTER paying for generation.

### 7. SEOGeneratorAgent (`app/agents/etsy/seo_generator.py`)
- Prompt asks for title 20-70 chars, ≥5 keywords, sections. Output validated by
  `SchemaValidator.validate_seo`.
- **Gap (confirmed live)**: keywords → tags conversion historically truncated
  mid-word and under-filled (fixed forward in #7, but 45 live listings still carry
  3-13 tags, several under-filled). No abstain path — always returns some copy.

### 8. ListingGeneratorAgent (`app/agents/etsy/listing_generator.py`)
- Post-#7: `_to_valid_tag` whole-word ≤20, `validate_tags`, filler to 13. Good.

### 9. ContentQualityService (`app/services/content_quality_service.py`)
- Vision-model review of the actual delivery asset. Real teeth: rejected pre-colored
  coloring pages (5.5–16.3% color), garbled text ("Black the School"), duplicated
  planner entries. **This is working** (adversarial check: it catches real garbage).
- **Gap**: uses gpt-4o-mini (cheap vision) as both generator-QA and PDF-QA; a
  stronger reader would catch more, but cost-controlled.

---

## REGISTERED-BUT-OFF-PATH AGENTS (likely legacy / low coverage)
planner, executor, generator, critic, fixer, qa (core.agents), design, optimization,
planning, copywriter, brand, visual_director, consistency, fact_check, completeness,
product_generator. These are registered and instantiable via `get_agent()` but do
not appear in the live autonomy→listing call graph. **Zero evidence any of them run
in production today** (agent_executions table had 1 row total until the #15 fix; no
per-agent execution history exists to prove otherwise). Recommendation: either wire
them into the path with QA coverage or delete them — they are audit surface with no
runtime proof, and `get_agent()` is reachable (though key-gated) via the API.

## Agents with ZERO QA/critic coverage on their output
- ResearchAgent (free-text, unvalidated until Intelligence consumes it).
- IntelligenceAgent (`opportunities` shape unchecked downstream).
- Every registered off-path agent (no runtime, no critic).
