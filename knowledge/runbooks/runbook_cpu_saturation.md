# Runbook: CPUSaturation / CPU alta (cpu.utilization)
## Sintomas
cpu.utilization acima do threshold; latencia subindo junto.
## Causa raiz comum
Aumento de trafego alem da capacidade provisionada; hot loop.
## Acao recomendada
Acao primaria: **scale_out** (self-heal) adicionando replicas. Investigar hot path se a saturacao persistir apos o scale out.
## Notas de autonomia
GREEN: scale_out automatico. YELLOW: PR com nova capacidade. RED: escalar on-call.
