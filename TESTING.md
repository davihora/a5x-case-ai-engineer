# Roteiro de teste — Agente de Remediacao Autonoma (A5X)

Prova de "clone -> roda" em qualquer maquina. O caminho **offline** (default) nao
precisa de rede nem de credenciais; o caminho **ponta-a-ponta com LLM** e opcional e
so e usado pelo *judge* das evals.

## Pre-requisitos

- `git` e `python3` >= 3.10 (o `>=3.9` funciona, mas 3.10+ e o recomendado).
- `make`.
- `uv` e **opcional**: se estiver instalado, o `make setup` usa o `uv.lock` (build
  reproduzivel); se nao estiver, cai para `python -m venv` + `pip install -r requirements.txt`.
  Nenhuma dependencia precisa ser instalada "a mao" antes — o `make setup` faz tudo.

## 1. Clonar

```bash
git clone git@github.com:davihora/a5x-case-ai-engineer.git
cd a5x-case-ai-engineer
```

## 2. Setup automatico (cria o venv e instala as deps pinadas)

```bash
make setup
```

Isso cria `.venv/` e instala as 4 deps do projeto (duckdb, pytest, pypdf, python-docx)
com os pins exatos. **Nao e preciso ativar o venv** — os alvos `make test/run/eval`
detectam `.venv/bin/python` automaticamente.

> Alternativa manual (o que o README mostra), se preferir gerir o venv voce mesmo:
> ```bash
> python3 -m venv .venv && source .venv/bin/activate
> pip install -r requirements.txt
> ```

## 3. Rodar os specs

```bash
make test
```

Esperado: **28 passed**. Cobre backend (decide/guardrail/apply idempotente — os 6
testes-spec do starter, byte-intactos), limpeza do feed sujo, acao GREEN derivada do
runbook, guardrails contextuais (fundamentacao fail-closed, blast radius, rate limit
incluindo backfill com ts anterior) e as evals (tier-gate, recall do guardrail,
runbook envenenado recusado na fundamentacao pelo gate de confianca (piso+margem), anti-vazamento do
judge, delimitacao do conteudo recuperado no prompt e parse estrito do veredito).

## 4. Rodar o pipeline ponta-a-ponta

```bash
make run
```

Gera `dashboard.html` (abra no navegador) e persiste `out_action.parquet` +
`out_decision_trace.parquet`. Saida esperada no console:

```
novas acoes: 72
metricas: {'total_actions': 72, 'by_status': [('APPLIED', 18), ('ESCALATED', 21), ('PROPOSED', 33)],
           'by_mode': [('ESCALATE', 20), ('PR', 25), ('SELF_HEAL', 27)], 'auto_apply_rate': 0.25, 'blocked': 0}
acoes fundamentadas (RAG): 72 | exemplo: {... 'runbook': 'runbooks/runbook_cpu_saturation.md', ...}
dashboard: dashboard.html
decision_trace: 72
```

Por que APPLIED (18) < SELF_HEAL (27): os guardrails **contextuais** agiram no feed
real — 8 rollbacks destrutivos em GREEN foram rebaixados a PROPOSED por *blast radius*
(3+ servicos do mesmo time em breach simultaneo = incidente sistemico) e 1 self-heal
foi ESCALATED por *rate limit* (2a auto-aplicacao no mesmo servico em <30min). Os
motivos ficam em `reason` no ledger e em `gate_reason` no decision_trace.

Rode `make run` **duas vezes**: na segunda deve imprimir `novas acoes: 0`
(idempotencia entre execucoes: os mesmos incident_ids caem no anti-join do ledger).

### Conferir a trilha de auditoria (decision_trace)

O dashboard tem uma secao **decision_trace**; para cruzar contra os dados:

```bash
.venv/bin/python -c "import duckdb; \
print(duckdb.sql(\"SELECT incident_id, tier, proposed_action, gate_result, tokens, cost_usd, latency_ms, final_status FROM 'out_decision_trace.parquet' ORDER BY tier, incident_id LIMIT 10\"))"
```

