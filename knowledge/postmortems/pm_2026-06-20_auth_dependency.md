# Post-mortem: Auth Gateway - ConnectionRefused (2026-06-20)
## Servico
svc-auth (tier RED, time Platform).
## Impacto
http.error_rate 8%; logins falhando com ConnectionRefused para o provedor de identidade.
## Causa raiz
Regiao us-east-1 do provedor de identidade fora do ar (dependency timeout).
## Acao tomada
Agente escalou on-call (RED). Time executou failover_region para sa-east-1.
## Licao
failover_region e a acao correta para ConnectionRefused, mas em servico RED exige aprovacao humana.
## MTTR
33 minutos.
