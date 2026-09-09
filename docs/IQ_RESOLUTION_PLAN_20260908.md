# Plano de resolução — IQ Option sem operações e com desconexões

Base: [diagnóstico da sessão de 08/09/2026](IQ_SESSION_ERRORS_20260908.md), conferido com
o código atual do Desktop e do Hub. Status atual: **IMPLEMENTADO E VALIDADO LOCALMENTE**.
A execução e suas limitações estão em
[IQ_RESOLUTION_IMPLEMENTATION_20260908.md](IQ_RESOLUTION_IMPLEMENTATION_20260908.md).
Não houve operação financeira, conta Real ou validação externa nesta execução.

## Objetivo e limite da promessa

Fazer cada broker operar independentemente, recuperar conexão com evidência, explicar toda
entrada bloqueada e mostrar na UI a permissão efetiva do Core. Uma entrada elegível precisa
chegar à submissão exatamente uma vez; uma entrada inelegível precisa ter motivo rastreável.

Não é possível garantir 100% de disponibilidade de uma API externa nem ausência absoluta de
bugs. O critério verificável é passar todos os cenários especificados, com evidência financeira,
de estado e do artefato compilado. Aprovação de sinal não garante aceitação do broker ou lucro.

Não alterar fórmulas, limiares, warmup, stake, payout mínimo, limites financeiros ou guards Real
para aumentar artificialmente o número de operações. Não recuperar o motivo dos sinais antigos
por suposição: a telemetria original perdeu parte da informação.

## O que precisa mudar

| ID | Problema | Resultado exigido |
| --- | --- | --- |
| R1 | Sinal OK esconde bloqueio posterior | Cada decisão termina em envio comprovado ou motivo explícito. |
| R2 | Parada e rearme vazam entre brokers | Falha/ação na Deriv não altera o armamento e gates próprios da IQ, e vice-versa. |
| R3 | IQOPTION e IQ_OPTION criam escopos distintos | Identidade canônica e limpeza apenas pelo produtor/generation dono do blocker. |
| R4 | Heartbeat falha e monitor morre | Recovery limitado, observável, cancelável e sem rearme automático. |
| R5 | Contagem parcial de mensagens | Mercado e consultas operacionais respeitam um orçamento coordenado. |
| R6 | Origem/espelho de manifesto falham | Catálogo válido acessível ou motivo preciso; cache conforme contrato de validade. |
| R7 | UI mostra intenção de ligar como prontidão | Operador vê conexão, armamento, bloqueio efetivo e última decisão separadamente. |

## Etapa 1 — Explicação completa das decisões e falhas (R1)

Arquivos centrais: `apps/core/iqoption_auto_trader.py`, `apps/core/worker_client.py`,
`apps/core/read_only_worker_supervisor.py`, `packages/observability/events.py`.

1. Criar identidade de decisão com broker, identificador local opaco de conta, geração, revisão
   da estratégia, ativo exato, timeframe e época da vela. Vincular a correlação financeira se
   houver admissão. Não expor loginid, e-mail ou credenciais nos eventos.
2. Separar eventos de avaliação, arbitragem, admissão, submissão e resposta. A deduplicação
   considera a etapa; o registro de `SIGNAL_OBSERVED` não impede `ENTRY_BLOCKED` depois.
3. Registrar todos os retornos após um sinal: bot desarmado, exigência de sinal novo, conflito,
   ordem em voo, risco, orçamento, monitor indisponível, payout ausente/insuficiente/vencido,
   manifesto incompatível e falha de persistência. Não converter exceção em sucesso.
4. Na decisão de payout, registrar Decimal como string, piso, idade e resultado do gate; sem
   payload de conta. O payout de outra coleta/horário nunca substitui a cotação desta entrada.
5. A mesma decisão pode passar de espera pré-admissão para elegível se a política atual permitir.
   Registrar transições e consolidar repetições; isso não altera o dedupe financeiro nem libera
   reenvio de SUBMITTING/UNKNOWN/ordem já consumida.
6. Preservar códigos sanitizados de falha: camada IPC ou broker, operação, generation, tipo de
   exceção, duração monotônica, código estável e próxima tentativa. Não habilitar dump bruto do
   stdout/stderr, cookies, respostas de autenticação ou strings irrestritas de exceção.
7. Obter evidências de persistência/resultado pelo Core e suas projeções/eventos existentes.
   Não adicionar leitura SQLite por avaliação. Logs são limitados e rotacionados; falha de
   diagnóstico jamais vira evidência de aceitação financeira.

