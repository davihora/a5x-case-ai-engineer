# -*- coding: utf-8 -*-
"""EDA dos 5 parquets de data/ (DuckDB). Reproduzivel e deterministico:
toda query tem ORDER BY explicito, percentuais arredondados no SQL e impressao
propria (independente da largura do terminal). Rodar 2x gera saida identica.

Uso: python scripts/eda.py   (de qualquer diretorio, sem argumentos)

Cada numero e impresso ao lado da query que o gerou, para citacao no DESIGN.md.
O baseline usa o SIGNAL_ACTION importado de agent.build (a heuristica real,
nao uma copia)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import duckdb
from agent.build import SIGNAL_ACTION, load

TABLES = ["service_catalog", "otel_signal", "action_catalog", "incident_log", "eval_golden"]


def show(con, titulo, sql):
    """Imprime a query e o resultado em formato estavel (uma linha por registro)."""
    print(f"\n### {titulo}")
    print("```sql")
    print(sql.strip())
    print("```")
    rel = con.sql(sql)
    cols, rows = rel.columns, rel.fetchall()
    print(" | ".join(cols))
    for r in rows:
        print(" | ".join("NULL" if v is None else str(v) for v in r))
    print(f"-- {len(rows)} linha(s)")


def main():
    con = duckdb.connect()
    load(con, data_dir=str(ROOT / "data"))

    print("# EDA - dataset de observabilidade A5X (DuckDB)")

    # ------------------------------------------------------------------ 1. schema e contagem
    print("\n## 1. Schema e contagem por tabela")
    show(con, "Linhas por tabela", """
SELECT 'action_catalog'  AS tabela, count(*) AS linhas FROM action_catalog  UNION ALL
SELECT 'eval_golden',              count(*)           FROM eval_golden     UNION ALL
SELECT 'incident_log',             count(*)           FROM incident_log    UNION ALL
SELECT 'otel_signal',              count(*)           FROM otel_signal     UNION ALL
SELECT 'service_catalog',          count(*)           FROM service_catalog
ORDER BY tabela
""")
    for t in TABLES:
        show(con, f"Schema de {t}", f"""
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = '{t}'
ORDER BY ordinal_position
""")

    # ------------------------------------------------------------------ 2. sujeira do otel_signal
    print("\n## 2. Sujeira do otel_signal")
    show(con, "Resumo da sujeira (linhas afetadas por tipo)", """
SELECT
  (SELECT count(*) FROM otel_signal)                                  AS linhas_total,
  (SELECT count(*) - count(DISTINCT signal_id) FROM otel_signal)      AS duplicatas_excedentes,
  (SELECT count(*) FROM otel_signal o
     LEFT JOIN service_catalog s USING (service_id)
     WHERE s.service_id IS NULL)                                      AS sinais_fora_catalogo,
  (SELECT count(*) FROM otel_signal
     WHERE unit = 'percent' AND (value < 0 OR value > 100))           AS leituras_impossiveis_percent
""")
    show(con, "Duplicatas de signal_id (e se as copias divergem em conteudo)", """
SELECT
  count(*)                                            AS linhas_total,
  count(DISTINCT signal_id)                           AS ids_distintos,
  count(*) - count(DISTINCT signal_id)                AS duplicatas_excedentes,
  (SELECT count(*) FROM (
     SELECT signal_id FROM otel_signal
     GROUP BY signal_id HAVING count(*) > 1))         AS ids_duplicados,
  (SELECT count(*) FROM (
     SELECT signal_id FROM otel_signal
     GROUP BY signal_id
     HAVING count(DISTINCT (ts, service_id, signal_kind, name,
                            value, unit, attributes)) > 1))         AS ids_duplicados_divergentes
FROM otel_signal
""")
    show(con, "Sinais de servicos fora do service_catalog", """
SELECT o.service_id, count(*) AS sinais
FROM otel_signal o
LEFT JOIN service_catalog s USING (service_id)
WHERE s.service_id IS NULL
GROUP BY o.service_id
ORDER BY o.service_id
""")
    show(con, "Leituras impossiveis em percent (fora de [0, 100])", """
