# -*- coding: utf-8 -*-
"""Camada 3 (visualizacao) - console de acoes do agente. Camada SO de leitura:
quem materializa `action` e `decision_trace` e o run.py (backend); render() apenas
consulta as tabelas e gera o HTML."""
import html

from agent.build import operational_metrics

def esc(x):
    """Escape de tudo que vem de dados (ids, sinais, docs do corpus): o threat model
    das evals assume atacante capaz de plantar arquivo em knowledge/, e o doc_id
    (nome do arquivo) flui para o HTML via retrieved_docs."""
    return html.escape(str(x), quote=True)

CSS = """body{font-family:Lato,Arial,sans-serif;color:#10212B;margin:24px}
h1{color:#075484;margin:0}.sub{color:#06436A;font-weight:700;margin:0 0 16px}
h3{color:#075484;margin:22px 0 4px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.card{border:1px solid #E6EEF3;border-top:3px solid #075484;border-radius:8px;padding:12px 16px;min-width:140px}
.card .k{font-size:26px;font-weight:900;color:#075484}.card .l{font-size:11px;color:#5b6b75}
table{border-collapse:collapse;width:100%;margin:8px 0 20px;font-size:13px}
th{background:#075484;color:#fff;text-align:left;padding:7px 10px}td{padding:6px 10px;border-bottom:1px solid #E6EEF3}
td.mono{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#5b6b75}
.note{font-size:12px;color:#5b6b75;margin:4px 0 12px;max-width:900px}
.PASS{color:#1f9d55;font-weight:700}.BLOCK{color:#c0392b;font-weight:700}
.APPLIED{color:#1f9d55;font-weight:700}.PROPOSED{color:#b7791f;font-weight:700}.ESCALATED{color:#c0392b;font-weight:700}.BLOCKED{color:#7a8893;font-weight:700}"""


def _eval_cards():
    """Cards de evals a partir de agent.evals.run() (offline/deterministico por default).
    Isolado em try/except: se a avaliacao falhar, o dashboard principal ainda renderiza."""
    try:
        from agent import evals
        econ = evals.run()
        def pct(metrica, dim="TOTAL"):
            row = econ.execute("SELECT valor FROM eval_result WHERE metrica=? AND dimensao=?",
                               [metrica, dim]).fetchone()
            return f"{int(round(row[0]*100))}%" if row else "-"
        return [("Decisao (acao)", pct("acuracia_action")),
                ("Tier-gate (mode)", pct("acuracia_mode")),
                ("Guardrail recall", pct("recall_adversarial")),
                ("Retrieval hit@1", pct("hit_at_1"))]
    except Exception as e:  # noqa: BLE001 - dashboard nao pode quebrar por causa da eval
        return [("Evals indisponivel", type(e).__name__)]


def _eficacia_historica(con):
    """Referencia HISTORICA por acao (incident_log, 203 casos): taxa de sucesso e MTTR.
    Nao entra na decisao (130/203 violam a politica de tiers — ver DESIGN.md, cortes);
    no console responde "o agente esta ajudando?" com a regua do proprio historico."""
    try:
        return con.sql("""
            SELECT chosen_action, count(*) AS incidentes,
                   ROUND(100.0 * SUM(CASE WHEN action_outcome = 'SUCCESS' THEN 1 ELSE 0 END)
                         / count(*), 1) AS taxa_sucesso_pct,
                   ROUND(AVG(mttr_min), 1) AS mttr_medio_min
            FROM incident_log GROUP BY chosen_action ORDER BY chosen_action""").fetchall()
    except Exception:  # noqa: BLE001 - con sem incident_log: secao apenas some
        return []