Aceite: para cada sinal no ensaio, existe uma trilha até bloqueio/adiamento identificado,
submissão aceita, rejeição comprovada ou estado ambíguo. Eventos repetidos não multiplicam
ordens. Se a falha for anterior à criação da intenção, o teste comprova zero intenção/reserva/outbox.

## Etapa 2 — Isolamento de controle e ownership do Health Gate (R2, R3)

Arquivos: `apps/core/runtime.py`, `lifecycle_service.py`, `health.py`, `coordinator.py`,
`read_only_worker_supervisor.py` e os contratos de broker pertinentes.

O ajuste não se limita a `_request_deriv_recovery`. A revisão confirmou outros pontos:
`resume_new_entries_for` chama `clear_if('HG_SAFE_STOP')`, que remove o motivo em todos os
escopos; `_on_reconciliation_cycle_completed` redefine `dispatcher_started` pela saúde
agregada. Esses caminhos também precisam de correção e testes.

1. Definir parada de conta/broker explicitamente. Preservar parada global deliberada,
   shutdown, falha de banco e demais impedimentos globais reais.
2. Cada ARM/DISARM altera apenas o alvo. Rearmar IQ não limpa parada manual da Deriv nem
   uma parada global. A retomada global continua sendo uma ação própria, com semântica
   documentada; não reaproveitar silenciosamente o botão de um broker para isso.
3. Recovery da Deriv revoga somente sua autorização de novas entradas. Recovery IQ faz o
   equivalente. A conta não afetada mantém seu estado anterior, se seus próprios gates permitirem.
4. Reutilizar `HealthGate.state_for` e o despacho filtrado que o coordinator já oferece.
   Não criar um segundo Core financeiro. Auditar todos os usos de `dispatcher_started` para
   que saúde agregada não desligue a conta saudável e nem autorize uma conta desarmada.
5. Admissão e envio revalidam o escopo correto. Preservar serialização, intenção/reserva/outbox
   atômicas, deadlines, fencing e tratamento existente de UNKNOWN. Uma troca de estado entre
   persistir e enviar não pode furar o guard; exige teste concorrente específico.
6. Liquidar e reconciliar ordens existentes mesmo com novas entradas pausadas.
7. Canonizar IQ no limite interno para `Broker.IQ_OPTION`. Se o protocolo atual exige
   `IQOPTION`, manter esse valor no transporte e fazer conversão explícita na fronteira de
   health/domínio. Não renomear enums wire ou registros financeiros históricos indiscriminadamente.
8. Unificar blockers por união conservadora quando houver aliases antigos. A identidade
   lógica inclui produtor (supervisor/candles/clock) e generation: candle recebido não limpa
   falha do supervisor, e callback antigo não limpa blocker da conexão atual.
9. Manter APIs legadas por wrappers quando possível. Alteração de schema/protocolo, se necessária,
   será versionada e testada; não presumir migração obrigatória para estados apenas em memória.

Aceite: alternar falhas/rearmes em cada broker não altera a autorização do outro. Uma parada
global permanece efetiva. Nenhuma liberação de blocker ocorre sem evidência do produtor atual.

## Etapa 3 — Recovery contínuo e sem concorrência de reconectores (R4)

Arquivos: `read_only_worker_supervisor.py`, `worker_client.py`, `lifecycle_service.py`,
`apps/iqoption_connection_worker/server.py`, sessão/adaptador IQ e
`apps/core/iqoption_connection_safety.py`.

1. Separar liveness IPC de conexão autenticada no broker. O PONG comprova que o processo
   respondeu, não que a conta está pronta. O PING não deve executar login/reconnect oculto.
   Se for necessário ampliar health/capabilities, usar contrato compatível/versionado e testes.
2. O lifecycle coordena um único recovery por broker/conta/generation; o worker executa as
   operações de transporte. Nenhum pedido de candle, evento ou heartbeat inicia um recovery
   concorrente fora dessa coordenação.
3. Falha de sessão com IPC vivo solicita recuperação limitada na sessão existente. IPC morto
   ou processo encerrado exige supervisor/cliente novos, encerramento do anterior e fencing.
   Não reutilizar SocketWorkerClient cujo transporte falhou.
4. Estados operacionais propostos:

   `DISCONNECTED → RECOVERING → SYNCING → RECONCILING → READY_TO_ARM`.

   READY_TO_TRADE exige ação de rearme do operador, sinal posterior, payout fresco e todos os
   gates. Estados podem ser projetados sobre os enums existentes sem refatoração ampla.
