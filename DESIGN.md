# DESIGN.md - Agente de Remediacao

## Arquitetura da solucao (diagrama obrigatorio)
Inclua o desenho de TODA a solucao (as 3 camadas e o fluxo de dados), nao so de um trecho.
Use Mermaid embutido neste .md, ou anexe um Excalidraw / draw.io exportado (PNG ou SVG, com a fonte).
O diagrama deve mostrar componentes, fluxo, onde a IA/RAG atua e os pontos de decisao.
Explique em alto nivel as escolhas em aberto e os trade-offs (ferramentas e arquitetura).

## Arquitetura (3 camadas)
- Backend: o loop do agente (fila -> decisao -> guardrail -> aplicacao). Tool-use/funcoes.
- Modelagem: tabela action, chave de idempotencia, status.
- Visualizacao: o console de acoes (o que o operador precisa ver).

## Politica por criticidade
GREEN self-heal automatico; YELLOW propoe via PR; RED so escala. Justifique os limites.

## Guardrails e seguranca de IA
Whitelist por tier, acao destrutiva, blast radius, rate limit; guardrails deterministicos independentes do modelo. Seguranca de IA a prevenir: prompt injection (direto e indireto via RAG), memory poisoning, excesso de agencia e vazamento do system prompt/segredos/dados sensiveis; trate conteudo ingerido/recuperado como nao confiavel (instrucoes != dados). O que mais adicionaria?

## Idempotencia e observabilidade
Como garante que rodar 2x nao age 2x; o que tracearia por decisao (tokens, custo, latencia).

## RAG (documentacao)
- Ingestao multi-formato de `knowledge/` (md, pdf, docx, txt): runbooks, post-mortems e politicas de negocio.
- Como voce recupera a doc relevante para o incidente (retriever, chunking, top-k) e a usa para endereca-lo.
- Em producao: embeddings + reranking, atualizacao do indice, faithfulness e citacao de fontes.
