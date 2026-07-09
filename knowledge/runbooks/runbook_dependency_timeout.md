# Runbook: ConnectionRefused / Dependencia indisponivel (dependency)
## Sintomas
http.error_rate alto com error_type ConnectionRefused; dependencia externa fora do ar.
## Causa raiz comum
Falha de uma regiao/instancia da dependencia.
## Acao recomendada
Acao primaria: **failover_region** para a regiao saudavel. Acao destrutiva: automatica apenas em GREEN; caso contrario abrir Pull Request de failover controlado.
## Notas de autonomia
GREEN: failover_region automatico. YELLOW: PR. RED: escalar on-call imediatamente.
