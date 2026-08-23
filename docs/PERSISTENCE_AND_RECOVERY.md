# Persistência e Recovery

## 1. Autoridade

O Trading Core é a única autoridade sobre estado financeiro local. `SingleDatabaseWriter` é o único
componente autorizado a gravar `state.db`. UI, workers e estratégias não acessam esse banco para
escrita.

Market data e evidência de estratégia ficam em `strategy_data.db`, separado para não competir com
o caminho financeiro crítico.

## 2. Bancos

| Banco | Conteúdo | Escritor |
|---|---|---|
| `state.db` | intenção, reserva, outbox, ordem, eventos e reconciliação | Core/Single Database Writer |
| `strategy_data.db` | candles, journal, replay e checkpoint | repository de estratégia no Core |
| `simulated_broker_state.db` | estado sintético do broker fake | simulated worker |

O banco do broker simulado não é autoridade financeira do produto; ele existe para testar
reconciliação após restart do worker.

## 3. Configuração SQLite crítica

Conexão writer:

- `foreign_keys=ON`;
- `journal_mode=WAL`;
- `synchronous=FULL`;
- `busy_timeout=5000 ms`;
- transações explícitas;
- uma instância de Core por perfil.

Reader abre em modo read-only/query-only. Startup usa `quick_check`; diagnóstico e backup usam
`integrity_check`.

`state.db.expected` diferencia primeiro uso legítimo de desaparecimento inesperado. Marker presente
sem banco fecha o startup com `DB_MISSING_UNEXPECTED`; o sistema não recria silenciosamente um
banco crítico perdido.

## 4. Schema financeiro atual

Migrações publicadas:

| Versão | Nome | Finalidade |
|---:|---|---|
| 1 | `0001_initial_state` | intenção, reserva, outbox, ordem e eventos processados |
| 2 | `0002_outbox_state_reason` | reason code do estado da outbox |
| 3 | `0003_reconciliation` | evidências e tentativas de reconciliação |
| 4 | `0004_broker_order_events` | eventos normalizados e efeitos de settlement |

Tabelas principais:

- `trade_intents`;
- `risk_reservations`;
- `outbox_messages`;
- `orders`;
- `processed_order_events`;
- `reconciliation_evidence`;
- `reconciliation_attempts`;
- `broker_order_events`;
- `schema_migrations`.

Constraints e índices protegem IDs, relacionamento e uma reserva ativa por escopo. O código das
migrações em `packages/persistence/migrations.py` é a fonte autoritativa.

## 5. Schema de estratégia atual

Migração `0001_strategy_evidence` cria:

- `candles`;
- `decision_events`;
- `replay_runs`;
- `warmup_checkpoints`;
- `strategy_schema_migrations`.

Candles são únicos por stream/fechamento. Conteúdo divergente não é sobrescrito. Eventos do journal
e checkpoints são append-only pela API, com hashes canônicos.

## 6. Unidade financeira obrigatória

Antes de qualquer dispatch, uma transação confirma:

```text
TradeIntent
+ RiskReservation ACTIVE
+ OutboxMessage PENDING
+ projeção inicial da Order
```

Falha em qualquer etapa faz rollback completo. Não existe comando financeiro válido apenas em
memória.

## 7. Claim e ambiguidade

O dispatcher faz claim transacional da outbox. Estados distinguem:

- nunca enviado;
- em dispatch;
- enviado/aceito;
- ambíguo;
- cancelado antes de claim.

Se o processo cai com claim em andamento ou perde conexão depois de possível envio, recovery não
repete a ordem. O estado vira ambíguo/`UNKNOWN`, a reserva permanece ativa e o Health Gate bloqueia
novas entradas no escopo.

Deadline expirado pode cancelar somente mensagem nunca claimed. Tempo decorrido nunca resolve
`UNKNOWN` ou `SETTLEMENT_UNKNOWN`.

## 8. Startup e recovery

Ordem resumida do `CoreRuntime.start()`:

1. adquirir `CoreInstanceGuard`;
2. abrir writer e verificar presença/migrations/integridade;
3. abrir reader;
4. recuperar outbox/ordens interrompidas;
5. iniciar worker/supervisor;
6. reconciliar estados não terminais;
7. restaurar reservas ativas no Risk Ledger;
8. iniciar processamento de eventos;
9. abrir dispatch somente se Health Gate permitir.

