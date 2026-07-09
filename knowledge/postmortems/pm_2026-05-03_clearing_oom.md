# Post-mortem: Clearing Ledger - OOMKilled (2026-05-03)
## Servico
svc-clearing (tier RED, time Clearing).
## Impacto
mem.utilization atingiu 96%; pods OOMKilled durante o fechamento. Liquidacao atrasou 22 min.
## Causa raiz
Batch de reconciliacao carregou posicoes em memoria sem paginacao (DeployRegression + OOMKilled).
## Acao tomada
Por ser RED, o agente NAO agiu sozinho: escalou o on-call (page_oncall). O time aplicou restart_pod manual e corrigiu a paginacao via Pull Request.
## Licao
Servicos RED nunca sao remediados automaticamente. restart_pod resolve o sintoma de OOM; a correcao real foi paginacao.
## MTTR
41 minutos.
