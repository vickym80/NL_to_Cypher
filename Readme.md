# NL2Cypher POC — Implementation Doc

**Status: implemented and live-tested against the running Neo4j instance and OpenAI provider.** This is the follow-through on `docs/nl2cypher_poc_plan.md` and `docs/neo4j_native_graphrag_text2cypher.md` (Phase 0 planning) into a working Phase 1 MVP, in the new `NL2Cypher/` folder — deliberately isolated from `graphrag_service/`, `rca_agent/`, and every other existing module. Every code sample, test result, and API response quoted below is real output from actually running this code, captured during implementation — nothing is a mocked example. The LLM factory also supports Azure OpenAI via `LLM_PROVIDER=azure_openai`.

## 1. Why do this at all — benefits over the existing template-based Cypher queries

The existing pipeline's Cypher lives in `graphrag_service/cypher_templates.py` as fixed, parameterized query strings (`build_causal_path_query`, `DRIVER_EVIDENCE_QUERY`, `EVIDENCE_SUMMARY_QUERY`, `DRIVER_TO_FEATURE_QUERY`). Each answers exactly one question shape, with placeholders for a handful of parameters. That's the right design for the RCA pipeline (see §7 for why it should stay that way there) — but it has real costs as a *general* graph-querying interface:

| Template-based Cypher | NL2Cypher (Text2CypherRetriever) |
|---|---|
| Answers only the question shapes a developer already anticipated and wrote a template for | Answers any question expressible in Cypher over the given schema, with zero new code per question |
| A new question shape means: a developer writes new Cypher, tests it, ships a backend release | An analyst types a new question and gets an answer immediately — no deploy cycle |
| The Cypher is invisible to non-developers — reading `cypher_templates.py` is the only way to know "what can I ask" | The interface *is* plain English — self-documenting, no need to know the graph model at all |
| Every template hardcodes label/relationship names inline; an ontology rename means hunting down and editing every affected template | The schema context (`NL2Cypher/schema_context.py`) is one shared source of truth; update it once and every future question benefits |
| Good for internal debugging/exploration only if you already know Cypher | Genuinely useful as a live schema/data QA tool during ontology development — ask a question, see immediately whether new nodes/relationships behave as expected |

**The honest caveat, and why this is additive, not a wholesale replacement:** the RCA pipeline's own templates (`build_causal_path_query` in particular) aren't just "a Cypher query that happens to answer a question" — their output shape (exact field names like `avg_confidence_score`, `hops`, `edges[].confidence`) is a load-bearing contract that `path_scorer.py`'s 6-signal formula and `dowhy_estimator.py`'s ATE estimation directly consume. An LLM asked the same underlying question in natural language is not guaranteed to phrase the returned columns identically every time, and even a single differently-named or missing field would silently break the scorer or estimator. Swapping those specific templates for LLM-generated Cypher would trade deterministic, tested structure for non-determinism, in the one place the pipeline can least afford it. So: **use NL2Cypher as a new, standalone ad hoc query surface (this doc), and leave the RCA-critical templates exactly as they are.** If, after this POC proves out, there turn out to be non-critical/debug-only templates elsewhere worth retiring in favor of NL2Cypher, that's a case-by-case call for later — not a default outcome of building this.

## 2. What was actually built

```
NL2Cypher/
  __init__.py
  schema_context.py   — curated Neo4j schema strings (ontology + instance data)
  examples.py          — 10 hand-written few-shot NL→Cypher examples
  llm_provider.py       — builds a neo4j_graphrag-compatible LLM from existing .env settings
  engine.py             — NL2CypherEngine: wraps Text2CypherRetriever + the live Neo4j driver
  results_writer.py     — appends each run (NL query + GraphRAG context + Cypher + result) to a txt log
  schema_diff.py         — diffs the curated fallback schema against a live get_schema() fetch
  cli.py                — `python -m NL2Cypher.cli "<question>"` for quick manual testing
  service.py            — standalone FastAPI app (port 9060), separate from graphrag_service
  tests/
    test_nl2cypher.py   — 10-question integration test suite (pytest -m neo4j)
start_nl2cypher.sh       — start script for the standalone service, mirrors start_backend.sh
requirements/base.txt    — added `neo4j-graphrag>=1.18.0` (optional block, like the existing Databricks one)
```

