"""Few-shot NL-question -> Cypher examples for Text2CypherRetriever.

Every query below was hand-written against the real ontology/mock-data schema
(see schema_context.py) and is intended to be validated by actually running it
against the live Neo4j instance (see tests/test_nl2cypher.py) — these are meant
to teach the LLM this schema's idioms, especially the variable-length causal-path
pattern, which a generic few-shot set (e.g. a movie-graph example) would not cover.

Format matches neo4j_graphrag.retrievers.Text2CypherRetriever's expected shape:
one string per example, "USER INPUT: '<question>' QUERY: <cypher>".

Note: the "IMPORTANT: ..." lines above several examples below are Python `#`
comments — the interpreter strips them before this list is built, so the LLM
never actually sees that reasoning at runtime, only the bare NL/Cypher pair.
The enum/gotcha facts they describe (valid KPI.id values, valid evidence_type
values, date properties being STRING not DATE) are now derived live and
rendered directly into the schema text instead (see schema_context.py's
enum-value sampling and auto-generated Notes) — that's what the LLM actually
sees, and it stays correct as the graph changes. The comments here are kept
only as documentation for future editors of this file.

Engine startup also runs every example here through
schema_context.filter_valid_examples() against the live schema, dropping any
example that references a label/relationship type no longer present in the
graph, so a schema change can't leave a stale example actively teaching the
LLM a wrong pattern.
"""

from __future__ import annotations

