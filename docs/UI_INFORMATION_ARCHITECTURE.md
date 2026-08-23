# Arquitetura de Informação da UI

## Objetivo

A UI apresenta projeções bounded do Core sem acessar corretoras ou bancos críticos. A navegação
separa contexto global, Deriv, IQ Option, atividade financeira e configuração para que falha ou
modo de uma corretora não sejam interpretados como estado da outra.

## Navegação principal

| Aba | Conteúdo | Regra de segurança |
|---|---|---|
| Visão geral | exposição, P&L, estado global, resumos das corretoras e Health Gates | consolidado não substitui o detalhe por broker |
| Deriv | projeção e ordens exclusivamente `DERIV` | identidade exata; sem associação por substring |
| IQ Option | projeção e ordens exclusivamente `IQ_OPTION` | falha IQ não oculta Deriv |
| Atividade | todas as ordens projetadas pelo Core | abertas, `UNKNOWN` e em reconciliação nunca são ocultadas |
| Configurações | explicações de aplicativo, risco, estratégias e diagnóstico | nenhum controle financeiro local ou fictício |

Cada aba de corretora possui:

- **Status:** modo efetivo, conexão, saldo/moeda, relógio e ordens daquele broker;
- **Configuração:** explicação, escopo, valor efetivo confirmado pelo Core e aviso explícito de que
  modo real não está disponível.

## Configuração explicável

Uma opção editável só pode existir depois de haver comando IPC versionado, validação no Core e
resposta que confirme o valor efetivo. Até lá, a UI mostra a limitação como somente leitura em vez
de oferecer botão que não produz mudança autoritativa.

As seções globais explicam:

- aplicativo: idioma e natureza descartável da projeção;
- risco e segurança: limites efetivos do Core e semântica do Safe Stop;
- estratégias: configuração imutável/versionada e ordem Runtime → Arbiter → Allocator → Risk Ledger;
- diagnóstico: bundle local redigido, sem bancos, vaults ou credenciais.

## Persistência e falhas

- trocar de aba não altera estado financeiro;
- desconexão IPC conserva a última projeção apenas para visualização e marca o Core desconectado;
- restart da UI reconstrói as abas a partir da próxima projeção;
- Safe Stop permanece visível em todas as abas e não abandona operações abertas;
- broker desconhecido não é inferido como Deriv ou IQ Option;
- valores monetários são formatados diretamente de minor units, sem `float`.
