# -*- coding: utf-8 -*-
"""STARTER (TAKE-HOME) - Agente de Remediacao.
O agente age conforme a criticidade do servico:
  GREEN -> self-heal automatico + notifica | YELLOW -> propoe via PR + notifica | RED -> so escala.
Camadas: backend (decide/guardrails/apply) + modelagem (tabela action) + viz (operational_metrics)."""
import os

import duckdb

SIGNAL_ACTION = {  # acao "primeira escolha" por sinal (heuristica de runbook)
    "cpu.utilization": "act-scaleout", "mem.utilization": "act-restart",
    "http.error_rate": "act-rollback", "latency.p99": "act-clearcache",
}

_ACTION_SCHEMA = {  # schema do ledger `action` (hidratacao valida contra ele)
    "incident_id": "VARCHAR", "service_id": "VARCHAR", "tier": "VARCHAR",
    "signal_name": "VARCHAR", "mode": "VARCHAR", "action_id": "VARCHAR",
    "auto_apply": "BOOLEAN", "status": "VARCHAR", "reason": "VARCHAR", "ts": "TIMESTAMP",
}

def load(con, data_dir="data"):
    con.execute(open(f"{data_dir}/create_tables.sql").read().replace("{data_dir}", data_dir))
    # retoma o ledger de acoes do run anterior (idempotencia entre execucoes do run.py);
    # ledger ilegivel ou com schema divergente e descartado: e artefato derivavel de data/
    if os.path.exists("out_action.parquet"):
        try:
            schema = dict(r[:2] for r in con.sql(
                "DESCRIBE SELECT * FROM read_parquet('out_action.parquet')").fetchall())
            if schema == _ACTION_SCHEMA:
                con.execute("CREATE OR REPLACE TABLE action AS SELECT * FROM read_parquet('out_action.parquet')")
        except duckdb.Error:
            pass

def action_map(con):
    rows = con.sql("SELECT action_id, action_name, action_type, is_destructive, allowed_tiers FROM action_catalog").fetchall()
    return {r[0]: {"action_id": r[0], "action_name": r[1], "action_type": r[2],
                   "is_destructive": r[3], "allowed_tiers": list(r[4])} for r in rows}

def pending_incidents(con):
    """Fila do agente: o breach mais recente por (servico, sinal). Cria a TEMP VIEW
    `pending` com incident_id, service_id, tier, signal_name, value, threshold, ts.
    Limpeza ANTES da deteccao (dedup, servico fora do catalogo, range de percent);
    retorna o numero de incidentes pendentes."""
    con.execute("""
    CREATE OR REPLACE TEMP VIEW pending AS
    WITH dedup AS (
      -- 1) dedup de signal_id: 1 linha por id (copias sao identicas; empate resolvido
      --    por ordem total explicita para ser deterministico mesmo se divergissem)
      SELECT * FROM otel_signal
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY signal_id
        ORDER BY ts, service_id, name, value, unit) = 1
    ),
    validos AS (
      -- 2) semi-join com o catalogo (descarta servico fantasma) + range-check:
      --    leitura em percent fora de [0, 100] e fisicamente impossivel -> lixo
      SELECT d.* FROM dedup d
      SEMI JOIN service_catalog s ON d.service_id = s.service_id
      WHERE d.unit <> 'percent' OR d.value BETWEEN 0 AND 100
    ),
    com_threshold AS (
      -- 3) limiar correto por sinal, cada um da sua coluna do catalogo e na MESMA
      --    unidade do sinal (percent p/ cpu/mem/error_rate; ms p/ p99); sinais sem
      --    limiar (log.record, span.duration) ficam com NULL e caem no passo 4
      SELECT v.*, s.tier,
             CASE v.name
               WHEN 'cpu.utilization' THEN s.cpu_threshold_pct
               WHEN 'mem.utilization' THEN s.mem_threshold_pct
               WHEN 'http.error_rate' THEN s.slo_error_rate_pct
               WHEN 'latency.p99'     THEN s.slo_p99_ms
             END AS threshold
      FROM validos v JOIN service_catalog s ON v.service_id = s.service_id
    ),
    breaches AS (
      -- 4) violacao: valor estritamente acima do limiar (NULL nao compara -> sai)
      SELECT * FROM com_threshold WHERE value > threshold
    )
    -- 5) fila final: o breach mais recente por (servico, sinal); empate de ts
    --    decidido por signal_id p/ manter deterministico
    SELECT 'inc-' || signal_id AS incident_id, service_id, tier,
           name AS signal_name, value, threshold, ts
    FROM breaches
    QUALIFY ROW_NUMBER() OVER (
      PARTITION BY service_id, name
      ORDER BY ts DESC, signal_id DESC) = 1
    ORDER BY service_id, signal_name
    """)
    return con.sql("SELECT count(*) FROM pending").fetchone()[0]