NL2CYPHER_EXAMPLES: list[str] = [
    "USER INPUT: 'Which suppliers ship to DC_EAST?' "
    "QUERY: MATCH (s:Supplier)-[:SHIPS_TO]->(dc:DistributionCenter {id: 'DC_EAST'}) "
    "RETURN s.id, s.name, s.tier",

    "USER INPUT: 'List all interventions that do not require approval.' "
    "QUERY: MATCH (i:Intervention {approval_required: false}) "
    "RETURN i.id, i.name, i.action_owner, i.lead_time_days",

    "USER INPUT: 'Which causal drivers can eventually lead to an increase in stockout_rate?' "
    "QUERY: MATCH (d:CausalDriver)-[:CAUSES|CONTRIBUTES_TO*1..5]->(k:KPI {id: 'stockout_rate'}) "
    "RETURN DISTINCT d.id, d.name",

    "USER INPUT: 'What interventions mitigate supplier lead time delay?' "
    "QUERY: MATCH (i:Intervention)-[:MITIGATES]->(d {id: 'supplier_lead_time_delay'}) "
    "RETURN i.id, i.name, i.approval_required, i.action_owner",

    "USER INPUT: 'Which purchase orders were delayed by more than 5 days?' "
    "QUERY: MATCH (po:PurchaseOrder) WHERE po.delay_days > 5 "
    "RETURN po.id, po.sku_id, po.supplier_id, po.delay_days ORDER BY po.delay_days DESC",

    "USER INPUT: 'How many inventory snapshots for SKU_1002 were below safety stock?' "
    "QUERY: MATCH (inv:InventorySnapshot {sku_id: 'SKU_1002', is_below_safety_stock: true}) "
    "RETURN count(inv) AS below_safety_stock_count",

    "USER INPUT: 'Show evidence records supporting the port_congestion driver, most recent first.' "
    "QUERY: MATCH (e)-[:EVIDENCES]->(d:CausalDriver {id: 'port_congestion'}) "
    "RETURN e.id, e.evidence_type, e.severity, e.confidence, e.date ORDER BY e.date DESC",

    "USER INPUT: 'What is the average forecast error percentage for SKU_1001 at DC_WEST?' "
    "QUERY: MATCH (f:DemandForecastSnapshot {sku_id: 'SKU_1001', dc_id: 'DC_WEST'}) "
    "RETURN avg(f.forecast_error_pct) AS avg_forecast_error_pct",

    "USER INPUT: 'List every KPI and its directionality.' "
    "QUERY: MATCH (k:KPI) RETURN k.id, k.name, k.directionality",

    "USER INPUT: 'Which SKUs are stored at more than one distribution center?' "
    "QUERY: MATCH (s:SKU)-[:STORED_AT]->(dc:DistributionCenter) "
    "WITH s, count(dc) AS dc_count WHERE dc_count > 1 RETURN s.id, dc_count",

    "USER INPUT: 'Which ports have a high risk level?' "
    "QUERY: MATCH (p:Port {risk_level: 'high'}) RETURN p.id, p.name, p.country, p.avg_dwell_days",

    "USER INPUT: 'List transport lanes that are not the primary lane.' "
    "QUERY: MATCH (l:TransportLane {is_primary: false}) "
    "RETURN l.id, l.name, l.origin_port, l.destination_dc, l.mode, l.cost_premium_pct",

    # IMPORTANT: every date/order_date/promised_arrival_date/etc. property on instance
    # nodes (InventorySnapshot, DemandForecastSnapshot, PurchaseOrder, Shipment,
    # KPIObservation) is stored as a STRING like '2026-06-29', not a native Neo4j Date.
    # Comparing a STRING directly to date() silently matches nothing (no error) — always
    # wrap the property in date(...) first when filtering by a relative time window.
    "USER INPUT: 'Show inventory snapshots for SKU_1002 at DC_EAST from the last 14 days.' "
    "QUERY: MATCH (i:InventorySnapshot {sku_id: 'SKU_1002', dc_id: 'DC_EAST'}) "
    "WHERE date(i.date) >= date() - duration('P14D') "
    "RETURN i.id, i.date, i.on_hand_units, i.inventory_cover_days, i.is_below_safety_stock "
    "ORDER BY i.date DESC",

    "USER INPUT: 'What was the average forecast error for SKU_1001 at DC_WEST over the last 30 days?' "
    "QUERY: MATCH (f:DemandForecastSnapshot {sku_id: 'SKU_1001', dc_id: 'DC_WEST'}) "
    "WHERE date(f.date) >= date() - duration('P30D') "
    "RETURN avg(f.forecast_error_pct) AS avg_forecast_error_pct",

    # IMPORTANT: a KPI name like fill_rate/stockout_rate/otifd is NEVER a property name
    # on KPIObservation — it's the VALUE of the kpi_id property. The actual observed
    # number is always in the generic `value` property. Filter with {kpi_id: '<kpi>'},
    # never with a made-up property like `fill_rate` or `stockout_rate` directly.
    "USER INPUT: 'How did fill_rate change for SKU_1001 at DC_WEST over the last 30 days?' "
    "QUERY: MATCH (o:KPIObservation {sku_id: 'SKU_1001', dc_id: 'DC_WEST', kpi_id: 'fill_rate'}) "
    "WHERE date(o.date) >= date() - duration('P30D') "
    "RETURN o.date, o.value, o.threshold_breached ORDER BY o.date DESC",

    # IMPORTANT: the only valid KPI.id values are exactly: days_of_supply, fill_rate,
    # otifd, perfect_order_rate, stockout_rate, supplier_otd. Common phrasings do NOT
    # match these literally — "OTIF" / "on-time in-full delivery" means kpi_id 'otifd'
    # (or 'supplier_otd' if the question is specifically about supplier on-time
    # delivery, not customer delivery). Never invent a KPI id like 'otif' — if unsure,
    # match on k.name instead, e.g. WHERE k.name CONTAINS 'On-Time'.
    "USER INPUT: 'Which distribution centers have low on-time in-full delivery?' "
    "QUERY: MATCH (o:KPIObservation {kpi_id: 'otifd', threshold_breached: true}) "
    "RETURN DISTINCT o.dc_id, o.sku_id, o.date, o.value ORDER BY o.date DESC",

    # IMPORTANT: EVIDENCES edges only ever target CausalDriver, CausalMediator, or
    # IntermediateFactor — never a KPI node directly. Do not write
    # (:KPI)<-[:EVIDENCES]-(...); it will never match. The only real evidence_type
    # values currently in the data are: forecast_bias, inventory_shortage,
    # supplier_delay — never invent a value like 'OTIF' or 'delay'.
    "USER INPUT: 'Show evidence of supplier delay affecting DC_EAST in the last 60 days.' "
    "QUERY: MATCH (e)-[ev:EVIDENCES {evidence_type: 'supplier_delay'}]->(d) "
    "WHERE date(ev.date) >= date() - duration('P60D') "
    "RETURN e.id, d.id AS driver, ev.severity, ev.observed_value, ev.threshold_value, ev.date "
    "ORDER BY ev.date DESC",

    # IMPORTANT: Shipment (outbound, DC -> Customer) has NO relationship to Supplier at
    # all — supplier_id/FROM_SUPPLIER only exists on PurchaseOrder (inbound, Supplier ->
    # DC). "OTIF for shipments from SupplierX" cannot be answered by joining Shipment to
    # Supplier — that edge doesn't exist. Query Shipment on its own properties
    # (sku_id/dc_id/customer_id/otif_flag) instead, and drop the supplier qualifier, or
    # if the question is really about inbound delivery performance, use PurchaseOrder
    # (which DOES have FROM_SUPPLIER) instead of Shipment.
    "USER INPUT: 'Which shipments to DC_EAST were not on-time-in-full in the last 21 days?' "
    "QUERY: MATCH (sh:Shipment {dc_id: 'DC_EAST', otif_flag: false}) "
    "WHERE date(sh.actual_delivery_date) >= date() - duration('P21D') "
    "RETURN sh.id, sh.sku_id, sh.customer_id, sh.promised_delivery_date, sh.actual_delivery_date "
    "ORDER BY sh.actual_delivery_date DESC",
]
