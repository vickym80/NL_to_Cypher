"""Natural-language-to-Cypher POC, built on Neo4j's native `neo4j-graphrag` package.

Deliberately isolated from `graphrag_service`/`rca_agent`: this is a general-purpose,
read-only, ad hoc graph query assistant, not a replacement for the RCA pipeline's
deterministic causal-path templates. See docs/implementation_nl2cypher.md.
"""