5. SYNCING comprova conta/modo, saldo válido, relógio confiável e dados atuais; RECONCILING
   resolve exposição persistida ou mantém bloqueio por UNKNOWN. Conectar WebSocket sozinho
   não é critério de prontidão.
6. Reaproveitar backoff, máximo de tentativas, quarentena e controle persistente de login
   existentes no Desktop. Auth recusada, 2FA e rate limit têm política explícita e não provocam
   repetição cega. O limite do Lab é independente e não deve ser copiado para o EXE.
7. O monitor continua acompanhando o estado após erro transitório. Ao esgotar tentativas,
   expõe ATTENTION_REQUIRED/razão estável e não fica em loop nem finge READY.
8. Shutdown cancela a espera/backoff, impede nova geração e aguarda o encerramento das tasks.
   Callbacks atrasados não podem restaurar sessão, saldo, health ou armamento antigos.

Aceite: falhas recuperáveis simuladas convergem até READY_TO_ARM dentro da sequência de
backoff configurada, sem modificar timeouts para passar testes; falhas terminais convergem
para estado de atenção. Nenhum recovery envia ou repete ordem, rearma ou deixa processo órfão.

## Etapa 4 — Orçamento completo e menor trabalho repetido (R5)

O código define teto interno de 90 mensagens/minuto, com até 60 para mercado e reserva de 30.
Esses números são política local, não uma quota oficial comprovada da IQ Option. O contador
observado cobre apenas parte das chamadas. Saldo + relógio a cada dois segundos poderiam somar
até 60 pedidos IPC/minuto antes do mercado; isso não equivale automaticamente a 60 chamadas
externas, pois o adaptador pode responder por cache. Medir ambos separadamente.

1. Inventariar cada origem de chamadas e distinguir IPC local de mensagem efetiva HTTP/WS.
   A contabilização externa ocorre junto ao envio, cobrindo pedidos internos do adaptador.
2. Coordenar quotas e filas limitadas. Mercado não consome a reserva operacional; heartbeat,
   reconciliação e acompanhamento de ordens têm prioridade sobre scans e refresh de saldo.
   Não descartar eventos financeiros recebidos. Sob saturação bloquear novas entradas antes
   de criar exposição e preservar o acompanhamento da exposição existente.
3. Reutilizar cache de saldo com TTL explícito e invalidação por evento/reconnect; relógio via
   offset validado e TTL. Cache vencido não serve para validar entrada. Cancelar coletas antigas.
4. Reutilizar SeriesHub e cache incremental; uma coleta por série/fechamento, respeitando
   ativo exato, timeframe, geração e warmup. Uma vez sem orçamento, agendar a próxima chance
   pelo relógio monotônico; não tentar a cada tick nem queimar o sinal financeiro.
5. Medir demanda real sob 16 ativos e sob os catálogos admitidos pelo benchmark. Se exceder o
   orçamento, reduzir trabalho redundante/frequência de telemetria; não elevar tetos por palpite.

Aceite: medição por janela deslizante não supera o teto configurado em chamadas efetivas;
fila limitada, sem starvation dos controles. Testes preservam indicadores/sinais da referência.

## Etapa 5 — Diagnóstico e reparo do catálogo no Hub (R6)

Escopos: Desktop em `apps/core/manifest_client.py`; Hub em
`strategy-lab/apps/hub/supabase/functions/manifest_current/index.ts` e adaptadores relacionados.
São produtos isolados, integrados pelo manifesto assinado/versionado.

1. Recolher, de forma sanitizada, corpo/código das respostas e logs do Hub. O handler possui
   pelo menos duas causas de 503: `HUB_DB_FAILED` e `HUB_MANIFEST_LAST_GOOD_UNAVAILABLE`.
   O diagnóstico anterior só observou o status e não distinguiu as duas.
2. Conferir canal staging/production, referência do projeto, migrations, candidatos committed,
   caminho do objeto, hash, acesso de leitura e eventual divergência entre banco e Storage.
   Para o HTTP 400 do espelho, identificar a resposta real antes de alterar RLS, bucket ou URL.
3. Corrigir somente a causa demonstrada. Se não houver manifesto elegível/publicado, registrar
   indisponibilidade e concluir a publicação legítima quando existir evidência aprovada. Não
   copiar o cache local para o servidor, inventar aprovação ou assinar uma receita só para obter 200.
