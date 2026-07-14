# -*- coding: utf-8 -*-
"""Testes do candidato (test_agent.py, o spec do starter, permanece intocado).
Cobrem o que os specs nao exercitam: limpeza do feed sujo, escolha da acao via
runbook e os guardrails CONTEXTUAIS de apply_decisions (fundamentacao fail-closed,
blast radius e rate limit)."""
import duckdb

from agent.build import apply_decisions, decide_action, pending_incidents

ACTIONS = {
 "act-scaleout":  {"action_id":"act-scaleout","action_name":"scale_out","action_type":"SELF_HEAL","is_destructive":False,"allowed_tiers":["GREEN"]},
 "act-clearcache":{"action_id":"act-clearcache","action_name":"clear_cache","action_type":"SELF_HEAL","is_destructive":False,"allowed_tiers":["GREEN"]},
 "act-rollback":  {"action_id":"act-rollback","action_name":"rollback_deploy","action_type":"SELF_HEAL","is_destructive":True,"allowed_tiers":["GREEN"]},
}


def _con(servicos, sinais):
    """servicos: [(service_id, team, tier)]; sinais: [(id, ts, service_id, name, value, unit)]."""
    con = duckdb.connect()
    con.execute("CREATE TABLE service_catalog AS SELECT * FROM (VALUES " + ",".join(
        f"('{s}','{s.upper()}','{t}','{tier}','prod',3.0,500,85,90,'r','oncall-{t}')"
        for s, t, tier in servicos) +
        ") t(service_id,service_name,team,tier,environment,slo_error_rate_pct,"
        "slo_p99_ms,cpu_threshold_pct,mem_threshold_pct,runbook_id,oncall_team)")
    con.execute("""CREATE TABLE action_catalog AS SELECT * FROM (VALUES
      ('act-scaleout','scale_out','SELF_HEAL',FALSE,['GREEN']),
      ('act-rollback','rollback_deploy','SELF_HEAL',TRUE,['GREEN']),
      ('act-restart','restart_pod','SELF_HEAL',FALSE,['GREEN']),
      ('act-clearcache','clear_cache','SELF_HEAL',FALSE,['GREEN']),
      ('act-openpr','open_pull_request','PR',FALSE,['GREEN','YELLOW']),
      ('act-page','page_oncall','ESCALATE',FALSE,['GREEN','YELLOW','RED'])
    ) t(action_id,action_name,action_type,is_destructive,allowed_tiers)""")
    con.execute("CREATE TABLE otel_signal AS SELECT * FROM (VALUES " + ",".join(
        f"({i},TIMESTAMP '{ts}','{svc}','METRIC','{nome}',{v},'{u}')"
        for i, ts, svc, nome, v, u in sinais) +
        ") t(signal_id,ts,service_id,signal_kind,name,value,unit)")
    return con


def test_limpeza_do_feed_sujo_antes_da_deteccao():
    """Dup de signal_id, servico fantasma e leituras impossiveis saem ANTES do breach;
    value NULL (esperado em LOG) passa pela limpeza sem virar incidente nem quebrar."""
    con = _con([("g", "t1", "GREEN")], [
        (1, "2026-06-30 10:00", "g", "cpu.utilization", "99.0", "percent"),
        (1, "2026-06-30 10:00", "g", "cpu.utilization", "99.0", "percent"),   # duplicata
        (2, "2026-06-30 10:05", "svc-ghost", "cpu.utilization", "99.0", "percent"),  # fantasma
        (3, "2026-06-30 10:10", "g", "mem.utilization", "250.0", "percent"),  # impossivel >100
        (4, "2026-06-30 10:15", "g", "latency.p99", "-50.0", "ms"),           # impossivel <0
        (5, "2026-06-30 10:20", "g", "log.record", "NULL", "count"),  # nulo esperado (LOG)
    ])
    assert pending_incidents(con) == 1
    fila = con.sql("SELECT service_id, signal_name FROM pending").fetchall()
    assert fila == [("g", "cpu.utilization")]


def test_acao_green_vem_do_runbook_recuperado():
    """Tarefa 2: escolha via runbook. Para latency.p99 o runbook_latency.md manda
    scale_out — a acao deve vir dele (a heuristica antiga por sinal dava clearcache)."""
    d = decide_action({"incident_id": "i", "service_id": "s",
                       "signal_name": "latency.p99", "value": 900.0, "threshold": 500},
                      {"tier": "GREEN"}, ACTIONS)
    assert d["action_id"] == "act-scaleout"
    assert "runbook_latency" in d["reason"]


