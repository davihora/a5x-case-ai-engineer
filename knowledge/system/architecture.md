# Arquitetura do Sistema de Operacao Autonoma (A5X)
A plataforma central recebe logs, traces e metricas via OpenTelemetry. O sistema tem 3 camadas:
- Camada 1 (backend): ingestao/limpeza do feed OTel, deteccao de breaches e o loop do agente.
- Camada 2 (modelagem relacional, DuckDB): tabelas incident, action, incident_memory, eval_result.
- Camada 3 (visualizacao): dashboards de triagem, console de acoes e scorecard de evals.
O agente enderca incidentes usando runbooks, post-mortems e politicas de negocio recuperados via RAG.