def decide_action(incident, service, actions):
    """Decide modo e acao pelo TIER: GREEN->SELF_HEAL(auto), YELLOW->PR(act-openpr),
    RED->ESCALATE(act-page). Tier desconhecido escala por seguranca."""
    tier, sig = service["tier"], incident.get("signal_name")
    base = {"incident_id": incident["incident_id"], "service_id": incident["service_id"], "tier": tier}
    if tier == "GREEN":
        aid = SIGNAL_ACTION.get(sig)  # primeira escolha do runbook por sinal
        return {**base, "mode": "SELF_HEAL", "action_id": aid, "auto_apply": True,
                "reason": f"GREEN: self-heal automatico ({sig} -> {aid})"}
    if tier == "YELLOW":
        return {**base, "mode": "PR", "action_id": "act-openpr", "auto_apply": False,
                "reason": f"YELLOW: propoe correcao via PR ({sig})"}
    if tier == "RED":
        return {**base, "mode": "ESCALATE", "action_id": "act-page", "auto_apply": False,
                "reason": f"RED: apenas escala ao on-call ({sig})"}
    return {**base, "mode": "ESCALATE", "action_id": "act-page", "auto_apply": False,
            "reason": f"tier desconhecido ({tier}): escala por seguranca ({sig})"}

def guardrails(decision, service, actions):
    """Defesa em profundidade, independente do decisor. Tudo vem do catalogo (`actions`):
    bloqueia acao fora da whitelist do tier (allowed_tiers) e destrutiva auto-aplicada
    fora de GREEN. Retorna (ok, motivo)."""
    tier, aid = service["tier"], decision.get("action_id")
    acao = actions.get(aid)
    if acao is None:
        return False, f"acao '{aid}' inexistente no catalogo"
    if tier not in acao["allowed_tiers"]:
        return False, f"{aid} nao permitida no tier {tier} (allowed_tiers={acao['allowed_tiers']})"
    if acao["is_destructive"] and decision.get("auto_apply") and tier != "GREEN":
        return False, f"{aid} e destrutiva: auto-aplicacao proibida fora de GREEN (tier={tier})"
    return True, "ok"