def test_fail_closed_sem_runbook_nao_auto_aplica(tmp_path):
    """'Toda acao carrega sua fonte': self-heal sem runbook que fundamente escala."""
    corpus_vazio = tmp_path / "knowledge"
    corpus_vazio.mkdir()
    con = _con([("g", "t1", "GREEN")],
               [(1, "2026-06-30 10:00", "g", "cpu.utilization", 99.0, "percent")])
    assert apply_decisions(con, corpus_dir=str(corpus_vazio)) == 1
    status, reason = con.sql("SELECT status, reason FROM action").fetchone()
    assert status == "ESCALATED" and "sem fundamentacao" in reason


def test_rate_limit_recorrencia_na_janela_escala():
    """Duas auto-aplicacoes no mesmo servico em <30min: a segunda escala
    (runbook_oom: 'recorrente em menos de 30 min, escalar')."""
    con = _con([("g", "t1", "GREEN")], [
        (1, "2026-06-30 10:00", "g", "cpu.utilization", 99.0, "percent"),
        (2, "2026-06-30 10:10", "g", "mem.utilization", 95.0, "percent"),  # +10min
    ])
    assert apply_decisions(con) == 2
    por_sinal = dict(con.sql("SELECT signal_name, status FROM action").fetchall())
    assert por_sinal["cpu.utilization"] == "APPLIED"
    assert por_sinal["mem.utilization"] == "ESCALATED"
    assert "rate limit" in con.sql(
        "SELECT reason FROM action WHERE signal_name = 'mem.utilization'").fetchone()[0]


def test_rate_limit_ignora_backfill_com_ts_anterior():
    """So recorrencia POSTERIOR conta: incidente que chega num run seguinte mas com
    ts ANTERIOR ao ultimo APPLIED (backfill/replay fora de ordem) nao e recorrencia."""
    con = _con([("g", "t1", "GREEN")],
               [(1, "2026-06-30 10:20", "g", "cpu.utilization", 99.0, "percent")])
    assert apply_decisions(con) == 1                        # APPLIED as 10:20
    con.execute("""INSERT INTO otel_signal VALUES
        (2, TIMESTAMP '2026-06-30 10:00', 'g', 'METRIC', 'mem.utilization', 95.0, 'percent')""")
    assert apply_decisions(con) == 1                        # breach backfilled (ts anterior)
    por_sinal = dict(con.sql("SELECT signal_name, status FROM action").fetchall())
    assert por_sinal == {"cpu.utilization": "APPLIED", "mem.utilization": "APPLIED"}


def test_blast_radius_destrutiva_em_incidente_sistemico_vira_pr():
    """Destrutiva-auto so em incidente isolado: com 3+ servicos do MESMO time em
    breach, o rollback automatico e rebaixado a PR; em time isolado, aplica."""
    con = _con([("a", "data", "GREEN"), ("b", "data", "GREEN"), ("c", "data", "GREEN"),
                ("z", "solo", "GREEN")], [
        (1, "2026-06-30 08:00", "a", "http.error_rate", 9.0, "percent"),
        (2, "2026-06-30 09:00", "b", "http.error_rate", 9.0, "percent"),
        (3, "2026-06-30 10:00", "c", "http.error_rate", 9.0, "percent"),
        (4, "2026-06-30 10:00", "z", "http.error_rate", 9.0, "percent"),
    ])
    assert apply_decisions(con) == 4
    por_svc = dict(con.sql("SELECT service_id, status FROM action").fetchall())
    assert por_svc == {"a": "PROPOSED", "b": "PROPOSED", "c": "PROPOSED", "z": "APPLIED"}
    assert "blast radius" in con.sql(
        "SELECT reason FROM action WHERE service_id = 'a'").fetchone()[0]


def test_fonte_e_latencia_registradas_na_acao():
    """Tarefa 6 ('registre a fonte na acao') + compliance §4 (latencia da decisao)."""
    con = _con([("g", "t1", "GREEN")],
               [(1, "2026-06-30 10:00", "g", "cpu.utilization", 99.0, "percent")])
    apply_decisions(con)
    runbook, score, lat = con.sql(
        "SELECT runbook, runbook_score, latency_ms FROM action").fetchone()
    assert runbook == "runbooks/runbook_cpu_saturation.md" and score > 0
    assert lat >= 0
