# Instrucoes para o agente de IA (portavel)

Este desafio e resolvido com apoio de IA. Vale qualquer ferramenta: Claude Code, Codex,
GitHub Copilot, Cursor, Windsurf, Antigravity, Kiro ou outra. Independentemente dela,
siga a regra abaixo durante toda a sessao.

## Regra: registrar TODA interacao no AI_WORKLOG.md
Ao final de cada interacao com a IA (cada prompt relevante), faca APPEND de uma entrada
no arquivo AI_WORKLOG.md, sem apagar as anteriores, no formato:

| data/hora | ferramenta + modelo | pedido (resumo) | gerado/alterado (arquivos) | decisao: aceito/ajustado/rejeitado | como validou |

Nao registre segredos (chaves ou tokens). Uma linha por interacao basta.

## Como ativar (mecanismo portavel)
Aponte seu agente para este arquivo (AGENTS.md) OU copie esta regra para o arquivo de
regras da sua ferramenta. O mecanismo e o mesmo em todas: manter o AI_WORKLOG.md atualizado.
Arquivos de regras por ferramenta (exemplos):
- Claude Code: CLAUDE.md
- Cursor: .cursorrules (ou .cursor/rules/*.md)
- Windsurf: .windsurfrules
- GitHub Copilot: .github/copilot-instructions.md
- Codex e outros: AGENTS.md (este arquivo)