def render(con, out_path="dashboard.html"):
    """Renderiza o console a partir das tabelas ja materializadas pelo run.py
    (`action` via apply_decisions, `decision_trace` via decision_trace)."""
    m = operational_metrics(con)
    n_trace = con.sql("SELECT count(*) FROM decision_trace").fetchone()[0]

    cards = "".join(f'<div class="card"><div class="k">{v}</div><div class="l">{l}</div></div>'
        for l, v in [("Acoes totais", m["total_actions"]),
                     ("Auto-aplicadas", f'{int(m["auto_apply_rate"]*100)}%'),
                     ("Bloqueadas (guardrail)", m["blocked"])])
    st = "".join(f'<tr><td class="{esc(k)}">{esc(k)}</td><td>{n}</td></tr>' for k, n in m["by_status"])
    md = "".join(f"<tr><td>{esc(k)}</td><td>{n}</td></tr>" for k, n in m["by_mode"])
    # amostra POR TIER (nao um LIMIT global): a demo mostra os 3 modos, inclusive PR
    recent = con.sql("""SELECT service_id, tier, mode, action_id, status FROM (
        SELECT *, ROW_NUMBER() OVER (PARTITION BY tier ORDER BY ts DESC, incident_id) rn
        FROM action) WHERE rn <= 7 ORDER BY tier, ts DESC""").fetchall()
    rows = "".join(f'<tr><td>{esc(a)}</td><td>{esc(b)}</td><td>{esc(c)}</td><td>{esc(d)}</td><td class="{esc(e)}">{esc(e)}</td></tr>' for a,b,c,d,e in recent)

    # cards de avaliacao (novos)
    ev = "".join(f'<div class="card"><div class="k">{v}</div><div class="l">{l}</div></div>'
                 for l, v in _eval_cards())
    # agregados de custo/latencia (§4) — zerados no modo offline, mas exibidos p/ auditoria
    tok, cost, lat, ndec = con.sql("""
        SELECT COALESCE(SUM(tokens),0), COALESCE(SUM(cost_usd),0.0),
               COALESCE(AVG(latency_ms),0.0), count(*) FROM decision_trace""").fetchone()
    cl = "".join(f'<div class="card"><div class="k">{v}</div><div class="l">{l}</div></div>'
                 for l, v in [("Tokens (0 por design: sem LLM na decisao)", tok),
                              ("Custo US$ (0 por design: sem LLM na decisao)", f"{cost:.4f}"),
                              ("Latencia media (ms)", f"{lat:.1f}"), ("Decisoes rastreadas", ndec)])
    # eficacia historica (incident_log): a regua de "o agente esta ajudando?"
    ef = "".join(f'<tr><td>{esc(a)}</td><td>{n}</td><td>{tx}%</td><td>{mt}</td></tr>'
                 for a, n, tx, mt in _eficacia_historica(con))
    # tabela decision_trace (uma linha por decisao)
    tr = con.sql("""SELECT incident_id, tier, signal_name, proposed_action, gate_result,
                           retrieved_docs, tokens, cost_usd, latency_ms, final_status
                    FROM decision_trace ORDER BY tier, incident_id LIMIT 30""").fetchall()
    trace = "".join(
        f'<tr><td>{esc(i)}</td><td>{esc(t)}</td><td>{esc(sg)}</td><td>{esc(pa)}</td>'
        f'<td class="{esc(gr)}">{esc(gr)}</td><td class="mono">{esc(docs)}</td><td>{tk}</td>'
        f'<td>{co:.4f}</td><td>{la:.1f}</td><td class="{esc(fs)}">{esc(fs)}</td></tr>'
        for i, t, sg, pa, gr, docs, tk, co, la, fs in tr)

    html = f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>
    <h1>A5X &middot; Console de Acoes</h1><div class=sub>Agente de Remediacao &middot; Camada de Visualizacao</div>
    <div class=cards>{cards}</div>
    <div class=note>BLOCKED = 0 no feed e corpus atuais: o decisor so propoe acoes da whitelist
    do tier — a prova do gate esta nos 88 casos adversariais do <code>make eval</code> (recall 100%).
    Os guardrails contextuais (blast radius, rate limit, fundamentacao) aparecem em
    status/motivo na trilha abaixo.</div>
    <h3>Por status</h3><table><tr><th>Status</th><th>Qtd</th></tr>{st}</table>
    <h3>Por modo</h3><table><tr><th>Modo</th><th>Qtd</th></tr>{md}</table>
    <h3>Acoes recentes</h3><table><tr><th>Servico</th><th>Tier</th><th>Modo</th><th>Acao</th><th>Status</th></tr>{rows}</table>
    <h3>Avaliacao offline (evals)</h3><div class=cards>{ev}</div>
    <h3>Eficacia historica por acao (incident_log)</h3>
    <table><tr><th>Acao</th><th>Incidentes</th><th>Taxa de sucesso</th><th>MTTR medio (min)</th></tr>{ef}</table>
    <div class=note>Referencia do historico (203 incidentes): a regua contra a qual a eficiencia
    do agente sera medida. O historico NAO alimenta a decisao — 130/203 acoes historicas violam
    a politica de tiers do catalogo (ver DESIGN.md, cortes por prioridade).</div>
    <h3>Custo &amp; latencia por decisao (compliance &sect;4)</h3><div class=cards>{cl}</div>
    <div class=note>tokens/custo = 0: nao ha chamada a LLM no caminho de decisao (decisor
    deterministico; o LLM entra apenas como juiz opcional das evals). Latencia MEDIDA por
    decisao (retrieval + decisao + gates) no momento do apply.</div>
    <h3>Trilha de decisao &middot; decision_trace ({n_trace} decisoes)</h3>
    <table><tr><th>Incidente</th><th>Tier</th><th>Sinal</th><th>Acao proposta</th><th>Gate</th>
    <th>Docs recuperados (score)</th><th>Tokens</th><th>Custo US$</th><th>Latencia ms</th><th>Status final</th></tr>{trace}</table>
    </body></html>"""
    open(out_path, "w").write(html); return out_path
