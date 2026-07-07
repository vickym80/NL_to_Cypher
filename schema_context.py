"""Hand-curated Neo4j schema strings for Text2CypherRetriever.

`neo4j_graphrag.schema.get_schema(driver)` can auto-introspect the live database,
but applied to this instance it would include the ~600-node mock instance data's
labels/properties verbatim alongside the 42-node core ontology — a much larger,
noisier prompt than most questions need. We hand-curate instead (see
docs/neo4j_native_graphrag_text2cypher.md §5) and keep two variants: a lean
ontology-only schema for causal/structural questions, and a fuller schema that
adds the mock instance-data labels for questions about concrete records
(purchase orders, inventory snapshots, etc).

Property lists below were confirmed against the live ontology/mock-data YAML
files (ontology/nodes.yaml, ontology/edges.yaml, ontology/mock_data/*.yaml),
not guessed.
"""

from __future__ import annotations

ONTOLOGY_SCHEMA = """\
Node properties:
KPI {id: STRING, name: STRING, description: STRING, directionality: STRING, gold_table: STRING, outcome_column: STRING}
CausalDriver {id: STRING, name: STRING, description: STRING, domain: STRING, rca_relevance: STRING}
IntermediateFactor {id: STRING, name: STRING, description: STRING}
Intervention {id: STRING, name: STRING, description: STRING, action_owner: STRING, approval_required: BOOLEAN, lead_time_days: INTEGER}
Supplier {id: STRING, name: STRING, description: STRING, tier: INTEGER, country_of_origin: STRING, category: STRING}
DistributionCenter {id: STRING, name: STRING, description: STRING, region: STRING, country: STRING}
SKU {id: STRING, name: STRING, description: STRING, category: STRING}
GoldTable {id: STRING, name: STRING, description: STRING, catalog: STRING, schema: STRING, grain: STRING}
GeopoliticalRisk {id: STRING, name: STRING, description: STRING, risk_category: STRING, impact_horizon_days: INTEGER}
SupplyRisk {id: STRING, name: STRING, description: STRING, risk_category: STRING}
DemandRisk {id: STRING, name: STRING, description: STRING, risk_category: STRING}
LogisticsRisk {id: STRING, name: STRING, description: STRING, risk_category: STRING}
ComplianceRequirement {id: STRING, name: STRING, description: STRING, regulation: STRING, risk_category: STRING}
Carrier {id: STRING, name: STRING, mode: STRING, reliability_score: FLOAT}
Port {id: STRING, name: STRING, country: STRING, risk_level: STRING, avg_dwell_days: FLOAT}
TransportLane {id: STRING, name: STRING, origin_port: STRING, destination_dc: STRING, avg_transit_days: INTEGER, mode: STRING, is_primary: BOOLEAN, cost_premium_pct: INTEGER}

Relationship properties:
CAUSES {confidence: FLOAT, ate_estimate: FLOAT, ate_ci_lower: FLOAT, ate_ci_upper: FLOAT, lag_days_min: INTEGER, lag_days_max: INTEGER, evidence_type: STRING, is_validated: BOOLEAN, applicable_kpis: LIST, applicable_grains: LIST}
CONTRIBUTES_TO {confidence: FLOAT, evidence_type: STRING, is_validated: BOOLEAN, applicable_kpis: LIST}
MITIGATES {}
STORED_AT {}
SUPPLIED_BY {}
SHIPS_TO {}
SUPPLIES_TO {}

The relationships:
(:CausalDriver)-[:CAUSES]->(:CausalDriver)
(:CausalDriver)-[:CAUSES]->(:IntermediateFactor)
(:CausalDriver)-[:CAUSES]->(:KPI)
(:IntermediateFactor)-[:CAUSES]->(:IntermediateFactor)
(:IntermediateFactor)-[:CAUSES]->(:KPI)
(:CausalDriver)-[:CONTRIBUTES_TO]->(:CausalDriver)
(:Intervention)-[:MITIGATES]->(:CausalDriver)
(:Intervention)-[:MITIGATES]->(:IntermediateFactor)
(:SKU)-[:STORED_AT]->(:DistributionCenter)
(:SKU)-[:SUPPLIED_BY]->(:Supplier)
(:Supplier)-[:SHIPS_TO]->(:DistributionCenter)
(:Supplier)-[:SUPPLIES_TO]->(:Supplier)

Note: multi-hop causal-chain questions ("what eventually leads to X") should use a
variable-length pattern across CAUSES and CONTRIBUTES_TO, e.g.
[:CAUSES|CONTRIBUTES_TO*1..5]. Two additional relationship types, MEDIATES and
AMPLIFIES, are reserved in the ontology's causal-chain retrieval logic but are not
present in the currently loaded data — do not assume they will match anything today.

Note: GeopoliticalRisk, SupplyRisk, DemandRisk, LogisticsRisk, ComplianceRequirement,
Carrier, Port, and TransportLane are currently isolated reference nodes with no
relationships to the rest of the graph (confirmed via live schema introspection) —
query them with plain property filters (e.g. MATCH (p:Port) WHERE p.risk_level =
'high'), not graph traversal. TransportLane's origin_port/destination_dc are plain
string properties, not relationships, despite the names.

Note: IntermediateFactor nodes are also labeled CausalMediator in the live database
(both labels coexist on the same nodes) — MATCH (:IntermediateFactor) already
matches everything MATCH (:CausalMediator) would.

Note: GeopoliticalRisk, SupplyRisk, DemandRisk, LogisticsRisk, and
ComplianceRequirement nodes are all also labeled with the generic base label
RiskNode (confirmed live: every node under one of those 5 specific labels also
carries RiskNode). Use MATCH (r:RiskNode) to match across all five risk subtypes
at once (e.g. "list every risk node") instead of naming each subtype individually.
"""