4. Manter último manifesto íntegro conforme validade/status e contrato de fallback. Expiração,
   revogação conhecida e revisão incompatível seguem bloqueando novas entradas; offline não
   autoriza prolongar validade arbitrariamente. Receitas em voo mantêm acompanhamento.
5. Testar ETag/304, fallback e integridade. O refresh permanece fora da execução financeira.
   Rever backoff para não consumir os quatro ciclos/hora logo nos primeiros minutos; orçamento
   esgotado informa a próxima tentativa possível, respeitando Retry-After quando aplicável.
6. Aplicação remota futura: migration/Edge Function apenas após testes do Hub, alvo conferido,
   versão anterior de rollback e smoke sanitizado. Nenhuma migração de banco por conveniência.

Aceite: 200 e 304 para manifesto legitimamente publicado, fallback comprovado e falhas
distinguíveis; manifesto hostil/expirado/regressivo não habilita entradas. Sem candidato válido,
o resultado correto é indisponibilidade explicada, não publicação fabricada.

## Etapa 6 — Estado honesto e útil na UI (R7)

Arquivos: `apps/core/ui_service.py`, `packages/protocol/ui_messages.py` e componentes IQ.

- Projetar separadamente conexão, sincronização/reconciliação, armamento do operador,
  autorização efetiva, blocker principal, última decisão e próxima tentativa de recovery.
- Se o broker estiver armado mas com gate fechado, exibir “Entradas bloqueadas: motivo”.
  Após reconexão: “Conectado — rearmar necessário”. Não manter “Bot ativo” como sinônimo de
  capacidade de enviar ordem; botão e painel usam o mesmo snapshot/generation do Core.
- Separar contagens de sinais, admitidos, enviados, aceitos, rejeitados e desconhecidos.
  Histórico de ordens vem da persistência, não do número de sinais.
- Exibir payout observado/piso/idade quando for o blocker e catálogo em cache quando offline.
  Os números do Lab só aparecem como evidência vinculada à revisão aprovada.
- Validar compatibilidade estrita das mensagens: campos opcionais só quando aceitos por ambos
  os lados; se necessário, versionar. A UI não altera gates nem decide autorização financeira.

Aceite: em falha injetada, painel e estado do Core concordam na próxima atualização concluída;
snapshot antigo não substitui geração nova. Medir latência de atualização no EXE sob carga.

## Matriz mínima de testes de regressão

Nomes abaixo são propostas de testes a criar ou equivalentes existentes a estender.

| Teste | Evidência obrigatória |
| --- | --- |
| `test_signal_ok_then_payout_block_is_recorded` | OK e bloqueio coexistem; nenhuma intenção/reserva/outbox. |
| `test_pre_admission_returns_have_reason` | Todos os retornos após sinal informam a etapa final. |
| `test_duplicate_diagnostics_do_not_duplicate_submit` | Redelivery não duplica submissão nem P&L. |
| `test_deriv_recovery_preserves_iq_authorization` | IQ mantém estado anterior com Deriv em recovery. |
| `test_iq_recovery_preserves_deriv_authorization` | Mesmo resultado no sentido inverso. |
| `test_arm_one_broker_does_not_clear_other_stop` | Rearme isolado não remove parada alheia. |
| `test_arm_one_broker_cannot_clear_global_stop` | Parada global continua exigindo sua ação própria. |
| `test_reconciliation_does_not_rearm_any_broker` | Snapshot conciliado não concede autorização. |
| `test_stop_between_persist_and_dispatch_is_respected` | Concorrência não permite envio após revogação; reserva/ordem resolvidas pelo fluxo vigente. |
| `test_iq_aliases_share_canonical_scope` | Aliases convergem sem apagar blockers. |
| `test_candle_success_does_not_clear_supervisor_failure` | Evidência parcial limpa apenas seu produtor. |
| `test_old_generation_cannot_change_current_health` | Callback antigo não afeta conta/estado atual. |
| `test_ping_does_not_login_or_submit` | PING somente liveness; zero login e buy. |
| `test_transient_broker_failure_keeps_recovery_alive` | Monitor não morre e recuperação chega a READY_TO_ARM. |
| `test_dead_ipc_replaces_client_and_reconciles` | Cliente novo, geração nova, estado financeiro reconstruído. |
| `test_recovery_exhaustion_is_visible_and_bounded` | Tentativas e quarentena respeitadas, sem loop infinito. |
| `test_shutdown_during_backoff_leaves_no_children` | Tasks e processos encerram sem novos spawns. |
| `test_unknown_is_never_retried_by_recovery` | Uma transmissão potencial; UNKNOWN mantém exposição. |
| `test_total_message_budget_includes_adapter_calls` | Contagem na saída cobre todas as origens. |
| `test_market_load_cannot_starve_reconciliation` | Saturação pausa novas entradas, acompanha abertas. |
| `test_manifest_primary_failure_uses_valid_fallback` | Hash/assinatura/validade e ETag preservados. |
| `test_manifest_absence_does_not_publish_local_cache` | Falta de publicação permanece explícita. |
| `test_manifest_poll_respects_window_after_errors` | HTTP limitado; próxima tentativa calculada. |
| `test_ui_reports_effective_gate_not_only_arm_flag` | Motivo e botão coerentes com snapshot do Core. |
| `test_iq_failure_trace_has_no_secrets` | Logs/relatório sanitizados em todos os caminhos. |

