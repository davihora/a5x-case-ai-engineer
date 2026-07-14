# -*- coding: utf-8 -*-
"""Pipeline (idempotente): fila -> decisao/guardrail -> action -> RAG (fundamentacao) -> dashboard."""
import duckdb
from agent.build import (load, apply_decisions, operational_metrics, grounded_actions,
                         decision_trace)
from dashboard.render import render

def main():
    con = duckdb.connect(); load(con)
    print("novas acoes:", apply_decisions(con))
    print("metricas:", operational_metrics(con))
    ga = grounded_actions(con)                        # cada acao fundamentada por runbook (RAG)
    print("acoes fundamentadas (RAG):", len(ga), "| exemplo:", ga[0] if ga else None)
    con.sql("SELECT * FROM action").write_parquet("out_action.parquet", overwrite=True)
    n_trace = decision_trace(con)                     # trilha de auditoria (compliance §4)
    print("dashboard:", render(con))                  # viz: so le action/decision_trace
    con.sql("SELECT * FROM decision_trace").write_parquet("out_decision_trace.parquet", overwrite=True)
    print("decision_trace:", n_trace)

if __name__ == "__main__":
    main()
