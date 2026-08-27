# Briefing Executivo — Trading Lab Desktop

**Baseline executável:** v1.9.11

**Atualização documental:** 2026-08-26

**Plataforma:** Windows 10/11 64 bits

**Estado:** laboratório local com execução financeira externa limitada à Deriv Demo

## Visão do produto

O Trading Lab Desktop é uma aplicação Windows para pesquisar, configurar, acompanhar e executar
estratégias automatizadas com controles de risco, persistência local e recuperação conservadora.
O produto combina uma interface operacional, um Core financeiro autoritativo e processos isolados
para integração com corretoras.

O sistema não promete lucro ou assertividade. Toda métrica estatística é evidência de observação,
não garantia de resultado. A prioridade do produto é impedir duplicidade de ordem, exposição
desconhecida e retomada insegura depois de falhas.

## Estado atual da v1.9.11

| Capacidade | Situação atual |
|---|---|
| Aplicativo desktop e launcher portátil | implementados |
| Instância única e recuperação de processos | implementadas |
| Deriv pública, ticks e diagnóstico | implementados |
| Login Deriv por API Token | implementado dentro do aplicativo |
| Seleção de conta Deriv Demo ou Real | implementada e explícita |
| Execução externa de ordens Deriv Demo | implementada para as três estratégias |
| Conta Deriv Real | conexão e monitoramento read-only; envio financeiro bloqueado |
| IQ Option | modelos, contratos, testes e harnesses; sem login/execução externa na UI |
| Estratégias | três estratégias Digit Edge operacionais em Demo |
| Martingale | opcional, limitado e desligado por padrão |
| Dashboard e resultados | atualização durante a operação |
| Persistência e reconciliação | implementadas em SQLite |
| Diagnóstico ZIP redigido | implementado; não inclui bancos nem credenciais |
| Instalador/assinatura Authenticode | ainda não implementados |

O transporte Deriv inicia em modo público/fake por segurança. A capacidade financeira só é ativada
depois que o usuário informa um token válido, escolhe a conta e confirma a conexão. Uma conta Real
pode ser selecionada para leitura e monitoramento, mas a v1.9.11 não envia ordens reais.

## Experiência principal

1. O usuário abre o mesmo executável portátil; uma segunda abertura apenas traz a janela existente
   para frente.
2. Dentro do aplicativo, acessa a área Deriv, informa o API Token e escolhe uma conta Demo ou Real.
3. Para conta Real, confirma explicitamente a seleção; o modo continua read-only nesta versão.
4. Escolhe uma das três estratégias, revisa ativo, stake, limites e Martingale opcional.
5. Em Demo, liga o bot manualmente. O bot nunca começa armado sozinho.
6. A aplicação aquece a análise, procura um sinal novo, reserva risco e envia no máximo uma ordem
   Deriv por vez.
7. Dashboard, histórico e gestão de risco são atualizados enquanto os eventos chegam.
8. Ao pausar, trocar de estratégia, perder conexão ou encerrar, novas entradas são bloqueadas e o
   estado pendente é reconciliado antes de qualquer retomada.

## Estratégias disponíveis

| Estratégia | Contrato Deriv | Ideia operacional |
|---|---|---|
| Tail Probability Edge | Over/Under | procura concentração estatística sustentável nas caudas baixa ou alta dos últimos dígitos |
| Selective Differs Edge | Digit Differs | seleciona o dígito com menor probabilidade condicional observada |
| Parity Regime Edge | Even/Odd | procura regime estatístico persistente entre dígitos pares e ímpares |

As três usam aquecimento de 500 ticks, janelas de 200/350/500 ticks, limite inferior de Wilson a
99% e contexto pelo grupo de paridade do dígito anterior. O radar pode comparar automaticamente
`R_10`, `R_25`, `R_50`, `R_75` e `R_100`. A seleção automática não garante vantagem: ela só troca
de ativo quando as regras de elegibilidade e segurança permitem.

## Gestão de risco

- O Core é a única autoridade financeira local.
- Uma intenção, sua reserva de risco e a outbox são persistidas antes do envio.
- Há no máximo uma ordem Deriv em voo e cada sinal de um tick é consumido uma única vez.
- Timeout potencialmente aceito vira `UNKNOWN`; a aplicação não repete a submissão no escuro.
- Pausa, troca de estratégia e reconexão não reaproveitam sinal antigo nem religam o bot.
- O filtro de desempenho aplica cooldown de 10 minutos após amostra mínima de 10 e resultado não
  positivo nas últimas 30 operações; a retomada usa até 10 operações de prova.
- Martingale é opcional, limitado por multiplicador, quantidade de passos, stake máxima e stop
  diário projetado. Durante uma recuperação, o ativo fica fixado para evitar mudança de contexto.
- O padrão atual é stake USD 1, stop diário USD 50, meta USD 30, uma perda consecutiva, pausa de
  30 segundos, confiança de 92,5%, `R_100`, seleção automática ligada e Martingale desligado.

## Confiabilidade e segurança

O launcher utiliza bloqueio de perfil, mutex de instância única e supervisão de processos. A sessão
Deriv autenticada é reconstruída depois de falha de transporte sem reenviar automaticamente ordem
ambígua. O token e a conta escolhida são protegidos com Windows DPAPI no escopo do usuário atual;
o App ID público é interno à aplicação. Logs, UI e diagnóstico devem ocultar credenciais.

O projeto usa dois bancos locais: `state.db` para estado financeiro/autoritativo e
`strategy_data.db` para ticks, análise e evidência de estratégia. O pacote de diagnóstico é
redigido e não transporta o vault, tokens ou bancos operacionais.

## Qualidade verificada

A suíte atual coleta **613 testes**, cobrindo Core, IPC, processos, Deriv pública/autenticada,
execução Demo, reconciliação, UI, estratégia, seleção automática, Martingale limitado, launcher e
segurança. Integrações externas reais continuam dependentes de rede, disponibilidade da Deriv e
permissões do token; testes locais não demonstram rentabilidade.

## Limitações e próximos marcos

1. Manter ordem financeira Real bloqueada até aprovação formal, critérios regulatórios e validação
   independente de risco.
2. Concluir integração operacional da IQ Option somente com API/política suportada.
3. Executar soak prolongado no Windows e ampliar telemetria de latência ponta a ponta.
4. Entregar instalador, atualização e binários assinados.
5. Promover estratégias apenas com evidência reproduzível fora da amostra; nunca por assertividade
   aparente de curto prazo.

## Fontes de verdade

- requisitos e limites do produto: [PRD](PRD_Trading_Desktop_Deriv_IQOption.md);
- arquitetura atual e alvo: [Arquitetura](Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md);
- regras obrigatórias: [RULES.md](RULES.md) e [AIGUARD.md](AIGUARD.md);
- instruções para agentes: [AGENTS.md](AGENTS.md);
- visão executável detalhada: [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md);
- componentes e modos: [docs/CURRENT_ARCHITECTURE.md](docs/CURRENT_ARCHITECTURE.md);
- operação e suporte: [docs/USER_GUIDE.md](docs/USER_GUIDE.md) e
  [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md);
- decisões e validações: [WORKLOG.md](WORKLOG.md) e [TEST_PLAN.md](TEST_PLAN.md).
