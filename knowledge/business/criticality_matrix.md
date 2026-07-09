# Matriz de Criticidade e Autonomia (A5X)
O tier do servico define a autonomia permitida ao agente de operacao autonoma.
- GREEN: o agente pode agir sozinho (self-heal) e notificar. Ex.: restart_pod, scale_out, clear_cache.
- YELLOW: o agente NAO age direto; propoe a correcao via Pull Request e notifica.
- RED: o agente apenas escala/notifica o on-call. Nenhuma acao autonoma, mesmo self-heal.
Acoes destrutivas (rollback_deploy, failover_region) so podem ser automatizadas em GREEN.