SELECT COALESCE(name, 'TOTAL') AS metrica,
       count(*) AS leituras, min(value) AS valor_min, max(value) AS valor_max
FROM otel_signal
WHERE unit = 'percent' AND (value < 0 OR value > 100)
GROUP BY ROLLUP(name)
ORDER BY name NULLS LAST
""")

    # ------------------------------------------------------------------ 3. distribuicao de tiers
    print("\n## 3. Distribuicao de tiers")
    show(con, "Servicos por tier (service_catalog)", """
SELECT tier, count(*) AS servicos,
       ROUND(100.0 * count(*) / SUM(count(*)) OVER (), 1) AS pct
FROM service_catalog
GROUP BY tier
ORDER BY tier
""")

    # ------------------------------------------------------------------ 4. baseline SIGNAL_ACTION vs eval_golden
    print("\n## 4. Baseline da heuristica SIGNAL_ACTION vs eval_golden")
    ddl = (
        "-- materializa agent.build.SIGNAL_ACTION para consulta em SQL\n"
        "CREATE OR REPLACE TABLE heuristica (signal_name VARCHAR, acao_prevista VARCHAR);\n"
        "INSERT INTO heuristica VALUES\n"
        + ",\n".join(f"  ('{s}', '{a}')" for s, a in sorted(SIGNAL_ACTION.items()))
        + ";"
    )
    con.execute(ddl)
    print("\n### Heuristica avaliada (agent.build.SIGNAL_ACTION)")
    print("```sql")
    print(ddl)
    print("```")
    show(con, "Mapeamento sinal -> acao da heuristica", """
SELECT signal_name, acao_prevista FROM heuristica ORDER BY signal_name
""")
    show(con, "Acuracia por tier (heuristica ignora tier)", """
SELECT COALESCE(g.service_tier, 'TOTAL') AS tier,
       count(*)                                                          AS casos,
       SUM(CASE WHEN h.acao_prevista = g.expected_action THEN 1 ELSE 0 END) AS acertos,
       ROUND(100.0 * SUM(CASE WHEN h.acao_prevista = g.expected_action THEN 1 ELSE 0 END)
             / count(*), 1)                                              AS acuracia_pct
FROM eval_golden g
LEFT JOIN heuristica h USING (signal_name)
GROUP BY ROLLUP(g.service_tier)
ORDER BY g.service_tier NULLS LAST
""")
    show(con, "Detalhe dos acertos/erros (previsto vs esperado)", """
SELECT g.service_tier AS tier, g.signal_name, h.acao_prevista, g.expected_action,
       count(*) AS casos,
       CASE WHEN h.acao_prevista = g.expected_action THEN 'ACERTO' ELSE 'ERRO' END AS resultado
FROM eval_golden g
LEFT JOIN heuristica h USING (signal_name)
GROUP BY ALL
ORDER BY tier, g.signal_name, g.expected_action
""")

    # ------------------------------------------------------------------ 5. taxas de sucesso no incident_log
    print("\n## 5. Taxas de sucesso por (root_cause, chosen_action) no incident_log")
    show(con, "Outcomes globais (NOOP conta como nao-sucesso)", """
SELECT action_outcome, count(*) AS incidentes,
       ROUND(100.0 * count(*) / SUM(count(*)) OVER (), 1) AS pct
FROM incident_log
GROUP BY action_outcome
ORDER BY action_outcome
""")
    show(con, "Taxa de sucesso por (root_cause_label, chosen_action)", """
SELECT root_cause_label, chosen_action, count(*) AS incidentes,
       SUM(CASE WHEN action_outcome = 'SUCCESS' THEN 1 ELSE 0 END) AS sucessos,
       ROUND(100.0 * SUM(CASE WHEN action_outcome = 'SUCCESS' THEN 1 ELSE 0 END)
             / count(*), 1)                                        AS taxa_sucesso_pct,
       ROUND(AVG(mttr_min), 1)                                     AS mttr_medio_min
