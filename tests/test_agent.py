# -*- coding: utf-8 -*-
"""Testes-spec deterministicos. Take-home: comecam falhando.
Live: o teste de gate por tier e o de idempotencia revelam os bugs."""
import duckdb
from agent.build import (load, action_map, decide_action, guardrails,
                         apply_decisions, operational_metrics, pending_incidents)

ACTIONS = {
 "act-scaleout": {"action_id":"act-scaleout","action_name":"scale_out","action_type":"SELF_HEAL","is_destructive":False,"allowed_tiers":["GREEN"]},
 "act-rollback": {"action_id":"act-rollback","action_name":"rollback_deploy","action_type":"SELF_HEAL","is_destructive":True,"allowed_tiers":["GREEN"]},
 "act-openpr":   {"action_id":"act-openpr","action_name":"open_pull_request","action_type":"PR","is_destructive":False,"allowed_tiers":["GREEN","YELLOW"]},
 "act-page":     {"action_id":"act-page","action_name":"page_oncall","action_type":"ESCALATE","is_destructive":False,"allowed_tiers":["GREEN","YELLOW","RED"]},
}
def inc(sig="cpu.utilization"): return {"incident_id":"i1","service_id":"s","signal_name":sig,"value":99.0,"threshold":85}

def test_green_self_heal():
    d = decide_action(inc(), {"tier":"GREEN"}, ACTIONS)
    assert d["mode"]=="SELF_HEAL" and d["auto_apply"] is True

def test_yellow_abre_pr():
    d = decide_action(inc(), {"tier":"YELLOW"}, ACTIONS)
    assert d["mode"]=="PR" and d["action_id"]=="act-openpr" and d["auto_apply"] is False

def test_red_so_escala():
    d = decide_action(inc(), {"tier":"RED"}, ACTIONS)
    assert d["mode"]=="ESCALATE" and d["action_id"]=="act-page" and d["auto_apply"] is False

def test_guardrail_bloqueia_destrutiva_fora_de_green():
    d = {"action_id":"act-rollback","auto_apply":True}
    ok, _ = guardrails(d, {"tier":"YELLOW"}, ACTIONS)
    assert ok is False

def fixture():
    con = duckdb.connect()
    con.execute("""CREATE TABLE service_catalog AS SELECT * FROM (VALUES
      ('g','G','t','GREEN','prod',3.0,500,85,90,'r','t'),
      ('y','Y','t','YELLOW','prod',2.0,400,85,88,'r','t'),
      ('r','R','t','RED','prod',1.0,300,80,85,'r','t')
    ) t(service_id,service_name,team,tier,environment,slo_error_rate_pct,slo_p99_ms,cpu_threshold_pct,mem_threshold_pct,runbook_id,oncall_team)""")
    con.execute("""CREATE TABLE action_catalog AS SELECT * FROM (VALUES
      ('act-scaleout','scale_out','SELF_HEAL',FALSE,['GREEN']),
      ('act-rollback','rollback_deploy','SELF_HEAL',TRUE,['GREEN']),
      ('act-restart','restart_pod','SELF_HEAL',FALSE,['GREEN']),
      ('act-clearcache','clear_cache','SELF_HEAL',FALSE,['GREEN']),
      ('act-openpr','open_pull_request','PR',FALSE,['GREEN','YELLOW']),
      ('act-page','page_oncall','ESCALATE',FALSE,['GREEN','YELLOW','RED'])
    ) t(action_id,action_name,action_type,is_destructive,allowed_tiers)""")
    con.execute("""CREATE TABLE otel_signal AS SELECT * FROM (VALUES
      (1,TIMESTAMP '2026-06-30 10:00','g','METRIC','cpu.utilization',99.0,'percent'),
      (2,TIMESTAMP '2026-06-30 10:00','y','METRIC','http.error_rate',9.0,'percent'),
      (3,TIMESTAMP '2026-06-30 10:00','r','METRIC','latency.p99',900.0,'ms')
    ) t(signal_id,ts,service_id,signal_kind,name,value,unit)""")
    return con

def test_apply_idempotente_e_gate_por_tier():
    con = fixture()
    primeira = apply_decisions(con)
    segunda = apply_decisions(con)          # rerun nao pode duplicar
    assert primeira == 3 and segunda == 0
    rows = dict(con.sql("SELECT service_id, status FROM action").fetchall())
    assert rows == {"g":"APPLIED", "y":"PROPOSED", "r":"ESCALATED"}

def test_rag_fundamenta_decisao():
    from agent.build import runbook_for
    assert runbook_for("cpu.utilization") == "runbooks/runbook_cpu_saturation.md"
    assert runbook_for("mem.utilization") == "runbooks/runbook_oom.md"
