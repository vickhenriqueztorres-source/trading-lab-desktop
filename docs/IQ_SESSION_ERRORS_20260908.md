# IQ Option — relatório da sessão sem operações e com desconexões

Data da análise: 08/09/2026 BRT. Janela fechada para as contagens: 20:37:20 até
21:13:20 BRT (23:37:20 UTC de 08/09 até 00:13:20 UTC de 09/09). A sessão continuou
aberta durante a inspeção; resultados posteriores não estão incluídos.

## Resultado comprovado

- Zero intenções e zero ordens IQ Option criadas nessa janela, consultadas no SQLite
  operacional com `mode=ro` e `PRAGMA query_only=ON`.
- Seis avaliações de estratégia chegaram ao estágio `OK`. Isso comprova sinais de
  análise, mas não aprovação dos gates posteriores, submissão ou aceitação externa.
- Duas transições do supervisor IQ para `HG_MARKET_DATA_DISCONNECTED`, além de
  oscilações do estado de coleta de candles.
- Um Safe Stop global provocado pelo recovery da Deriv ficou sem evento de liberação
  até o fim da janela, apesar de a IQ ter sido armada anteriormente.
- Nenhuma rejeição remota de ordem IQ foi demonstrada nesta sessão: não há ordem nova
  persistida. O histórico anterior contém 335 REJECTED e 102 SETTLED, com última ordem
  em 03/09; esses números não representam tentativas de hoje.

Artefato extraído às 20:37:16: `TradingLab.exe`, SHA-256
`5F2D4E5E910B5CEB9231ADF91126FB2622BBEA5F1C5EE67AFE8694CA5E85C535`, igual ao
onedir entregue. As cópias empacotadas de `iqoption_auto_trader.py`,
`lifecycle_service.py` e `read_only_worker_supervisor.py` coincidem com o fonte analisado.
As consultas de processo retornaram sete processos TradingLab, mas sem acesso ao caminho
executável de cada PID; não foi possível atribuir individualmente cada PID ao artefato.

## Linha do tempo — horário BRT

| Horário | Evidência |
| --- | --- |
| 20:37:20 | Novo Core abriu o banco e executou recovery. |
| 20:37:22 | Cache do manifesto aceito: `MANIFEST_ACCEPTED`. |
| 20:37:39 e 20:37:41 | IQ armada; houve Safe Stop entre os dois armamentos. |
| 20:43:07 e 20:43:15 | Sinais `OK` em EURUSD e USDCHF. |
| 20:43:59 | Supervisor IQ bloqueou `HG_MARKET_DATA_DISCONNECTED`. |
| 20:45:39–20:45:41 | Bloqueios IQ liberados e reconciliação concluída. |
| 20:45:46 | Sinal `OK` em USDCHF, antes do novo armamento. |
| 20:45:48 | Novo armamento da IQ. |
| 20:50:10 | Deriv perdeu telemetria; recovery aplicou `HG_SAFE_STOP` global. |
| 20:50:32 | Deriv recuperou, com `OPERATOR_REARM_REQUIRED`. |
| 21:02:03, 21:02:20 e 21:03:02 | Sinais `OK` em NZDUSD, GBPUSD e EURUSD. |
| 21:03:04 | Novo bloqueio do supervisor IQ. |
| 21:03:19–21:03:54 | Gate da coleta alternou entre bloqueado e liberado. |
| 21:11:54–21:11:55 | Bloqueios IQ liberados novamente. |

Os intervalos de bloqueio do supervisor foram aproximadamente 1min40s e 8min50s.
Não equivalem a uma medição direta da duração da queda no servidor da corretora.

## 1. Recovery da Deriv interfere na IQ — defeito confirmado no código

`apps/core/lifecycle_service.py::_request_deriv_recovery` chama incondicionalmente
`runtime.stop_new_entries()`. Em `apps/core/runtime.py`, essa função desliga o dispatcher
e aplica `HG_SAFE_STOP` global. O fluxo não limita a parada ao broker Deriv.

O trader IQ consulta `operator_armed=lambda: self._iqoption_bot_armed`, um estado separado.
Assim, a indicação de armado pode coexistir com o gate financeiro global fechado. A sequência
das 20:50:10 é evidência operacional deste caminho; não há liberação global posterior na janela.
Esse defeito explica um bloqueio real após aquele horário, mas não explica sozinho por que
os dois sinais das 20:43 não produziram intenção.

## 2. Supervisor pode permanecer desconectado após falha recuperável

Em `apps/core/read_only_worker_supervisor.py::_monitor_loop`, a primeira
`WorkerDispatchError` do ping chama `_on_disconnect(IPC_CONNECTION_LOST)` e encerra a thread.
`_on_disconnect` marca DISCONNECTED, mas não inicia nova sondagem ou recovery.

O worker IQ tenta reconectar dentro do PING usando a sessão existente. Se essa tentativa
retornar um erro, `SocketWorkerClient.ping` aceita apenas PONG e gera falha de heartbeat.
Uma falha recuperável no broker pode, portanto, encerrar definitivamente o monitor do Core.
Requisições posteriores de candles podem reconectar a sessão por outro caminho, enquanto a
saúde do supervisor continua desconectada.