Nothing in `graphrag_service/`, `rca_agent/`, `ui/`, or any other existing module was touched.

## 3. Why `neo4j-graphrag` (not a hand-rolled prompt) — confirmed capabilities used

As scoped in `docs/neo4j_native_graphrag_text2cypher.md`, `neo4j_graphrag.retrievers.Text2CypherRetriever` (package version confirmed live: **1.18.0**, released 2026-06-24) provides, out of the box:

- **Schema-aware prompting** — `neo4j_schema` is injected into the LLM prompt verbatim. **Superseded:** the `schema_source="curated"` vs. `"live"` toggle described in §4e below (and the hand-curated `ONTOLOGY_SCHEMA`/`INSTANCE_DATA_SCHEMA` strings it referred to) no longer exists — `schema_context.py` now always derives the schema live, including low-cardinality property *values* (e.g. the valid `KPI.id` set), renders it into the same compact shape, and caches it with a TTL (`NL2CypherEngine.refresh_schema()` forces an immediate re-fetch). See the docstring at the top of `schema_context.py` for the current design and rationale; §4e/§4f below are kept as historical record of the bugs that motivated the change, not as current behavior.
- **Few-shot examples** — `examples: list[str]`, formatted `"USER INPUT: '...' QUERY: ..."`, joined into the prompt. Ten examples were hand-written against the *real* schema (verified correct by actually running each one — see §4) and deliberately include the ontology's distinctive variable-length causal pattern (`[:CAUSES|CONTRIBUTES_TO*1..5]`), since that's the one pattern a generic few-shot set wouldn't demonstrate.
- **Built-in read-only enforcement** — confirmed live in §5 below: the retriever inspects the generated query's type and refuses to execute anything that isn't read-only, raising `Text2CypherRetrievalError`.
- **Custom result formatting** — `result_formatter=_record_to_item` in `engine.py` converts each `neo4j.Record` to a plain dict (`record.data()`) instead of the library's default `str(record)`, so results are directly JSON-serializable for the API/UI.

## 4. Real captured runs

### 4a. Schema/example accuracy — verified against live YAML + Neo4j, not assumed

Before writing `schema_context.py`, the actual ontology and mock-data structure was queried directly from `ontology/nodes.yaml`, `ontology/edges.yaml`, and `ontology/mock_data/*.yaml` — for example, confirming the real `EVIDENCES` relationship only occurs as three specific label pairs in the loaded data:

```
('DemandForecastSnapshot', 'CausalDriver'): 33
('PurchaseOrder', 'CausalDriver'): 20
('InventorySnapshot', 'IntermediateFactor'): 20
```

