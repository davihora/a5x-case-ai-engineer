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


if __name__ == "__main__":
    main()