O recovery IQ agendado pelo lifecycle atende startup com ordem pendente; não existe ligação
desse callback de desconexão a um ciclo geral de recuperação equivalente ao da Deriv.

**Limitação:** o motivo inicial das duas quedas não foi preservado. Não é possível afirmar se
foi timeout de rede, erro da API, expiração de sessão, fechamento remoto ou atraso do worker.
Não há evidência que autorize dizer que a corretora bloqueou a conta.

## 3. Falta de diagnóstico causal e dois estados concorrentes para IQ

O supervisor descarta `_code` em `_on_disconnect`; o processo filho usa stdout/stderr DEVNULL.
O trader captura exceção de candles como indisponibilidade genérica. Falhas de saldo e relógio
são capturadas sem registro de causa no início de `_cycle`.

O supervisor usa broker `IQOPTION`; o trader usa `IQ_OPTION`. `HealthGate` apenas converte para
maiúsculas, sem unificar esses nomes. Os dois blockers existem em escopos distintos. Isso foi
observado às 21:03, quando o trader limpou seu blocker enquanto o supervisor manteve o outro.
O alias incorreto é um defeito de consistência, não justificativa para remover os guards.

## 4. O motivo posterior ao sinal é perdido — defeito confirmado

`IqOptionAutoTrader._record_decision` deduplica por `(symbol, key, epoch)`, sem incluir etapa.
O estágio da estratégia é gravado antes de `_prepare_execution`. Caso payout, monitor ou
manifesto neguem a entrada, a tentativa de gravar esse motivo usa a mesma chave e é descartada.
Outras saídas pré-envio alteram apenas `_status_reason` em memória.

Por isso os seis registros `OK` não permitem identificar, retrospectivamente, o último gate de
cada sinal. O terceiro sinal ocorreu antes do rearme; os três últimos coexistiram com Safe Stop
global. Os dois primeiros precisam de instrumentação que preserve a decisão de admissão.

## 5. Atualização remota do catálogo indisponível

Na janela houve quatro respostas HTTP 503 da origem e quatro HTTP 400 do espelho, seguidas de
eventos de orçamento de polling esgotado às 20:41:09 e 20:55:30. O cache local versão 1 foi
aceito e contém 16 entradas F1 M1, uma por ativo; não são 16 famílias independentes.

A validade e a assinatura aceitas no startup não comprovam a qualidade estatística dessas
entradas. Esta inspeção não aprovou seus resultados de pesquisa. A falha remota não deve ser
confundida com ausência total de catálogo, porque a análise local efetivamente produziu sinais.

## 6. Frequência de análise e pressão de mensagens

| Estágio registrado | Quantidade |
| --- | ---: |
| ASSET_MISMATCH | 8.880 |
| REGIME | 172 |
| NO_SIGNAL | 204 |
| CONFIRM | 34 |
| OK | 6 |

`ASSET_MISMATCH` inclui a exclusão esperada de receitas pertencentes a outros ativos durante
AUTO. Não representa 8.880 oportunidades perdidas nem comprova mapeamento incorreto. Essas
contagens são eventos de diagnóstico, não número de operações, taxa de acerto ou frequência futura.

Houve pressão do orçamento local em 52/60 mensagens às 20:45:41 e 48/60 às 21:03:53. Isso não
comprova ultrapassagem do teto nem punição pelo broker. O trader também consulta saldo e relógio
a cada dois segundos fora desse contador de candles/payout; portanto ele não mede toda a carga.

O manifesto exige payout mínimo `0.85`. O `0.82` medido antes no Strategy Lab pertencia a outro
horário/contexto e não comprova o payout destes seis sinais. Payout insuficiente continua sendo
uma hipótese para sinais sem admissão, até que o valor e o resultado do gate sejam registrados.

## Correções prioritárias propostas

1. Isolar parada, dispatcher e recovery por broker, preservando Safe Stop financeiro e rearme.
2. Unificar a identidade IQ e recuperar saúde do supervisor com probes limitados, geração e
   reconciliação; distinguir falha do broker de falha do IPC, sem rearmar automaticamente.
3. Registrar código sanitizado de desconexão e etapas separadas: sinal, arbitragem, admissão,
   payout, submit e resposta. Deduplicar por etapa, sem duplicar sinal financeiro.
4. Fazer o painel explicar a diferença entre intenção do operador, conexão e permissão efetiva.
5. Reparar disponibilidade da origem/espelho e medir a carga total de mensagens.

Reproduções necessárias antes de um próximo build: Deriv cai com IQ armada; ping IQ falha e
retorna; candles recuperam depois do monitor; sinal OK barrado por payout/Safe Stop; nenhum
reconnect rearma; nenhum caminho financeiro ambíguo é reenviado.

## Escopo desta análise

Inspeção somente leitura dos processos, journal, cache público, configurações de risco e SQLite.
Não houve clique em ligar/rearmar, conexão de corretora, envio de ordem, modificação de runtime,
banco ou configurações, troca de estratégia, alteração de guards ou novo build. Somente este
relatório e a entrada correspondente do WORKLOG foram criados. Os testes anteriores de build
não são prova de estabilidade externa por 30 minutos, e não substituem os cenários acima.
