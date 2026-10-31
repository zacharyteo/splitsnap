"""Optional Neo4j trip ledger (P1). Used only if NEO4J_URI is set; the app works without it."""
from __future__ import annotations

import os
import uuid

NET_QUERY = """
MATCH (g:Group {id:$gid})-[:HAS_OUTING]->(o:Outing)
MATCH (p:Person)-[r]->(o) WHERE type(r) IN ['PAID','OWES_SHARE']
WITH p, sum(CASE type(r) WHEN 'PAID' THEN r.cents ELSE -r.cents END) AS net
RETURN p.name AS name, net ORDER BY net DESC
"""


def enabled() -> bool:
    return bool(os.environ.get("NEO4J_URI"))


def _driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.environ["NEO4J_URI"],
                                auth=(os.environ.get("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]))


def save_outing(gid: str, venue: str, payer: str, totals: dict[str, int]) -> None:
    oid = str(uuid.uuid4())
    with _driver() as d, d.session() as s:
        s.run("MERGE (g:Group {id:$gid}) SET g.name=$gid "
              "CREATE (g)-[:HAS_OUTING]->(o:Outing {id:$oid, venue:$venue, total_cents:$tot}) "
              "MERGE (pp:Person {name:$payer}) MERGE (pp)-[:MEMBER_OF]->(g) "
              "CREATE (pp)-[:PAID {cents:$tot}]->(o) "
              "WITH g, o UNWIND $rows AS row "
              "MERGE (p:Person {name:row.name}) MERGE (p)-[:MEMBER_OF]->(g) "
              "CREATE (p)-[:OWES_SHARE {cents:row.cents}]->(o)",
              gid=gid, oid=oid, venue=venue, payer=payer, tot=sum(totals.values()),
              rows=[{"name": k, "cents": v} for k, v in totals.items()])


def net_balances(gid: str) -> dict[str, int]:
    with _driver() as d, d.session() as s:
        return {r["name"]: r["net"] for r in s.run(NET_QUERY, gid=gid)}
