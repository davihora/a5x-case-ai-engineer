# Runbook: DeployRegression / Erro 5xx alto (http.error_rate)
## Sintomas
http.error_rate acima do SLO logo apos um deploy; logs 5xx (upstream returned 5xx).
## Causa raiz comum
Regressao introduzida por deploy recente (DeployRegression).
## Acao recomendada
Acao primaria: **rollback_deploy** para a ultima versao estavel. Acao destrutiva: so executar automaticamente em servico GREEN; em YELLOW abrir Pull Request de revert.
## Notas de autonomia
GREEN: rollback_deploy automatico. YELLOW: PR de revert. RED: escalar on-call.
