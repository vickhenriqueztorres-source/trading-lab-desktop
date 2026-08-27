# Auditoria de liveness e recovery — v1.9.11

## Escopo e veredito

Esta auditoria usa código, testes e documentos atuais da v1.9.11 como fonte de verdade. A correção
é incremental: preserva Core como autoridade financeira, `SingleDatabaseWriter`, persist-before-act,
reserva de `UNKNOWN`, idempotência, Real read-only, uma ordem Deriv em voo e rearm explícito.

**Veredito:** parcialmente resolvido. Os defeitos reproduzíveis no runtime local foram corrigidos e
cobertos. A conexão externa Deriv continua opt-in e não é certificada pela suíte local sem rede.

## Root causes confirmadas

| Região | Cenário | Causa do estado preso | Correção |
|---|---|---|---|
| `apps/core/worker_supervisor.py`, `restart`/recovery | worker cai durante ordem | o processo podia ser recriado, mas Core, router e event pump mantinham o `SocketWorkerClient` antigo; não havia probe automático depois de `OPEN` | supervisor virou porta financeira estável que delega ao cliente atual, agenda recovery, executa `OPEN → HALF_OPEN → CLOSED` e chama reconciliação |
| `apps/core/worker_supervisor.py`, `shutdown` | encerramento durante recovery | shutdown podia capturar o processo antigo enquanto a thread de recovery publicava outro worker; o novo processo mantinha o SQLite do simulador aberto | shutdown e restart agora compartilham a mesma trava; nenhuma nova geração pode surgir entre captura, encerramento e limpeza |
| `apps/core/lifecycle_service.py`, recovery Deriv | queda autenticada enquanto bot estava ligado | recovery chamava `resume_new_entries()` e podia reabrir entradas sem novo ARM | qualquer disconnect desarma; worker/OTP/sessão são substituídos; reconciliação e telemetria voltam, mas o estado final exige clique explícito |
| `apps/core/runtime.py`, startup/deferred reconciliation | restart com Deriv não terminal | Core iniciava público e a reconciliação Deriv dependia do comando posterior da UI | UI/Core ficam disponíveis e, havendo credencial salva e candidato Deriv, recovery autenticado inicia em background sem habilitar submissão |
| `apps/core/risk.py` + persistence | crash após loss/settlement | step, perdas, pin e cooldown existiam apenas em memória; havia janela entre commit financeiro e atualização do Martingale | migration `0005_digit_risk_runtime`; settlement/reconciliation atualizam sequência na mesma transação SQLite e o ledger recarrega a projeção durável |
| `apps/core/deriv_auto_trader.py` | restart no step N | `_recovery_symbol` era privado do trader e sumia no restart | pin passa a vir do estado durável; step sem pin falha fechado como `BOT_MARTINGALE_STATE_INCOMPLETE` |
| `apps/core/health.py` | clock/market ruim, mas conta financeira parecia aberta | blocker `(DERIV, market-data)` não participava do gate `(DERIV, account_id)` | escopo broker-wide de market data passa a bloquear todas as contas financeiras somente daquele broker |
| `apps/core/deriv_telemetry.py` | resposta tardia de monitor antigo | geração antiga podia alterar snapshot/gate depois de substituição | cada monitor recebe geração; monitor aposentado não consulta cliente, não seta/limpa gate e não solicita recovery |
| `apps/core/lifecycle_service.py` + Launcher | pausa confundida com Core indisponível | `CoreServiceState.SAFE_STOP` era usado como saúde do processo, mas Launcher exige `READY` para disponibilidade | `READY` agora significa control plane disponível; ARM/Safe Stop ficam em estado separado |
| `apps/core/read_only_worker_supervisor.py` | restart manual | `_cleanup_connection()` esquecia handle de processo ainda vivo | restart encerra geração/processo anterior antes de criar a nova |

## Hipóteses refutadas ou parciais

- **Deadlock circular Core READY → UI → login → reconciliation:** refutado como deadlock de criação da
  UI. O Core e o serviço de UI podem subir desarmados. Era parcialmente verdadeiro como defeito de
  recovery: uma ordem Deriv persistida não disparava sozinha a sessão autenticada.
- **Delay de análise como causa principal:** refutado. O problema dominante era referência de
  conexão antiga, gates com escopo incorreto e estado de risco somente em memória.
- **Reconectar o mesmo OTP single-use:** refutado. O caminho autenticado já substituía o processo e
  obtinha um bootstrap novo. A correção foi tornar isso determinístico e não rearmar.
