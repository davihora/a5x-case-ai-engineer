# Runbook: CacheStampede / Latencia por cache (cache)
## Sintomas
Pico de latency.p99 e carga apos expiracao de cache (CacheStampede).
## Causa raiz comum
Expiracao simultanea de chaves quentes; ausencia de lock/jitter.
## Acao recomendada
Acao primaria: **clear_cache** e reaquecer. Adicionar jitter de TTL para evitar recorrencia.
## Notas de autonomia
GREEN: clear_cache automatico. YELLOW: PR com jitter de TTL. RED: escalar.