## Ordem de implementação e verificação

1. Etapa 1: instrumentar e reproduzir o comportamento atual; testes devem detectar os defeitos
   antes da correção. A telemetria permitirá resolver os sinais ainda sem causa demonstrada.
2. Etapa 2: canonicalização/ownership e isolamento de parada/rearme, com testes nos dois brokers.
3. Etapa 3: recovery único, geração, reconciliação, cancelamento e ausência de rearme.
4. Etapa 4: orçamento completo e redução de trabalho redundante.
5. Etapa 5: reparar Hub/refresh conforme resposta causal obtida.
6. Etapa 6: consolidar UI sobre os novos estados e evidências.
7. Executar regressão afetada a cada mudança. No fechamento, suíte Desktop, Ruff do escopo
   executável, mypy, compileall, secret scan e diff-check. Se Hub/Lab mudar, executar as suítes
   correspondentes nos ambientes próprios, incluindo Deno. Não somar contagens como prova única.

Reutilizar testes existentes de `read_only_worker_supervisor`, `unified_health_gate`,
`iqoption_failure_recovery`, `manifest_execution_gates`, `manifest_runtime_refresh` e os replays
de 24h. Relacionar cada critério a teste/evidência; não exigir um arquivo novo para cada nome.

## Validação do EXE e critério de liberação

**Local:** build canônico, integridade, scanner, abertura de UI e encerramento seguro. Executar
no próprio EXE um replay de 24h com falhas de sessão/IPC e troca de geração. Adicionar soak de
pelo menos 2h com o mesmo runtime vivo, observando memória, filas, latências, UI e shutdown.
Essas durações são alvos de validação, não garantias de disponibilidade contínua.

**Practice externo:** após correções e no escopo financeiro explicitamente autorizado para o
ensaio, registrar versão/hash, conta confirmada como Practice, uma ordem em voo e limites de
stake/perda. Observar sinais naturais por pelo menos 60min; conferir IDs do broker, intenção,
reserva/outbox, aceitação/rejeição e settlement no banco. Testes locais não substituem isso.
Injetar falhas de rede somente em conta/perfil de teste isolado e sob condições controladas.

Se não houver sinal naturalmente elegível, registrar inconclusivo para envio externo. Não
afrouxar payout, estratégia ou risco para fabricar aceitação. Para validar infraestrutura sem
depender de sinal natural, usar fixture local determinística; não apresentá-la como operação real.

**Liberação:** todos os cenários locais aprovados; nenhum sinal do ensaio fica sem desfecho
explicado; nenhuma ordem duplicada; nenhum recovery rearma; nenhum callback antigo contamina
a geração atual; zero processos residuais após shutdown. Relatório distingue validação local,
teste do binário e validação externa executada/pendente.

Conservar artefato anterior e plano de rollback. Havendo ordem aberta/UNKNOWN, não encerrar
à força ou iniciar outra instância para trocar versão. Drenar/reconciliar pelo caminho existente;
backup consistente do perfil e compatibilidade de schema precedem rollback de binário.

## Resultado esperado para o operador

Com a IQ conectada, sincronizada, armada e com sinal elegível, a decisão segue ao Core e ao
broker uma única vez. Com bloqueio, o painel informa qual gate impediu a entrada. Se a IQ cair,
o sistema recupera a conexão quando possível e solicita rearme. Se a Deriv cair, a IQ continua
sob seus próprios controles. Qualidade/frequência das estratégias será medida separadamente
depois que execução e diagnóstico estiverem corretos.