- **Asset pinning como corrupção:** refutado. Esperar sinal novo no mesmo ativo é a política vigente.
  Sem requisito normativo de timeout, ela foi mantida e exposta como espera, não alterada.
- **Settlement duplicado duplicava P&L:** refutado no writer atual. A nova atualização durável do
  Martingale foi inserida dentro da mesma idempotência para manter a propriedade.
- **Segunda instância como única causa:** refutado. O guard/mutex já existe; conflitos anteriores não
  explicavam a perda de liveness após substituição do worker.

## Recovery final

```text
BOOT
  → state.db: integrity + migrations + interrupted dispatch recovery
  → restore de reservas + digit_risk_runtime
  → control plane READY, UI disponível, trading DISARMED
  → se há ordem Deriv não terminal: recovery autenticado em background

DISCONNECT
  → HG_SAFE_STOP / DISARM
  → invalidar geração antiga
  → encerrar trader, pump, telemetria e processo antigos
  → backoff bounded
  → novo worker e novo bootstrap autenticado
  → reconciliation antes de registrar rota de submissão
  → restaurar risco/Martingale
  → provar clock, market stream e warm-up
  → READY_TO_ARM
  → somente ação explícita do usuário cria novo arm epoch
  → somente sinal posterior ao epoch pode abrir ordem
```

Para falha repetida do worker simulado:

```text
CLOSED → crash → restart/backoff → OPEN → espera definida → HALF_OPEN/probe
       → prova estável → CLOSED
       → nova falha → OPEN novamente
```

`UNKNOWN`, `SETTLEMENT_UNKNOWN` e conflito não usam timeout para liberar exposição. Permanecem
bloqueados até evidência reconciliada ou revisão humana.

## Projeção única de readiness

`TradingReadinessSnapshot` separa Core disponível, processo do broker, autenticação, reconciliação,
risco, relógio, market, warm-up, Safe Stop/ARM, ordem em voo, `ready_to_arm`, `ready_to_trade` e os
motivos bloqueadores. A UI inclui o gate `DERIV_READY_TO_ARM`. Safe Stop não derruba a
disponibilidade do Core e não é confundido com falha de processo.

## Health Gate audit

| Blocker | Owner/escopo | Condição de set | Condição de clear | Evidência/teste |
|---|---|---|---|---|
| `HG_SAFE_STOP` | Core/global | startup, operador, disconnect ou recovery | somente ARM explícito; os demais blockers continuam | UI safe-stop/resume; recovery sem auto-resume |
| `HG_ORDER_UNKNOWN` | writer/reconciliation, conta | envio possivelmente ocorrido sem resposta | somente evidência externa conclusiva | timeout/restart/reconciliation e chaos buy boundary |
| `HG_SETTLEMENT_UNKNOWN` | eventos/reconciliation | terminal sem settlement comprovado | evento/evidência terminal válida | reconciliation protocol e event lifecycle |
| `HG_RECONCILIATION_REQUIRED` | recovery/eventos | estado interrompido, backpressure ou gap | todos os candidatos resolvidos | storage recovery, gap fallback |
| `HG_RECONCILIATION_CONFLICT` | reconciliation/global | evidência conflita com identidade financeira | revisão humana; não há clear automático | reconciliation conflict tests |
| `HG_RECONCILIATION_UNAVAILABLE` | reconciliation/global | fonte falhou após tentativas bounded | nova reconciliação comprovada | retry/status timeout tests |
| `HG_WORKER_DISCONNECTED` | supervisor/global | processo/IPC/heartbeat perdido | handshake + estabilidade do cliente novo | worker auto-recovery e subprocess tests |
| `HG_WORKER_CIRCUIT_OPEN` | supervisor/global | crashes excedem janela | probe HALF_OPEN bem-sucedido | circuit auto-probe regression |
| `HG_WORKER_NOT_READY` | dispatcher, conta | rota/worker ausente | registro de worker pronto | coordinator/router tests |
| `HG_ORDER_EVENT_GAP` | event processor, conta | sequência externa pula evento | reconciliation daquele pedido resolve | event gap tests |
| `HG_ORDER_EVENT_CONFLICT` | event processor, conta | replay/evento incompatível | revisão/recovery explícito; não por tempo | conflicting event tests |
| `HG_BROKER_EVENT_BACKPRESSURE` | IPC/global | fila financeira saturada | replacement + reconciliation | IPC backpressure tests |
| `HG_COOLDOWN_ACTIVE` | Risk Ledger/global | limite de perdas | origem UTC + duração persistidas expiram | digit risk + restart tests |
| `HG_DAILY_STOP_REACHED` | Risk Ledger/global | P&L alcança stop | reset diário explícito | digit/global risk tests |
| `HG_DAILY_TAKE_PROFIT_REACHED` | Risk Ledger/global | P&L alcança meta | reset diário explícito | digit risk tests |
| `HG_MARKET_DATA_DISCONNECTED` | Deriv telemetry, broker | stream/probe indisponível | geração atual prova conexão | telemetry/recovery tests |
| `MD_CLOCK_UNTRUSTED` | Deriv telemetry, broker | RTT/offset/clock inválido | amostra válida da geração atual | clock recovery + scope tests |
| blockers `DB_*` | persistence/global | integridade, migration ou write falha | restart/repair comprovado, nunca conveniência | storage resilience |
| blockers Auth/lease | Auth Agent/global | sessão/lease/entitlement inválido | renovação/reautorização válida | auth/licensing tests |

