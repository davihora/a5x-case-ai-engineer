# -*- coding: utf-8 -*-
"""STARTER (TAKE-HOME) - Agente de Remediacao.
O agente age conforme a criticidade do servico:
  GREEN -> self-heal automatico + notifica | YELLOW -> propoe via PR + notifica | RED -> so escala.
Camadas: backend (decide/guardrails/apply) + modelagem (tabela action) + viz (operational_metrics)."""
import os
import re
import time

import duckdb

SIGNAL_ACTION = {  # fallback por sinal quando nenhum runbook fundamenta a acao
    "cpu.utilization": "act-scaleout", "mem.utilization": "act-restart",
    "http.error_rate": "act-rollback", "latency.p99": "act-clearcache",
}

# Guardrails CONTEXTUAIS (aplicados em apply_decisions; o gate estatico do catalogo
# vive em guardrails()). Parametros com fonte, nao arbitrarios:
BLAST_RADIUS_MIN_SERVICES = 3  # 3+ servicos do MESMO time em breach simultaneo na fila
                               # = indicio de incidente sistemico (causa comum); rollback
                               # automatico por servico multiplicaria o blast radius
RATE_LIMIT_WINDOW_MIN = 30     # janela do proprio corpus: runbook_oom.md manda escalar
                               # se "recorrente em menos de 30 min" — 1 auto-aplicacao
                               # por servico por janela; a recorrencia escala