An earlier draft of the schema string (based on a prior planning doc's assumption) had this wrong — it listed all three instance-record types as evidencing both `CausalDriver` and `IntermediateFactor`. Verifying against the actual data before writing the schema constant caught this; `schema_context.py` reflects the corrected, confirmed set.

### 4b. CLI runs — real questions, real answers, first try

```
$ python -m NL2Cypher.cli "Which suppliers ship to DC_EAST?"
Cypher:   MATCH (s:Supplier)-[:SHIPS_TO]->(dc:DistributionCenter {id: 'DC_EAST'}) RETURN s.id, s.name, s.tier
Rows (2): [{"s.id": "Supplier_A", ...}, {"s.id": "Supplier_C", ...}]
```

```
$ python -m NL2Cypher.cli "How many purchase orders have a status of delivered?"
Cypher:   MATCH (po:PurchaseOrder {status: 'delivered'}) RETURN count(po) AS delivered_purchase_orders_count
Rows (1): [{"delivered_purchase_orders_count": 53}]
```

```
$ python -m NL2Cypher.cli "What causal drivers can eventually affect fill_rate?"
# Correctly generalized the *taught* pattern (stockout_rate in the few-shot example) to a
# different KPI (fill_rate) never mentioned in any example, returning 8 upstream drivers
# including multi-hop ones like port_congestion and component_shortage.
```

None of these three questions are copies of the few-shot examples in `examples.py` — they're paraphrases/generalizations, which is the actual thing being tested (memorizing the exact example text would prove nothing).

### 4c. Full test suite — 10/10 passed, first run, ~22 seconds

```
$ pytest -m neo4j NL2Cypher/tests/test_nl2cypher.py -v
...
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[List all KPIs and their directionality.] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[Which suppliers have tier 1?] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[How many purchase orders have a status of delivered?] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[What is the average actual lead time for purchase orders from Supplier_A?] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[Which causal drivers directly contribute to forecast_error?] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[What causal drivers can eventually affect fill_rate?] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[List interventions owned by Procurement.] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[Which shipments were not on-time-in-full?] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[Show me distribution centers in the East region.] PASSED
NL2Cypher/tests/test_nl2cypher.py::test_generates_and_executes_read_only_cypher[Which supplier supplies SKU_1002?] PASSED

============================= 10 passed in 21.83s ==============================
```

Each of these 10 questions is intentionally *not* one of the 10 few-shot examples — the point of the suite is generalization to new phrasing, not example recall. This clears the Phase 1 exit bar set in `docs/nl2cypher_poc_plan.md` §7 (with the caveat that 10 questions is a smoke-test-sized sample, not the fuller 15-20 question rigor described there — see §8 below).

### 4d. Standalone FastAPI service — real HTTP round trip

```bash
$ ./start_nl2cypher.sh
Starting NL2Cypher POC on http://localhost:9060

$ curl -X POST http://localhost:9060/nl2cypher/query \
    -d '{"question": "Which interventions require approval?"}'
{
  "cypher": "MATCH (i:Intervention {approval_required: true}) RETURN i.id, i.name, i.action_owner, i.lead_time_days",
  "rows": [
    {"i.id": "activate_alternate_supplier", "i.name": "Activate Alternate Supplier", "i.action_owner": "Procurement", "i.lead_time_days": 3},
    {"i.id": "air_freight_escalation", "i.name": "Air Freight Escalation", "i.action_owner": "Logistics", "i.lead_time_days": 1},
    {"i.id": "increase_safety_stock", "i.name": "Increase Safety Stock", "i.action_owner": "Supply Planning", "i.lead_time_days": 5},
    {"i.id": "port_diversion", "i.name": "Port Diversion", "i.action_owner": "Logistics", "i.lead_time_days": 2}
  ],
  "error": null
}
```

## 4e. Live schema introspection — confirmed working once APOC was installed, and it caught a real gap

> **Superseded** — this section documents the bugs (curated-schema drift, noisy live `get_schema()` output) that motivated replacing the curated/live toggle entirely. `schema_context.py` now always derives schema live via `neo4j_graphrag.schema.get_structured_schema(..., is_enhanced=True)`, renders it into a compact custom format (filtering out generic base-label noise like `GraphNode`/`InstanceRecord`), and includes property-value enums. See that file's docstring for the current design.

`neo4j_graphrag.schema.get_schema(driver)` is the library's built-in live-introspection function (`Text2CypherRetriever` calls it automatically whenever `neo4j_schema` isn't passed in). It depends on the **APOC** plugin (`apoc.meta.data`, `apoc.schema.nodes`) with no pure-Cypher fallback. The first attempt against this environment's Neo4j Desktop instance failed:

```
neo4j.exceptions.ClientError: {neo4j_code: Neo.ClientError.Procedure.ProcedureNotFound}
{message: There is no procedure with the name `apoc.meta.data` registered...}
```

After installing/enabling APOC in Neo4j Desktop and restarting the DBMS, the same call succeeded and returned a **14,814-character** live schema string — versus the curated `ONTOLOGY_SCHEMA + INSTANCE_DATA_SCHEMA`'s much smaller footprint. That size gap confirms the original reasoning in `docs/neo4j_native_graphrag_text2cypher.md` §5 for not defaulting to live auto-fetch.

But comparing the two directly (`NL2Cypher/schema_diff.py`, new in this pass) surfaced something more important than prompt size: **the curated schema was missing real, live node types.** `ontology/nodes.yaml`/`edges.yaml` — the two files the original curated schema was built from — don't include two supplementary ontology files that *are* loaded into the live graph: `ontology/risk_taxonomy.yaml` (`GeopoliticalRisk`, `SupplyRisk`, `DemandRisk`, `LogisticsRisk`, `ComplianceRequirement`, all also carrying a shared `RiskNode` base label) and `ontology/logistics_network.yaml` (`Carrier`, `Port`, `TransportLane`). Before this fix, any question like *"which ports have a high risk level?"* would have silently failed or hallucinated, because the LLM's prompt never mentioned `Port` existed at all.