Todo SET/CLEAR do `HealthGate` gera evento redigido no journal operacional persistente.

## Martingale durável

A tabela singleton `digit_risk_runtime` mantém fingerprint da política, moeda, limites, P&L diário,
perdas consecutivas, step atual, ativo pinado, perda acumulada da sequência, início UTC do cooldown,
última ordem e último settlement.

No settlement de produto de dígitos, a transação do `SingleDatabaseWriter` persiste/deduplica evento,
atualiza Order, aplica P&L, libera RiskReservation e atualiza step/pin/perdas/cooldown antes do mesmo
commit. O mesmo ocorre ao aplicar evidência de reconciliação somente quando ela produz a primeira
transição da ordem para `SETTLED`. Evento repetido, evidência repetida e uma consulta tardia com novo
`evidence_id` para uma ordem já liquidada não avançam a sequência novamente.

No restart, a configuração é validada por fingerprint, cooldown é reconstruído por
`cooldown_started_at + duração` e convertido para deadline monotônico apenas dentro do processo.
Quando step > 0, somente o símbolo persistido participa. Sem sinal desse ativo, o estado explícito é
`BOT_MARTINGALE_ASSET_PINNED`; nenhum timeout financeiro foi inventado.

## Journal operacional

`operational-journal.jsonl` é append-only, redigido e rotacionado com um arquivo anterior. Ele não é
estado financeiro autoritativo. Registra startup/recovery, worker/PID, disconnect/restart/circuit,
reconciliation, eventos financeiros, Martingale, gates, ARM/DISARM e shutdown. Token, OTP e payload
de credencial não entram nos campos aceitos.

## Testes novos ou ajustados

- persistência/idempotência/pin do Martingale após reconstrução completa do Core;
- blocker broker-wide de market data aplicado à conta Deriv e isolado de outros brokers;
- resposta de geração aposentada incapaz de alterar health;
- recovery autenticado não chama `resume_new_entries`;
- worker crash substitui cliente, reconcilia settlement e permanece desarmado;
- shutdown concorrente com recovery não deixa processo ou banco órfão;
- circuit breaker agenda probe e fecha após prova estável;
- process `READY` permanece separado de Safe Stop;
- reconciliação tardia de ordem já liquidada não duplica P&L nem step do Martingale;
- três estratégias selecionam de fato o `active_strategy_id` no teste end-to-end.

## Validação executada

- suíte local completa: **615 passed, 4 skipped, 0 failed**;
- Ruff check e Ruff format check aprovados em `apps`, `packages` e `tests`;
- mypy aprovado em **211 arquivos-fonte**;
- bytecode/compileall aprovado para `apps` e `packages`;
- `git diff --check` sem erro de whitespace.

Os quatro skips são caminhos deliberadamente opcionais ou específicos de plataforma, incluindo os
smokes externos Deriv. Nenhuma credencial foi usada e nenhuma ordem externa Demo ou Real foi enviada
durante esta auditoria.

## Riscos restantes

- testes externos Deriv Demo são opt-in; a suíte comum não prova disponibilidade da API/rede real;
- asset pinning não possui timeout e pode aguardar novo sinal do mesmo ativo, agora explicitamente;
- conflitos/ambiguidade sem evidência continuam exigindo ação humana, por design;
- o journal tem uma rotação local simples, não agregação remota;
- Real continua read-only e IQ Option externa continua fora de escopo.
