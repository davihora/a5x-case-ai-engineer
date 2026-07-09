# Runbook: OOMKilled / Memoria alta (mem.utilization)
## Sintomas
mem.utilization acima do threshold do servico. Logs com error_type OOMKilled; pods reiniciando por falta de memoria.
## Causa raiz comum
Vazamento de memoria ou heap subdimensionada apos aumento de carga.
## Acao recomendada
Acao primaria: **restart_pod** (self-heal). Reinicia o pod para liberar memoria. Se recorrente em menos de 30 min, escalar para revisao de limites de memoria.
## Notas de autonomia
Em servicos GREEN o agente aplica restart_pod automaticamente e notifica. Em YELLOW, abrir Pull Request ajustando requests/limits. Em RED, apenas escalar.