Falha em qualquer ponto limpa componentes iniciados, encerra worker quando aplicável, fecha writer e
libera o guard. Startup incompleto não fica `READY`.

## 9. Reconciliação por evidência

Reconciliation consulta o worker/broker simulado usando identidade/correlação preservadas. Resultado
precisa ser consistente com conta, broker, símbolo, moeda, stake e estado. Conflito não é escolhido
por conveniência: mantém bloqueio e registra reason code.

Resultados possíveis incluem resolved, idempotent, not found, unavailable, timeout, external
unknown e manual review. Apenas evidência terminal válida pode liberar reserva conforme a máquina
de estados.

## 10. Eventos e settlement

Eventos normalizados possuem sequência, identidade e proveniência. Duplicata idêntica é idempotente;
duplicata conflitante, gap ou scope mismatch bloqueia. Settlement aplica ordem, reserva, P&L/efeito
e registro do evento na mesma unidade transacional.

Estado terminal não regride por evento atrasado.

## 11. Commit de candle e checkpoint

O candle bruto pode ser persistido antes da decisão. Para cada candle entregue, journal completo da
decisão e `StrategyStateV1` correspondente são confirmados juntos. Crash antes do commit reprocessa
o candle; crash depois começa no próximo. O hash final deve coincidir com replay limpo.

## 12. Backup

`DatabaseBackupService` usa SQLite Backup API através do writer, grava arquivo `.partial`, executa
`integrity_check` e publica com `os.replace`. Regras:

- destino deve ser diferente do source;
- destino existente não é sobrescrito;
- arquivo parcial é removido na falha;
- não copie manualmente `state.db`, `-wal` e `-shm`;
- backup não substitui teste de restore.

Restore de produção ainda não possui comando operacional. Qualquer ensaio deve ocorrer em diretório
isolado, nunca sobre o único perfil original.

O harness `tests/integration/test_backup_restore_drill.py` formaliza esse ensaio: fecha o Core de
origem, preserva seu hash, torna o arquivo original temporariamente indisponível dentro de
`tmp_path`, copia apenas o backup publicado para outro perfil, cria o marker esperado, executa
`quick_check` e `integrity_check` e inicia um Core independente. Migrations, intenção, reserva,
outbox e ordem são comparadas exatamente; ao final, o arquivo original volta ao mesmo caminho com o
mesmo hash. Isso é prova de recuperação, não um comando de restore de produção.

## 13. Matriz de falha

| Falha | Comportamento esperado |
|---|---|
| segunda instância | bloqueada antes do banco/dispatcher |
| banco esperado ausente | startup falha fechado |
| corrupção/checksum | startup/diagnóstico falha fechado |
| I/O/write failure | Database Health falha e dispatch bloqueia |
| crash antes do commit | nenhuma parcialidade durável |
| crash após commit | estado reaparece no restart |
| crash durante dispatch | ambiguidade + reserva ativa |
| worker morto | disconnect/recovery/reconciliação explícitos |
| evento duplicado | idempotente se idêntico |
| evento conflitante/gap | gate fechado e reconciliação |
| lease expirada | novas entradas bloqueadas; recovery continua |

## 14. Ações proibidas

- editar migration publicada;
- gravar `state.db` por UI/worker/script;
- executar SQL manual para reclassificar ordem;
- liberar reserva por timeout;
- apagar histórico inconsistente;
- copiar banco live como backup;
- restaurar sobre o único original sem verificação;
- usar `float` para dinheiro;
- iniciar segundo Core para “destravar” o primeiro.

## 15. Testes relacionados

- `tests/integration/test_persistence_and_dispatch.py`;
- `tests/integration/test_storage_resilience.py`;
- `tests/integration/test_backup_restore_drill.py`;
- `tests/integration/test_reconciliation_protocol.py`;
- `tests/integration/test_order_event_lifecycle.py`;
- `tests/integration/test_strategy_data_persistence.py`;
- `tests/chaos/test_process_crash_recovery.py`;
- `tests/chaos/test_strategy_replay_crash_recovery.py`;
- `tests/chaos/test_shadow_pipeline_crash_recovery.py`.
