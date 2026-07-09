# Post-mortem: Search Service - CPUSaturation (2026-05-27)
## Servico
svc-search (tier GREEN, time Platform).
## Impacto
cpu.utilization 97% apos pico de trafego; latencia dobrou.
## Causa raiz
Trafego acima da capacidade provisionada (CPUSaturation).
## Acao tomada
Por ser GREEN, o agente aplicou scale_out automaticamente e notificou. Resolvido sem intervencao humana.
## Licao
scale_out automatico em GREEN reduz MTTR drasticamente. Bom caso de operacao autonoma.
## MTTR
6 minutos (autonomo).