def apply_decisions(con):
    """Materializa as decisoes na tabela `action` de forma IDEMPOTENTE: anti-join por
    incident_id garante que rerun (inclusive o 2o apply dentro do render) nao duplica.
    status: APPLIED/PROPOSED/ESCALATED ou BLOCKED. Retorna nº de novas acoes."""
    pending_incidents(con)
    actions = action_map(con)
    con.execute("""CREATE TABLE IF NOT EXISTS action (
        incident_id VARCHAR, service_id VARCHAR, tier VARCHAR, signal_name VARCHAR,
        mode VARCHAR, action_id VARCHAR, auto_apply BOOLEAN, status VARCHAR,
        reason VARCHAR, ts TIMESTAMP)""")
    novas = con.sql("""
        SELECT p.incident_id, p.service_id, p.tier, p.signal_name, p.value, p.threshold, p.ts
        FROM pending p
        ANTI JOIN action a ON p.incident_id = a.incident_id
        ORDER BY p.service_id, p.signal_name""").fetchall()
    rows = []
    for incident_id, service_id, tier, signal_name, value, threshold, ts in novas:
        incident = {"incident_id": incident_id, "service_id": service_id,
                    "signal_name": signal_name, "value": value, "threshold": threshold}
        d = decide_action(incident, {"tier": tier}, actions)
        ok, motivo = guardrails(d, {"tier": tier}, actions)
        if not ok:
            status, reason = "BLOCKED", motivo
        elif d["mode"] == "SELF_HEAL" and d["auto_apply"]:
            status, reason = "APPLIED", d["reason"]
        elif d["mode"] == "PR":
            status, reason = "PROPOSED", d["reason"]
        else:
            status, reason = "ESCALATED", d["reason"]
        rows.append((incident_id, service_id, tier, signal_name, d["mode"],
                     d["action_id"], d["auto_apply"], status, reason, ts))
    if rows:
        con.executemany("INSERT INTO action VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)

def operational_metrics(con):
    """Camada de viz. Retorna dict: total_actions, by_status, by_mode, auto_apply_rate, blocked."""
    total = con.sql("SELECT count(*) FROM action").fetchone()[0]
    applied, blocked = con.sql("""
        SELECT count(*) FILTER (status = 'APPLIED'), count(*) FILTER (status = 'BLOCKED')
        FROM action""").fetchone()
    return {
        "total_actions": total,
        "by_status": con.sql("SELECT status, count(*) FROM action GROUP BY status ORDER BY status").fetchall(),
        "by_mode": con.sql("SELECT mode, count(*) FROM action GROUP BY mode ORDER BY mode").fetchall(),
        "auto_apply_rate": (applied / total) if total else 0.0,
        "blocked": blocked,
    }


import rag  # toolkit de retrieval (ja implementado): use load_corpus/build_index/retrieve

SIGNAL_QUERY = {  # query de retrieval por sinal: sinal + vocabulario de dominio dos runbooks
    "cpu.utilization": "runbook cpu.utilization cpu alta saturacao acao recomendada scale_out replicas",
    "mem.utilization": "runbook mem.utilization memoria alta oomkilled acao recomendada restart_pod",
    "http.error_rate": "runbook http.error_rate erros 5xx deploy regressao acao recomendada rollback_deploy",
    "latency.p99": "runbook latency.p99 latencia alta slo timeout acao recomendada scale_out",
}

_RETRIEVERS = {}  # por corpus_dir: {"ret": make_retriever, "memo": {signal: (doc_id, score) | None}}

def _grounding(signal_name, corpus_dir):
    """Top-1 do corpus INTEIRO (make_retriever) para a query do sinal. Guardrail de
    fundamentacao: so runbook com score > 0 fundamenta acao; senao None (nao inventa
    fonte nem aceita politica/post-mortem como base de acao)."""
    r = _RETRIEVERS.get(corpus_dir)
    if r is None:
        r = _RETRIEVERS[corpus_dir] = {"ret": rag.make_retriever(corpus_dir), "memo": {}}
    if signal_name not in r["memo"]:
        hits = r["ret"](SIGNAL_QUERY.get(signal_name, signal_name), 1)
        ok = hits and hits[0][1] > 0 and hits[0][0].startswith("runbooks/")
        r["memo"][signal_name] = hits[0] if ok else None
    return r["memo"][signal_name]

def runbook_for(signal_name, corpus_dir="knowledge"):
    """RAG: o runbook (doc_id) que fundamenta a decisao do agente para o sinal."""
    g = _grounding(signal_name, corpus_dir)
    return g[0] if g else None

def grounded_actions(con, corpus_dir="knowledge"):
    """RAG: anexa a cada acao doc_id + score do runbook que a fundamenta (auditoria)."""
    rows = con.sql("""
        SELECT incident_id, service_id, tier, signal_name, mode, action_id, status
        FROM action ORDER BY service_id, signal_name""").fetchall()
    out = []
    for i, s, t, sig, m, a, st in rows:
        g = _grounding(sig, corpus_dir)
        out.append({"incident_id": i, "service_id": s, "tier": t, "signal_name": sig,
                    "mode": m, "action_id": a, "status": st,
                    "runbook": g[0] if g else None,
                    "runbook_score": round(g[1], 4) if g else None})
    return out


def decision_trace(con, corpus_dir="knowledge", top_k=3):
    """Trilha de auditoria: uma linha por decisao do agente.
    Motivo de negocio -> knowledge/business/compliance_constraints.txt §4:
    "Toda acao autonoma deve emitir notificacao e registrar tokens, custo e latencia
    da decisao." As colunas tokens/cost_usd/latency_ms existem para satisfazer o §4;
    no modo OFFLINE (default) o agente decide com retriever lexical local + heuristica
    deterministica, sem chamada a LLM, entao os tres sao ZERO. Elas seriam preenchidas
    com o uso real da API no modo online (A5X_USE_LLM=1). Retorna nº de linhas.

    Colunas: incident_id, contexto (service/tier/signal), retrieved_docs (top-k com
    score), proposed_action (o que decide_action propos), gate_result + gate_reason
    (guardrail), tokens/cost_usd/latency_ms e final_status (o materializado na `action`).
    """
    actions = action_map(con)
    r = _RETRIEVERS.get(corpus_dir)
    if r is None:
        r = _RETRIEVERS[corpus_dir] = {"ret": rag.make_retriever(corpus_dir), "memo": {}}
    topk = {}
    con.execute("""CREATE OR REPLACE TABLE decision_trace (
        incident_id VARCHAR, service_id VARCHAR, tier VARCHAR, signal_name VARCHAR,
        retrieved_docs VARCHAR, proposed_action VARCHAR,
        gate_result VARCHAR, gate_reason VARCHAR,
        tokens BIGINT, cost_usd DOUBLE, latency_ms DOUBLE, final_status VARCHAR)""")
    src = con.sql("""
        SELECT incident_id, service_id, tier, signal_name, action_id, auto_apply, status
        FROM action ORDER BY service_id, signal_name""").fetchall()
    rows = []
    for incident_id, service_id, tier, signal_name, action_id, auto_apply, status in src:
        if signal_name not in topk:
            topk[signal_name] = r["ret"](SIGNAL_QUERY.get(signal_name, signal_name), top_k)
        docs = "; ".join(f"{d} ({s:.4f})" for d, s in topk[signal_name])
        ok, motivo = guardrails({"action_id": action_id, "auto_apply": auto_apply},
                                {"tier": tier}, actions)
        rows.append((incident_id, service_id, tier, signal_name, docs, action_id,
                     "PASS" if ok else "BLOCK", motivo, 0, 0.0, 0.0, status))
    if rows:
        con.executemany("INSERT INTO decision_trace VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con.sql("SELECT count(*) FROM decision_trace").fetchone()[0]