INSTANCE_DATA_SCHEMA = """\
Node properties:
Customer {id: STRING, name: STRING, region: STRING, channel: STRING, tier: STRING}
PurchaseOrder {id: STRING, sku_id: STRING, supplier_id: STRING, dc_id: STRING, order_date: DATE, promised_arrival_date: DATE, actual_arrival_date: DATE, quantity_ordered: INTEGER, quantity_received: INTEGER, lead_time_days_planned: INTEGER, lead_time_days_actual: INTEGER, delay_days: INTEGER, status: STRING}
InventorySnapshot {id: STRING, sku_id: STRING, dc_id: STRING, date: DATE, on_hand_units: INTEGER, safety_stock_units: INTEGER, avg_daily_demand_units: INTEGER, inventory_cover_days: FLOAT, is_below_safety_stock: BOOLEAN}
DemandForecastSnapshot {id: STRING, sku_id: STRING, dc_id: STRING, date: DATE, forecast_units: INTEGER, actual_demand_units: INTEGER, forecast_error_pct: FLOAT, bias_direction: STRING, promotion_flag: BOOLEAN}
Shipment {id: STRING, sku_id: STRING, dc_id: STRING, customer_id: STRING, order_date: DATE, promised_delivery_date: DATE, actual_delivery_date: DATE, quantity_ordered: INTEGER, quantity_shipped: INTEGER, otif_flag: BOOLEAN, perfect_order_flag: BOOLEAN}
KPIObservation {id: STRING, kpi_id: STRING, sku_id: STRING, dc_id: STRING, date: DATE, value: FLOAT, threshold_breached: BOOLEAN}

Relationship properties:
EVIDENCES {evidence_type: STRING, observed_value: FLOAT, threshold_value: FLOAT, severity: STRING, confidence: FLOAT, date: DATE, source_table: STRING, source_record_id: STRING}
FOR_SKU {}
AT_DC {}
FROM_SUPPLIER {}
TO_CUSTOMER {}
OBSERVES_KPI {}

The relationships:
(:PurchaseOrder)-[:FOR_SKU]->(:SKU)
(:PurchaseOrder)-[:AT_DC]->(:DistributionCenter)
(:PurchaseOrder)-[:FROM_SUPPLIER]->(:Supplier)
(:Shipment)-[:TO_CUSTOMER]->(:Customer)
(:KPIObservation)-[:OBSERVES_KPI]->(:KPI)
(:PurchaseOrder)-[:EVIDENCES]->(:CausalDriver)
(:DemandForecastSnapshot)-[:EVIDENCES]->(:CausalDriver)
(:InventorySnapshot)-[:EVIDENCES]->(:IntermediateFactor)

Note: instance nodes (PurchaseOrder, InventorySnapshot, DemandForecastSnapshot,
Shipment, KPIObservation) also carry sku_id/dc_id/supplier_id/customer_id as plain
string properties directly, in addition to the FOR_SKU/AT_DC/FROM_SUPPLIER/
TO_CUSTOMER relationships to the corresponding ontology node — either can be used
to filter by SKU/DC/supplier/customer; property filters are usually simpler.
"""


def get_schema_context(include_instance_data: bool = True) -> str:
    """Return the curated Neo4j schema string to hand to Text2CypherRetriever.

    include_instance_data=True (default) covers both the causal ontology and the
    concrete mock instance records, since most ad hoc analyst questions touch both
    ("which drivers..." as well as "which purchase orders..."). Set to False for a
    smaller prompt when a question is known to be purely about causal structure.
    """
    if include_instance_data:
        return ONTOLOGY_SCHEMA + "\n" + INSTANCE_DATA_SCHEMA
    return ONTOLOGY_SCHEMA
