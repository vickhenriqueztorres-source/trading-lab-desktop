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

Na workspace Deriv, o resumo de risco usa seis cartões em duas linhas para exposição, Stop Loss,
meta diária, perdas consecutivas, pausa e stake. A aba `Parâmetros e risco` reserva sua área útil aos
controles efetivos do Core; conteúdo explicativo não pode comprimir ou ocultar campos, Martingale
delimitado, validação ou o botão de aplicação. O layout de referência 1382×744 não exige scroll.

Em `Mercado ao vivo`, o radar multiativo Shadow aparece antes do painel da estratégia selecionada.
A tabela mostra posição, ativo, estado, melhor hipótese estatística, margem conservadora e
aquecimento. A marca de candidato é informativa: não existe botão de execução no radar, ele não
altera o ativo configurado e deve exibir abstenção quando nenhum candidato conservador existe. O
aviso visível distingue margem estatística de payout/EV líquido.

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
- uma falha IPC transitória não encerra o polling da dashboard: a UI mantém a cadência bounded,
  tenta reconectar no ciclo seguinte e volta a projetar liquidações sem exigir Safe Stop ou outro
  comando manual;
- restart da UI reconstrói as abas a partir da próxima projeção;
- Safe Stop permanece visível em todas as abas e não abandona operações abertas;
- broker desconhecido não é inferido como Deriv ou IQ Option;
- valores monetários são formatados diretamente de minor units, sem `float`.