_ACTION_SCHEMA = {  # schema do ledger `action` (hidratacao valida contra ele)
    "incident_id": "VARCHAR", "service_id": "VARCHAR", "tier": "VARCHAR",
    "signal_name": "VARCHAR", "mode": "VARCHAR", "action_id": "VARCHAR",
    "auto_apply": "BOOLEAN", "status": "VARCHAR", "reason": "VARCHAR", "ts": "TIMESTAMP",
    # fonte NA acao (tarefa 6: "registre a fonte na acao") + latencia da decisao (§4)
    "runbook": "VARCHAR", "runbook_score": "DOUBLE", "latency_ms": "DOUBLE",
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
            else:
                print("aviso: out_action.parquet com schema divergente; ledger descartado "
                      "(incidentes anteriores serao reavaliados)")
        except duckdb.Error:
            print("aviso: out_action.parquet ilegivel; ledger descartado "
                  "(incidentes anteriores serao reavaliados)")

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
      --    leitura em percent fora de [0, 100] e fisicamente impossivel -> lixo;
      --    negativo e impossivel em qualquer unidade do feed (fecha a classe), mas
      --    value NULL e esperado em LOG/TRACE ("nulo esperado nao e defeito") e passa
      SELECT d.* FROM dedup d
      SEMI JOIN service_catalog s ON d.service_id = s.service_id
      WHERE (d.unit <> 'percent' OR d.value BETWEEN 0 AND 100)
        AND (d.value >= 0 OR d.value IS NULL)
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

def decide_action(incident, service, actions, corpus_dir="knowledge"):
    """Decide modo e acao pelo TIER: GREEN->SELF_HEAL(auto), YELLOW->PR(act-openpr),
    RED->ESCALATE(act-page). Tier desconhecido escala por seguranca.
    Em GREEN a acao vem do RUNBOOK recuperado via RAG (tarefa 2: escolha via runbook,
    "function calling" deterministico — parse da secao 'Acao recomendada' mapeado ao
    action_catalog); sem runbook fundamentando, cai no fallback SIGNAL_ACTION. A
    sugestao derivada de conteudo recuperado passa pelo gate deterministico como
    qualquer outra (guardrails decide; o conteudo so sugere)."""
    tier, sig = service["tier"], incident.get("signal_name")
    base = {"incident_id": incident["incident_id"], "service_id": incident["service_id"], "tier": tier}
    if tier == "GREEN":
        aid, fonte = _runbook_action(sig, actions, corpus_dir)
        if aid is None:
            aid, fonte = SIGNAL_ACTION.get(sig), "fallback heuristico por sinal"
        return {**base, "mode": "SELF_HEAL", "action_id": aid, "auto_apply": True,
                "reason": f"GREEN: self-heal automatico ({sig} -> {aid}, {fonte})"}
    if tier == "YELLOW":
        return {**base, "mode": "PR", "action_id": "act-openpr", "auto_apply": False,
                "reason": f"YELLOW: propoe correcao via PR ({sig})"}
    if tier == "RED":
        return {**base, "mode": "ESCALATE", "action_id": "act-page", "auto_apply": False,
                "reason": f"RED: apenas escala ao on-call ({sig})"}
    return {**base, "mode": "ESCALATE", "action_id": "act-page", "auto_apply": False,
            "reason": f"tier desconhecido ({tier}): escala por seguranca ({sig})"}

def guardrails(decision, service, actions):
    """Gate ESTATICO, defesa em profundidade independente do decisor. Tudo vem do
    catalogo (`actions`): bloqueia acao fora da whitelist do tier (allowed_tiers) e
    destrutiva auto-aplicada fora de GREEN. Retorna (ok, motivo). Os guardrails
    CONTEXTUAIS (blast radius, rate limit, fundamentacao) dependem de estado da fila/
    ledger e vivem em apply_decisions — este gate permanece puro e testavel isolado."""
    tier, aid = service["tier"], decision.get("action_id")
    acao = actions.get(aid)
    if acao is None:
        return False, f"acao '{aid}' inexistente no catalogo"
    if tier not in acao["allowed_tiers"]:
        return False, f"{aid} nao permitida no tier {tier} (allowed_tiers={acao['allowed_tiers']})"
    if acao["is_destructive"] and decision.get("auto_apply") and tier != "GREEN":
        return False, f"{aid} e destrutiva: auto-aplicacao proibida fora de GREEN (tier={tier})"
    return True, "ok"

def apply_decisions(con, corpus_dir="knowledge"):
    """Materializa as decisoes na tabela `action` de forma IDEMPOTENTE: anti-join por
    incident_id garante que rerun nao duplica.
    Alem do gate estatico (guardrails), aplica os guardrails CONTEXTUAIS, em ordem:
      1. fundamentacao (fail-closed): self-heal sem runbook que fundamente nao
         auto-aplica -> ESCALATED ("toda acao carrega sua fonte");
      2. blast radius: destrutiva-auto rebaixada a PROPOSED (PR) quando o time do
         servico tem BLAST_RADIUS_MIN_SERVICES+ servicos em breach na fila
         (incidente sistemico: rollback por servico trata sintoma e amplia o dano);
      3. rate limit: 1 auto-aplicacao por servico por janela de RATE_LIMIT_WINDOW_MIN
         min (sobre o ts do sinal, deterministico); a recorrencia -> ESCALATED
         (runbook_oom: "recorrente em menos de 30 min, escalar"). So evento POSTERIOR
         na janela e recorrencia: backfill com ts anterior ao ultimo APPLIED nao conta.
    Registra fonte (runbook + score) e latencia da decisao em cada acao.
    status: APPLIED/PROPOSED/ESCALATED ou BLOCKED. Retorna nº de novas acoes."""
    pending_incidents(con)
    actions = action_map(con)
    con.execute("""CREATE TABLE IF NOT EXISTS action (
        incident_id VARCHAR, service_id VARCHAR, tier VARCHAR, signal_name VARCHAR,
        mode VARCHAR, action_id VARCHAR, auto_apply BOOLEAN, status VARCHAR,
        reason VARCHAR, ts TIMESTAMP, runbook VARCHAR, runbook_score DOUBLE,
        latency_ms DOUBLE)""")
    # blast radius: times com N+ servicos distintos em breach simultaneo na fila
    sistemicos = {t for (t,) in con.execute("""
        SELECT s.team FROM pending p JOIN service_catalog s USING (service_id)
        GROUP BY s.team HAVING count(DISTINCT p.service_id) >= ?""",
        [BLAST_RADIUS_MIN_SERVICES]).fetchall()}
    # rate limit: ultima auto-aplicacao por servico ja materializada no ledger
    ultima_auto = dict(con.execute(
        "SELECT service_id, max(ts) FROM action WHERE status = 'APPLIED' GROUP BY service_id"
    ).fetchall())
    novas = con.execute("""
        SELECT p.incident_id, p.service_id, p.tier, p.signal_name, p.value, p.threshold,
               p.ts, s.team
        FROM pending p
        JOIN service_catalog s USING (service_id)
        ANTI JOIN action a ON p.incident_id = a.incident_id
        ORDER BY p.ts, p.incident_id""").fetchall()  # ordem cronologica: janela do rate limit
    rows = []
    for incident_id, service_id, tier, signal_name, value, threshold, ts, team in novas:
        t0 = time.perf_counter()
        incident = {"incident_id": incident_id, "service_id": service_id,
                    "signal_name": signal_name, "value": value, "threshold": threshold}
        d = decide_action(incident, {"tier": tier}, actions, corpus_dir)
        ok, motivo = guardrails(d, {"tier": tier}, actions)
        g = _grounding(signal_name, corpus_dir)  # fonte da acao (auditoria)
        acao = actions.get(d["action_id"]) or {}
        auto = bool(d["auto_apply"])
        ultima = ultima_auto.get(service_id)  # ts NULL (ledger antigo) nao quebra a janela
        # 0 <=: recorrencia e evento POSTERIOR na janela; delta negativo (incidente
        # backfilled com ts anterior ao ultimo APPLIED) nao e recorrencia
        recente = (auto and ts is not None and ultima is not None
                   and 0 <= (ts - ultima).total_seconds() < RATE_LIMIT_WINDOW_MIN * 60)
        if not ok:
            status, reason, auto = "BLOCKED", motivo, False
        elif auto and g is None:
            status, reason, auto = "ESCALATED", (
                f"sem fundamentacao: nenhum runbook para {signal_name} "
                "(self-heal exige fonte)"), False
        elif auto and acao.get("is_destructive") and team in sistemicos:
            status, reason, auto = "PROPOSED", (
                f"blast radius: time {team} com {BLAST_RADIUS_MIN_SERVICES}+ servicos em "
                "breach (incidente sistemico) -> destrutiva rebaixada a PR"), False
        elif recente:
            status, reason, auto = "ESCALATED", (
                f"rate limit: auto-aplicacao em {service_id} ha menos de "
                f"{RATE_LIMIT_WINDOW_MIN} min (recorrencia escala, cf. runbook_oom)"), False
        elif d["mode"] == "SELF_HEAL" and auto:
            status, reason = "APPLIED", d["reason"]
            if ts is not None:
                ultima_auto[service_id] = ts  # consome a janela do rate limit
        elif d["mode"] == "PR":
            status, reason = "PROPOSED", d["reason"]
        else:
            status, reason = "ESCALATED", d["reason"]
        rows.append((incident_id, service_id, tier, signal_name, d["mode"],
                     d["action_id"], auto, status, reason, ts,
                     g[0] if g else None, round(g[1], 4) if g else None,
                     round((time.perf_counter() - t0) * 1000, 3)))
    if rows:
        con.executemany("INSERT INTO action VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
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

_RETRIEVERS = {}  # por corpus_dir: {"ret": make_retriever, "memo": {signal: (doc_id, score) | None},
                  #                  "docs": {doc_id: text} | None, "acao": {signal: (action_id, fonte) }}

def _corpus(corpus_dir):
    r = _RETRIEVERS.get(corpus_dir)
    if r is None:
        r = _RETRIEVERS[corpus_dir] = {"ret": rag.make_retriever(corpus_dir),
                                       "memo": {}, "docs": None, "acao": {}}
    return r

GROUNDING_MIN_SCORE = 0.30   # piso: score TF-IDF do top-1 runbook. Calibrado no corpus real
                             # (legitimos ~0.55; ruido de palavras genericas ~0.15). score>0
                             # sozinho nao filtrava nada — toda query casa "runbook"/"acao"
                             # com todo doc, entao o fail-closed vivia adormecido.
GROUNDING_MIN_MARGIN = 2.0   # margem: o top-1 runbook tem que vencer o 2o RUNBOOK por este
                             # fator. Legitimos vencem por >=3x (folga). Como o cosseno e
                             # <=1.0 e o runbook legitimo e forte (~0.52), NENHUM doc plantado
                             # consegue vence-lo por 2x (teto ~1.9x) -> corpus envenenado nao
                             # fundamenta acao: cai no fallback/escala. Post-mortem do mesmo
                             # tema perto nao conta (nao fundamenta); so outro runbook e ambiguo.

def _grounding(signal_name, corpus_dir):
    """Top-1 do corpus INTEIRO (make_retriever) para a query do sinal. Guardrail de
    fundamentacao com CONFIANCA: so fundamenta a acao um runbook que (a) seja o top-1
    geral, (b) supere o piso absoluto e (c) vença o 2o runbook por uma margem; senao None
    (fail-closed — nao inventa fonte, nao aceita match fraco/ambiguo nem politica/
    post-mortem como base de acao)."""
    r = _corpus(corpus_dir)
    if signal_name not in r["memo"]:
        # k>1: preciso enxergar o 2o RUNBOOK para medir a margem (nao so o 2o doc)
        hits = r["ret"](SIGNAL_QUERY.get(signal_name, signal_name), 6)
        r["memo"][signal_name] = _fundamento_confiavel(hits)
    return r["memo"][signal_name]

def _fundamento_confiavel(hits):
    """Aplica piso + margem ao ranking. Retorna (doc_id, score) do top-1 se ele
    fundamenta com confianca; None caso contrario (match fraco ou ambiguo)."""
    if not hits:
        return None
    doc, score = hits[0]
    if not doc.startswith("runbooks/") or score < GROUNDING_MIN_SCORE:
        return None
    prox_runbook = next((s for d, s in hits[1:] if d.startswith("runbooks/") and s > 0), 0.0)
    if prox_runbook and score < GROUNDING_MIN_MARGIN * prox_runbook:
        return None
    return hits[0]

def _runbook_action(signal_name, actions, corpus_dir="knowledge"):
    """Function calling deterministico sobre o runbook recuperado: parse da
    'Acao primaria: **<action_name>**' SOMENTE dentro da secao 'Acao recomendada'
    do runbook top-1 (texto fora da secao designada nao vira acao), mapeada a um
    action_id EXISTENTE no catalogo recebido — a "tool" so aceita acoes do repertorio
    e o gate deterministico valida o resto. Retorna (action_id, fonte) ou (None, None)
    quando nao ha runbook fundamentando ou a acao nao esta no catalogo.
    O memo por corpus guarda (action_name, doc_id); o mapeamento nome->id e refeito
    por chamada contra o `actions` recebido (catalogos diferentes nao se contaminam)."""
    r = _corpus(corpus_dir)
    if signal_name not in r["acao"]:
        nome_doc = (None, None)
        g = _grounding(signal_name, corpus_dir)
        if g:
            if r["docs"] is None:
                r["docs"] = {d["doc_id"]: d["text"] for d in rag.load_corpus(corpus_dir)}
            secao = re.search(r"##\s*Acao recomendada\s*\n(.*?)(?=\n##|\Z)",
                              r["docs"].get(g[0], ""), re.S)
            m = secao and re.search(r"Acao primaria:\s*\*\*([a-z0-9_]+)\*\*", secao.group(1))
            if m:
                nome_doc = (m.group(1), g[0])
        r["acao"][signal_name] = nome_doc
    nome, doc = r["acao"][signal_name]
    if nome is None:
        return None, None
    por_nome = {a["action_name"]: a["action_id"] for a in actions.values()}
    aid = por_nome.get(nome)
    return (aid, f"acao recomendada por {doc}") if aid else (None, None)

def runbook_for(signal_name, corpus_dir="knowledge"):
    """RAG: o runbook (doc_id) que fundamenta a decisao do agente para o sinal."""
    g = _grounding(signal_name, corpus_dir)
    return g[0] if g else None

def grounded_actions(con):
    """RAG: a fonte (runbook + score) registrada NA propria acao (tarefa 6),
    lida do ledger — e a fonte da epoca da decisao, nao um recomputo."""
    rows = con.sql("""
        SELECT incident_id, service_id, tier, signal_name, mode, action_id, status,
               runbook, runbook_score
        FROM action ORDER BY service_id, signal_name""").fetchall()
    return [{"incident_id": i, "service_id": s, "tier": t, "signal_name": sig,
             "mode": m, "action_id": a, "status": st, "runbook": rb, "runbook_score": sc}
            for i, s, t, sig, m, a, st, rb, sc in rows]


def decision_trace(con, corpus_dir="knowledge", top_k=3):
    """Trilha de auditoria: uma linha por decisao do agente.
    Motivo de negocio -> knowledge/business/compliance_constraints.txt §4:
    "Toda acao autonoma deve emitir notificacao e registrar tokens, custo e latencia
    da decisao." tokens/cost_usd = 0 porque NAO ha chamada a LLM no caminho de decisao
    (decisor deterministico por design; um decisor LLM plugado atras do gate — ver
    DESIGN.md — preencheria ambos). latency_ms e MEDIDA por decisao em apply_decisions
    (retrieval + decisao + gates) e lida do ledger. gate_result/gate_reason sao a
    PROJECAO do que foi materializado na epoca da decisao (action.status/reason), nao
    um recomputo com o catalogo atual — imune a drift do catalogo entre runs.
    Retorna nº de linhas.

    Colunas: incident_id, contexto (service/tier/signal), retrieved_docs (top-k com
    score), proposed_action, gate_result + gate_reason (inclui motivos dos guardrails
    contextuais), tokens/cost_usd/latency_ms e final_status (o materializado na `action`).
    """
    r = _corpus(corpus_dir)
    topk = {}
    con.execute("""CREATE OR REPLACE TABLE decision_trace (
        incident_id VARCHAR, service_id VARCHAR, tier VARCHAR, signal_name VARCHAR,
        retrieved_docs VARCHAR, proposed_action VARCHAR,
        gate_result VARCHAR, gate_reason VARCHAR,
        tokens BIGINT, cost_usd DOUBLE, latency_ms DOUBLE, final_status VARCHAR)""")
    src = con.sql("""
        SELECT incident_id, service_id, tier, signal_name, action_id, status, reason,
               latency_ms
        FROM action ORDER BY service_id, signal_name""").fetchall()
    rows = []
    for incident_id, service_id, tier, signal_name, action_id, status, reason, lat in src:
        if signal_name not in topk:
            topk[signal_name] = r["ret"](SIGNAL_QUERY.get(signal_name, signal_name), top_k)
        docs = "; ".join(f"{d} ({s:.4f})" for d, s in topk[signal_name])
        rows.append((incident_id, service_id, tier, signal_name, docs, action_id,
                     "BLOCK" if status == "BLOCKED" else "PASS", reason,
                     0, 0.0, lat, status))
    if rows:
        con.executemany("INSERT INTO decision_trace VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return con.sql("SELECT count(*) FROM decision_trace").fetchone()[0]