Colunas: `incident_id`, `retrieved_docs` (top-3 com score), `proposed_action`,
`gate_result`+`gate_reason` (projecao do que foi materializado na epoca da decisao,
incluindo motivos de blast radius/rate limit), `tokens`/`cost_usd` (0 — nao ha LLM no
caminho de decisao), `latency_ms` (MEDIDA por decisao: retrieval + decisao + gates;
a 1a decisao paga a indexacao do corpus — dezenas a centenas de ms, varia por
maquina — e as demais ficam sub-ms) e `final_status`.
A fonte tambem fica NA acao: `out_action.parquet` tem `runbook`, `runbook_score` e
`latency_ms` por linha (compliance §4).

## 5. Scorecard das evals (offline)

```bash
make eval
```

Imprime o scorecard e sai com codigo **0** (APROVADO) ou **1** se algum threshold
romper. Referencia atual: decisao(acao) 88%, tier-gate(mode) 100%, guardrail recall
100% (88 adversariais, incl. o runbook envenenado recusado na fundamentacao + a destrutiva injetada barrada no tier-gate),
retrieval hit@1 68%, faithfulness do judge 100% (deterministico = self-consistency;
sinal independente so com o LLM-judge do passo 6). Os thresholds
de acuracia/retrieval sao *pisos de regressao* fixados no baseline corrente (regressao
de 1 caso reprova); os invariantes de seguranca (mode, auto_apply, recall adversarial)
sao 1.00 duros. Determinismo: duas execucoes geram scorecards identicos. Veredicto
POR CASO do judge fica na tabela `judge_result` (casos reprovados sao impressos ao
final do scorecard).

## 6. (Opcional) Fluxo ponta-a-ponta com LLM judge — precisa de credencial e rede

Por design o agente e 100% offline (retriever lexical + heuristica deterministica); o
LLM entra **apenas** como *juiz* alternativo das evals, e so e ativado com as DUAS
condicoes: a flag `A5X_USE_LLM=1` **e** a variavel `ANTHROPIC_API_KEY`. A presenca da
chave sozinha nunca ativa o LLM.

```bash
export ANTHROPIC_API_KEY="sk-ant-...."   # NAO comite; use uma chave sua, valida
export A5X_USE_LLM=1
make eval
```

Isso faz ~25 chamadas ao modelo `claude-haiku-4-5` (uma por caso golden), julgando cada
decisao pela rubrica (tier_compliance / faithfulness / safety). No scorecard, a linha
`llm_judge_ativo` vira 1 e a concordancia com o golden vira **INFO** (sem threshold —
a saida deixa de ser deterministica de proposito). O rodape do scorecard lista os casos
reprovados pelo juiz (per-caso, da tabela `judge_result`) e o total de tokens/chamadas
do LLM (compliance §4). Volte ao offline com `unset A5X_USE_LLM`.

**Rede:** o POST vai para `https://api.anthropic.com/v1/messages`. Redes corporativas
com DLP/CASB costumam **bloquear** POSTs de chave para provedores de LLM e responder
`permission_error: "Access restricted by network policy"`. Se isso ocorrer, rode este
passo fora da rede corporativa (hotspot do celular ou rede pessoal). O bloqueio nao
afeta os passos 1–5, que sao offline.

**Seguranca:** nunca comite a chave. Ela so vive na variavel de ambiente do seu shell.
Se uma chave vazar, revogue em https://console.anthropic.com e gere outra.

## 7. CI (prova publica de "clone -> roda")

O push dispara `.github/workflows/ci.yml`: setup-uv -> `uv sync` (fallback pip) ->
`pytest -q` -> `python -m agent.evals` (scorecard como gate: exit 1 reprova o build) ->
`python run.py` -> assert de `dashboard.html` -> upload do dashboard como **artifact**.
O badge de status fica no topo do `README.md`; o `dashboard.html` gerado pelo CI e
baixavel na aba **Actions** de cada run. O CI e offline — nao usa a chave.

## Limpeza

```bash
make clean   # remove .venv, caches e saidas geradas
```
