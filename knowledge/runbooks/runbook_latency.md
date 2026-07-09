# Runbook: DeadlineExceeded / Latencia alta (latency.p99)
## Sintomas
latency.p99 acima do SLO; timeouts (DeadlineExceeded) em traces e logs.
## Causa raiz comum
Falta de capacidade ou dependencia lenta.
## Acao recomendada
Acao primaria: **scale_out** para absorver a fila. Se a causa for dependencia, ver runbook de dependency timeout.
## Notas de autonomia
GREEN: scale_out automatico + notifica. YELLOW: Pull Request. RED: escalar.