**Fixed** in `schema_context.py`: added all 8 missing labels (verified against the actual YAML property definitions, not guessed), plus two notes — these risk/logistics nodes are confirmed **isolated** (no relationships to the rest of the graph; `TransportLane.origin_port`/`destination_dc` are plain string properties despite the names, not edges), and `RiskNode` is a shared base label across all 5 risk subtypes (confirmed by directly querying `MATCH (n:RiskNode) RETURN labels(n)` — all 5 subtypes carry it, including `ComplianceRequirement`). Two new few-shot examples were added to `examples.py` covering `Port`/`TransportLane`. Re-verified live:

```
$ python -m NL2Cypher.cli "List every risk node and its category."
# ... 4 rows: geopolitical_risk, logistics_disruption_risk, supply_concentration_risk, ...

$ python -m NL2Cypher.cli "Which ports have a risk level of medium or high?"
Rows (2): [{"p.id": "port_los_angeles", ...}, {"p.id": "port_shanghai", ...}]
```

Both work correctly now; neither would have before this fix. The full 10-question regression suite (§4c) still passes 10/10 after the change.

**New tool**: `NL2Cypher/schema_diff.py` (`python -m NL2Cypher.schema_diff`) — fetches the live schema via `get_schema()` and diffs its label set against the curated schema, flagging labels that are live but missing from the curated string (or vice versa, for stale entries). Current output after the fix: only `CausalMediator` and `RiskNode` are flagged as "missing," both intentionally — they're documented in `schema_context.py` as coexisting/base labels already covered by `IntermediateFactor`/the 5 risk subtypes respectively, not real gaps.

**Correction from an earlier pass of this doc**: this section originally treated live schema fetch as a diagnostic tool only, with `engine.py` still always using the curated string at request time. That undersold what was actually asked for — the point of confirming `get_schema()` works was to let the *engine itself* read the schema from Neo4j, not just to validate the curated one offline. Fixed: `NL2CypherEngine.__init__` now takes `schema_source: Literal["curated", "live"] = "live"` — **live is now the default**. On every engine startup it calls `get_schema(driver)` directly and hands that real, current schema to `Text2CypherRetriever`, so the LLM's context is never stale relative to the actual graph (no more silent gaps like the risk/logistics one above). `schema_source="curated"` remains available (e.g. for environments without APOC, or to force a smaller prompt) — pass it explicitly, or use `--curated-schema` on the CLI. Verified: `engine.schema_context` is 14,814 characters when `schema_source="live"` vs. 6,394 for `"curated"`; the full 10-question regression suite still passes 10/10 with the live default.

## 4f. Bug report follow-up: "Why did fill rate drop for SKU_1002 at DC_EAST in the last 7 days?" returned nothing

A user report of "data is not getting retrieved" for this exact question led to finding two real, distinct bugs and one non-bug (a genuine data-coverage gap) — worth separating clearly since only two of the three were fixable:

**Bug 1 (fixed) — STRING vs. native DATE comparison.** The first generated query was:
```cypher
MATCH (i:InventorySnapshot {sku_id: 'SKU_1002', dc_id: 'DC_EAST'})
WHERE i.date >= date() - duration('P7D')
...
```
Every date-like property in the mock instance data (`date`, `order_date`, `promised_arrival_date`, etc.) is stored as a **STRING** (`'2026-06-29'`), confirmed via the live schema (§4e). Comparing a STRING directly to Neo4j's native `date()` type doesn't raise an error — it silently evaluates to `null` for every row, so the filter matches nothing and 0 rows come back with no error surfaced anywhere. None of the original 10 few-shot examples demonstrated relative-date filtering, so the LLM had no correct pattern to imitate. **Fix**: added two new examples to `examples.py` teaching `date(i.date) >= date() - duration('P<N>D')` (casting the string first). Confirmed live: the same style of query for `InventorySnapshot`/`DemandForecastSnapshot` now returns real rows.

