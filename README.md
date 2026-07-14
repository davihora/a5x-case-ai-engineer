# Agente de Remediacao Autonoma (A5X take-home)

[![CI](https://github.com/davihora/a5x-case-ai-engineer/actions/workflows/ci.yml/badge.svg)](https://github.com/davihora/a5x-case-ai-engineer/actions/workflows/ci.yml)

Agente que escuta a fila de incidentes da telemetria OTel e age conforme o tier do
servico — **GREEN** self-heal automatico, **YELLOW** propoe via Pull Request, **RED**
so escala — com guardrails deterministicos (whitelist por tier, destrutiva, blast
radius, rate limit), idempotencia de acao, fundamentacao via RAG com gate de confianca
(a acao GREEN vem do runbook recuperado, so se ele vencer o 2o por >=2x) e trilha de
auditoria com latencia por decisao.

Arquitetura de 3 camadas (backend + modelagem + visualizacao): ver **[DESIGN.md](DESIGN.md)**
(diagrama, decisoes, trade-offs, seguranca de IA e cortes por prioridade).

## Quickstart

```bash
make setup   # cria .venv e instala deps pinadas (uv se houver; senao venv+pip)
make test    # 28 passed (6 specs do starter intactos + 22 do candidato)
make run     # pipeline ponta-a-ponta -> dashboard.html (abra no navegador)
make run     # de novo: "novas acoes: 0" (idempotencia)
make eval    # scorecard offline (golden + 88 adversariais) -> APROVADO, exit 0
```

Saida esperada do `make run`: 72 incidentes de 7.116 sinais; 18 APPLIED / 33 PROPOSED /
21 ESCALATED (8 rollbacks rebaixados por blast radius, 1 recorrencia por rate limit).

Tudo roda **offline e deterministico**. `A5X_USE_LLM=1` + `ANTHROPIC_API_KEY` ativa
apenas o **LLM-judge** das evals (a chave sozinha nunca ativa; a decisao do agente
nunca passa por LLM). Roteiro completo de teste em outra maquina: **[TESTING.md](TESTING.md)**.
Uso de IA registrado em **[AI_WORKLOG.md](AI_WORKLOG.md)**.

> Alternativa sem make: `pip install -r requirements.txt && pytest -q && python run.py`.