FROM incident_log
GROUP BY root_cause_label, chosen_action
ORDER BY root_cause_label, chosen_action
""")
    show(con, "Cardinalidade root_cause -> chosen_action (0 linhas = mapeamento 1:1)", """
SELECT root_cause_label, count(DISTINCT chosen_action) AS acoes_distintas
FROM incident_log
GROUP BY root_cause_label
HAVING count(DISTINCT chosen_action) > 1
ORDER BY root_cause_label
""")
    show(con, "Historico vs politica de tiers (acoes que violam allowed_tiers do catalogo)", """
SELECT CASE WHEN list_contains(a.allowed_tiers, s.tier)
            THEN 'respeita politica' ELSE 'VIOLA politica' END AS situacao,
       count(*) AS incidentes,
       ROUND(100.0 * count(*) / SUM(count(*)) OVER (), 1) AS pct
FROM incident_log i
JOIN action_catalog a ON i.chosen_action = a.action_id
JOIN service_catalog s ON i.service_id = s.service_id
GROUP BY 1
ORDER BY 1
""")

    # ------------------------------------------------------------------ 6. join com LOGs
    print("\n## 6. O join com LOGs desambiguaria a causa-raiz? (evidencia do corte)")
    print("A acuracia de acao GREEN e 6/9 porque a query so conhece o sinal, nao a causa.")
    print("Antes de propor 'join com LOGs' como evolucao, mede-se o que ele daria AQUI.")
    show(con, "Causa do golden presente em ALGUM log do servico? (por tier)", """
SELECT g.service_tier, count(*) AS casos,
       SUM(CASE WHEN EXISTS (
             SELECT 1 FROM otel_signal l
             WHERE l.service_id = g.service_id AND l.signal_kind = 'LOG'
               AND l.attributes.error_type = g.root_cause_label)
           THEN 1 ELSE 0 END) AS causa_presente_nos_logs
FROM eval_golden g
GROUP BY g.service_tier
ORDER BY g.service_tier
""")
    show(con, "GREEN: acao via error_type MODAL dos LOGs (o que o join daria) vs esperado", """
WITH modal AS (
  SELECT service_id, attributes.error_type AS error_type,
         ROW_NUMBER() OVER (PARTITION BY service_id
                            ORDER BY count(*) DESC, attributes.error_type) AS rn
  FROM otel_signal WHERE signal_kind = 'LOG'
  GROUP BY service_id, attributes.error_type
), causa_acao AS (  -- mapa 1:1 do historico (secao 5): causa -> acao
  SELECT root_cause_label, MIN(chosen_action) AS acao FROM incident_log GROUP BY 1
)
SELECT g.case_id, g.service_id, g.root_cause_label, m.error_type AS log_modal,
       c.acao AS acao_via_log, g.expected_action,
       COALESCE(c.acao = g.expected_action, FALSE) AS acerto
FROM eval_golden g
LEFT JOIN modal m ON m.service_id = g.service_id AND m.rn = 1
LEFT JOIN causa_acao c ON c.root_cause_label = m.error_type
WHERE g.service_tier = 'GREEN'
ORDER BY g.case_id
""")
    show(con, "GREEN: teto com ORACULO de causa-raiz (causa verdadeira -> acao 1:1 do historico)", """
WITH causa_acao AS (
  SELECT root_cause_label, MIN(chosen_action) AS acao FROM incident_log GROUP BY 1
)
SELECT count(*) AS casos,
       SUM(CASE WHEN c.acao = g.expected_action THEN 1 ELSE 0 END) AS acertos_oraculo
FROM eval_golden g
JOIN causa_acao c USING (root_cause_label)
WHERE g.service_tier = 'GREEN'
""")
    print("\nLeitura: o join modal acerta 2/9 (pior que os 6/9 atuais) — os error_type de LOG")
    print("deste feed nao correlacionam com a causa do golden (presente em so 3/9 GREEN).")
    print("Mesmo um oraculo perfeito daria 8/9: gold-19 (ConnectionRefused) espera act-restart,")
    print("nao o act-failover destrutivo do runbook. Ficar signal-only e decisao MEDIDA.")


if __name__ == "__main__":
    main()
