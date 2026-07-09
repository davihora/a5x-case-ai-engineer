# Starter (takehome) - Agente de Remediacao (DuckDB)

[![CI](https://github.com/davihora/a5x-case-ai-engineer/actions/workflows/ci.yml/badge.svg)](https://github.com/davihora/a5x-case-ai-engineer/actions/workflows/ci.yml)

Sistema de **operacao 100% autonoma** sobre telemetria OpenTelemetry da A5X.
Tiers de servico definem a autonomia do agente: GREEN=self-heal, YELLOW=PR, RED=escala.

Arquitetura de 3 camadas: **backend** + **modelagem relacional** + **visualizacao**.

```bash
pip install -r requirements.txt
pytest -q        # pytest -q (comeca falhando)
python run.py    # pipeline ponta-a-ponta -> gera dashboard.html (abra no navegador)
```

Implemente as funcoes marcadas em `agent/build.py`. Camada de viz em `dashboard/render.py`. Dados em `data/`.
Preencha `DESIGN.md` e `AI_WORKLOG.md`. Calibre a profundidade pela descrição de perfil da sua vaga.