**Bug 2 (fixed) — KPI name treated as a property instead of a filter value.** Once bug 1 was fixed, the next attempt queried `e.fill_rate` as if `fill_rate` were a property name on a generic `InstanceRecord`. It isn't — `fill_rate`/`stockout_rate`/`otifd`/etc. are **values of `KPIObservation.kpi_id`**; the actual number is always in a generic `value` property. **Fix**: added a `KPIObservation {sku_id, dc_id, kpi_id: '<kpi>'}` few-shot example to `examples.py`. Confirmed live — "How did stockout_rate change for SKU_1001 at DC_EAST over the last 30 days?" now correctly generates and runs `MATCH (o:KPIObservation {sku_id:'SKU_1001', dc_id:'DC_EAST', kpi_id:'stockout_rate'}) WHERE date(o.date) >= date() - duration('P30D') ...` and returns 5 real dated observations.

**Not a bug — the exact SKU/KPI/DC combination in the original question has no data.** `KPIObservation` mock data only covers **6** total `(kpi_id, sku_id, dc_id)` combinations, not the full cross-product of 6 KPIs × 2 SKUs × 2 DCs:

| kpi_id | sku_id | dc_id | observations |
|---|---|---|---|
| stockout_rate | SKU_1001 | DC_EAST | 16 |
| fill_rate | SKU_1001 | DC_WEST | 17 |
| otifd | SKU_1002 | DC_EAST | 17 |
| days_of_supply | SKU_1001 | DC_EAST | 17 |
| perfect_order_rate | SKU_1001 | DC_WEST | 17 |
| supplier_otd | SKU_1002 | DC_EAST | 16 |

`fill_rate` × `SKU_1002` × `DC_EAST` — the exact combination asked about — simply isn't one of the 6 rows above. With both bugs fixed, the correctly-generated query for that combination genuinely returns 0 rows, because there's genuinely nothing to return. Rephrasing to a combination that *is* covered (e.g. `otifd` for `SKU_1002`/`DC_EAST`, or `stockout_rate` for `SKU_1001`/`DC_EAST`) returns real data.

**A third, softer finding — phrasing affects reliability.** Rephrasing the same fixed question as "**Why** did fill rate drop..." (causal framing) versus "**How did** fill rate change..." (factual framing) produced different Cypher on otherwise-identical inputs — the "why" phrasing once led the LLM to attempt a hallucinated `(InstanceRecord)-[:EVIDENCES]->(KPI)` traversal (no such edge exists; `EVIDENCES` only ever targets `CausalDriver`/`IntermediateFactor`, never `KPI` directly) instead of the correct `KPIObservation` lookup. This points at a scope boundary rather than a bug to keep chasing: **"why did X happen" is a causal-investigation question, which is exactly what the RCA pipeline (`/rca` page, `POST /v1/rca/query`, see `docs/rca_walkthrough_sku1002_stockout.md`) is purpose-built and tested for** — entity resolution, causal path retrieval, DoWhy estimation, ranked root causes. NL2Cypher is a general ad hoc *retrieval* tool (§1); it can answer "what/which/how many/show me/how did X change," but "why" questions are better routed to the RCA flow, which doesn't share this KPI/SKU/DC data-coverage gap since its causal-estimation feature panel is synthetically generated per-request for any SKU/DC pair rather than drawn from these 6 fixed pre-loaded observation rows.

## 4g. Batch audit: 10 realistic "why is KPI X off for SKU/DC Y" questions

Ran 10 RCA-style questions (the kind a user would naturally ask, all phrased as "why"/"what's causing/driving") through the live engine to see how NL2Cypher holds up beyond hand-picked examples. Results split into four distinct categories — only one was actually a prompting bug fixable by better examples:

| Outcome | Count | Example |
|---|---|---|
| Real, relevant data returned | 3/10 | "days of supply low for SKU_1001/DC_WEST" → 7 real `InventorySnapshot` rows below safety stock |
| Non-empty but hollow — generic KPI-level driver list, not per-instance attribution | 3/10 | "stockout rate increasing for SKU_1001/DC_EAST" → returns generic `CausalDriver` nodes with `null` evidence fields |
| Genuine data-coverage gap (correct query, 0 rows because the data isn't there) | 2/10 | "fill rate drop for SKU_1002/DC_EAST" (§4f); "stockout rate spike for SKU_1001/DC_WEST" (`stockout_rate` is only populated for SKU_1001/**DC_EAST**, not DC_WEST) |
| Fixable hallucination bugs | 2/10 | wrong KPI id `'otif'` (real: `otifd`); wrong `evidence_type: 'OTIF'` (real values: only `forecast_bias`/`inventory_shortage`/`supplier_delay`); `EVIDENCES` edges hallucinated as pointing directly at a `KPI` node (they never do) |

**Fixed** (added to `examples.py`): the real 6-value `KPI.id` enum, the real 3-value `evidence_type` enum, and an explicit "`EVIDENCES` never targets `KPI`" rule. Re-running the stockout-rate question after the fix now correctly targets `KPIObservation` with proper date casting — its remaining 0 rows is a genuine data gap (confirmed: no `stockout_rate` observations exist for SKU_1001/DC_WEST), not a bug anymore.

**Not fixed, and not fixable by prompting — a real schema gap discovered in the process**: two questions asked about OTIF for a specific supplier ("OTIF for shipments from Supplier_A", "OTIF dropping for orders from Supplier_C"). Verified directly against Neo4j: **`Shipment` has zero relationship to `Supplier` at any hop.** Supplier identity only exists on the *inbound* side (`PurchaseOrder.supplier_id`/`FROM_SUPPLIER`), while OTIF (`otif_flag`) only exists on the *outbound* side (`Shipment`, which connects to `Customer`, not `Supplier`). There is no correct Cypher query that joins "Supplier_A" to an OTIF observation, because that edge doesn't exist in the data model — inbound supplier performance and outbound delivery performance are simply not connected. Faced with an unanswerable question, the LLM doesn't refuse — it generates a different hallucinated path on each attempt (confirmed: two separate runs of the same question produced two different wrong traversals). Added a few-shot example documenting this gap explicitly so the LLM at least drops the unconnectable qualifier rather than inventing a fake edge, but this is a genuine modeling gap that only adding real data (e.g. a `supplier_id` property on `Shipment`, or joining via shared `sku_id`+`dc_id`+overlapping date windows as a proxy) can truly fix — no prompt engineering makes an edge exist that isn't there.

**Restated finding from §4f, now with more evidence**: 3 of the 10 "hollow" results are the same underlying limitation as before — the ontology's `CausalDriver→KPI` graph is generic (system-wide), not per-instance, so it cannot compute "how much did `demand_spike` specifically affect `SKU_1001`'s stockout rate." That requires statistical baseline-vs-current comparison, which is what the RCA agent's DoWhy step does and a single Cypher query structurally cannot. All 10 of these questions are better suited to `/rca` than to NL2Cypher.

## 5. Safety gate — confirmed live, not just documented

The single most important thing to verify before trusting this pattern is that the built-in read-only refusal actually fires. It does:

```bash
$ curl -X POST http://localhost:9060/nl2cypher/query \
    -d '{"question": "Delete all purchase orders that have a cancelled status."}'
{
  "cypher": null,
  "rows": [],
  "error": "Refusing to execute non-read-only Cypher (query_type='w'): MATCH (po:PurchaseOrder {status: 'cancelled'}) DELETE po"
}
```

The LLM *did* generate a syntactically valid `DELETE` query for this prompt — proving the guardrail is doing real work here, not just handling a hypothetical. `NL2CypherEngine.ask()` catches `Text2CypherRetrievalError` and surfaces it as a normal `error` field rather than crashing (`engine.py`), and the generated (rejected) query is included in the exception message for auditability even though it never executed.

**This is one layer, not the only layer.** Per `docs/nl2cypher_poc_plan.md` §6, before this goes beyond a local POC, the `NEO4J_USER`/`NEO4J_PASSWORD` this module connects with should be a dedicated Neo4j role with database-level read-only permissions — so a bug in this application-level check (or in a future version of the library) can't mutate data. That has **not** been set up yet; today `NL2Cypher/engine.py` connects with the same `.env` credentials as the rest of the backend, which in this dev environment are not read-only-restricted.

## 6. Configuration

Configuration is read from the existing root `.env`; Azure OpenAI adds an optional provider-specific block:

| Setting | Source | Used for |
|---|---|---|
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | `config/settings.py` `Settings.neo4j_*` | `engine.py`'s `neo4j.GraphDatabase.driver(...)` call |
| `LLM_PROVIDER` (`openai`/`azure_openai`/`claude`) | `Settings.llm_provider` | `llm_provider.py`'s branch between `OpenAILLM`/`AzureOpenAILLM`/`AnthropicLLM` |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | `Settings.openai_*` | Passed straight through to `neo4j_graphrag.llm.OpenAILLM(model_name=..., api_key=...)` |
| `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_VERSION` / `AZURE_OPENAI_DEPLOYMENT` | `Settings.azure_openai_*` | Passed to `neo4j_graphrag.llm.AzureOpenAILLM`; the deployment name is used as `model_name` for Azure chat completions |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | `Settings.anthropic_*` | Same, for `AnthropicLLM` |

`llm_provider.py` deliberately does **not** call `llm_service.llm_factory.get_llm_client()` — that factory returns clients implementing this repo's own `LLMClient` Protocol (`llm_service/base_client.py`), a different interface than `neo4j_graphrag.llm.LLMInterface`. Both read the same `.env` values through the same `Settings` class, so the two stay consistent without being coupled — no adapter/shim was needed, since `neo4j_graphrag` ships thin `OpenAILLM`/`AzureOpenAILLM`/`AnthropicLLM` wrappers around the same underlying SDKs (`openai`, `anthropic`) already in `requirements/base.txt`.

To run this yourself: `./start_nl2cypher.sh` (port 9060) or `python -m NL2Cypher.cli "<question>"` — beyond what `start_backend.sh` already requires (`.env`, `.venv`, live Neo4j), the default `schema_source="live"` also requires the **APOC plugin** enabled on that Neo4j instance. If APOC isn't available, either install it or pass `schema_source="curated"` (`--curated-schema` on the CLI, or `NL2CypherEngine(schema_source="curated")` in code).

## 7. What was deliberately left out of this POC

Matching the phased plan in `docs/nl2cypher_poc_plan.md` §6 — these are Phase 2+ items, not oversights:

- **No auth** on `service.py` — it's a local test surface (`allow_origins=["*"]`), not wired to the existing `require_analyst` dependency from `graphrag_service/main.py`. Must be added before this is reachable from anywhere but localhost.
- **No dedicated read-only Neo4j role** — see §5.
- **No row-limit/timeout enforcement at the Neo4j session level** — `max_rows` in `engine.ask()` truncates the *returned* rows after the fact, but an expensive query still runs to completion first. A `LIMIT`-injection or query-cost check would be needed before this is exposed to less-trusted users.
- **No answer synthesis (`GraphRAG` wrapper)** — `engine.py` returns raw rows only, matching `docs/neo4j_native_graphrag_text2cypher.md` §6's Phase-2-or-later framing; a plain-English gloss on top of the rows is straightforward to add later but wasn't necessary to prove the core mechanism works.
- **No native-vector-index few-shot retrieval** — `examples.py` is a static list, per Phase 1 in the original plan. Phase 3 (`VectorCypherRetriever` over a growing set of real analyst questions) is future work.
- **No UI** — this POC is backend/CLI/API only, exercised via `pytest`, `curl`, and the CLI script above.

## 8. Honest limitations observed during testing

- The 10-question test suite is a smoke test, not statistical proof of reliability — `docs/nl2cypher_poc_plan.md` §7 calls for 15-20 questions including harder multi-hop cases; only 2 of the 10 here were genuinely multi-hop (`forecast_error` direct-contribution and `fill_rate` multi-hop). A larger, harder test set (especially adversarial/ambiguous phrasing) is the natural next step before broader rollout.
- Every test/example above ran against `gpt-4o-mini`-class model behavior in this environment's default provider; results with a different `LLM_PROVIDER`/model were not tested in this pass.
- The read-only refusal test (§5) confirms the guardrail catches an *obvious* destructive request. It was not tested against more adversarial phrasing designed to obscure intent (e.g. indirect prompt injection via a crafted question) — that's a reasonable next hardening step before this is exposed beyond a trusted internal user.
