# Runbook Operacional — Fase 1 local

## 1. Escopo e advertência

Este runbook descreve o Launcher, a UI reativa MVP e harnesses locais da Fase 1. Não existe serviço
instalado, conta real ou operação financeira externa. Não use este documento para conectar
credencial real.

## 2. Objetivo operacional

Quando não for possível comprovar segurança:

1. bloquear novas entradas;
2. preservar estado/evidência;
3. manter acompanhamento e reconciliação de ordens abertas;
4. isolar o componente afetado;
5. recuperar somente por transição/evidência válida.

“Encerrar tudo” não é resposta segura se houver estado não terminal.

## 3. Startup esperado do Core

O comando local suportado é:

```powershell
python -m apps.launcher --profile-dir .\data\profiles\local --workers simulated deriv_read_only
```

O Launcher adquire `profile.lock`, cria o Job Object e inicia o host. Dentro do host, a ordem é:

```text
Auth Agent/DPAPI/handshake
→ Core instance guard + state.db + recovery
→ Simulated Worker + reconciliation/event pump
→ Deriv read-only fake
→ lifecycle READY
→ UI reativa (somente após Core/UI IPC healthy)
```

Ausência de qualquer ACK/health esperado falha o startup e limpa a árvore. O Launcher nunca abre o
banco ou a porta financeira do worker.

```text
Acquire single-instance guard
→ open/check/migrate state.db
→ recover interrupted local state
→ start worker supervisor
→ reconcile non-terminal orders
→ restore active risk reservations
→ start broker event pump
→ open Health Gate/dispatcher only if safe
```

Qualquer falha deixa `dispatcher_started=False`, encerra componentes iniciados e libera o guard.

## 4. Safe stop

`stop_new_entries()` fecha novas entradas com `HG_SAFE_STOP`. Shutdown deve:

- parar novas entradas primeiro;
- continuar/encerrar ordenadamente event pump e worker;
- persistir o que já foi confirmado;
- fechar writer;
- liberar instance guard;
- nunca converter ordem aberta/unknown em terminal por conveniência.

Na UI, “Parar novas entradas” e “Encerrar com segurança” são ações distintas. Safe stop confirma
`HG_SAFE_STOP` e mantém a janela, projeções e acompanhamento financeiro ativos. “Retomar” remove
somente esse blocker e pode ser recusado por qualquer outro motivo do Health Gate.

No Launcher, `stop_all()` executa estritamente:

1. `CORE_SAFE_STOP_REQUEST` e confirmação de `HG_SAFE_STOP`;
2. drain bounded apenas de eventos financeiros já enfileirados;
3. shutdown dos workers, mantendo o event pump/writer disponíveis durante a drenagem final;
4. shutdown do Auth Agent;
5. fechamento do Core, writer e locks;
6. timeout → `terminate()` → `kill()` e fechamento do Job Object.

Crash do Launcher fecha o Job e elimina descendentes, mas é shutdown abrupto: preserve o perfil e
deixe o próximo startup executar integridade/recovery/reconciliação. Nunca remova `profile.lock` ou
`.core.instance.lock` para contornar um processo vivo.

### 4.1 Operação da UI e falhas

O banner `MODO DEMO / PRÁTICA — SEM VALOR REAL` deve permanecer visível. Cards com
`INDISPONÍVEL` ou `NÃO COMPROVADO` indicam ausência de evidência autoritativa; não trate esses
valores como saldo zero ou relógio sincronizado. O painel de Health Gates mostra código estável e
descrição legível.

- `PARAR NOVAS ENTRADAS (SAFE STOP)`: bloqueia novas intenções; não abandona ordens abertas;
- `Retomar entradas`: somente o Core decide se todos os gates permitem reabertura;
- `Fechar com segurança`: registra o pedido no Core; o polling do Launcher inicia a escada completa;
- kill/travamento da UI: o Launcher marca a árvore `DEGRADED`, mas o Core e event pump continuam;
- desconexão IPC: não infira estado financeiro nem repita comando como se o ACK tivesse ocorrido.

O token HMAC da UI é efêmero, gerado pelo Launcher e entregue por `stdin`; não aparece em argv,
stdout, logs, banco ou projeções. Nunca copie o token para diagnóstico. A UI não abre `state.db` nem
conecta a Deriv/IQ Option. Para ensaio sem janela use somente em perfil isolado:

```powershell
python -m apps.launcher --profile-dir .\data\profiles\ui-test --workers simulated --headless-ui --auto-shutdown-after 2
```

## 5. Triagem inicial

Colete somente dados redigidos:

- data/hora UTC;
- componente e versão;
- estado do Health Gate;
- reason codes;
- broker/mode/capability;
- presença do banco e marker;
- worker health/PID;
- contadores de recovery/reconciliation;
- IDs de correlação necessários, sem payload bruto.

Não copie token, cookie, OTP, lease, banco ou resposta de autenticação.

## 6. Segunda instância

Sinal: `CORE_INSTANCE_ALREADY_RUNNING` ou `DB_LOCK_FAILED`.

Ação:

1. não remova o lock enquanto o processo dono estiver vivo;
2. confirme qual processo pertence ao perfil;
3. use o shutdown normal da instância existente;
4. após morte comprovada, o lock do sistema deve ser liberado;
5. se persistir, preserve evidência e investigue o guard; não abra outro writer.

## 7. Banco ausente ou corrompido

### Banco esperado ausente

Sinal: marker existe e `state.db` não existe; reason `DB_MISSING_UNEXPECTED`.

- não recrie banco vazio;
- não apague marker;
- preserve o diretório;
- localize backup consistente;
- ensaie restore em diretório isolado;
- reconcilie antes de novas entradas.

### Integridade/migration

Sinais: `DB_INTEGRITY_FAILED`, `DB_MIGRATION_FAILED` ou checksum mismatch.

- bloqueie startup;
- não edite migration publicada;
- execute diagnóstico/backup somente em cópia consistente;
- corrija com nova migration/versão;
- não execute SQL ad hoc no original.

## 8. Falha de escrita/disco

Sinal: `DB_WRITE_FAILED`, disk full ou I/O error.

- Health Gate deve fechar;
- nenhum dispatch novo;
- preserve processo/arquivo e espaço disponível;
- não considere operação não confirmada como persistida;
- após estabilizar storage, reinicie pelo fluxo completo de recovery/reconciliation.

## 9. Worker desconectado ou morto

Sinais: `HG_WORKER_DISCONNECTED`, `WORKER_CRASHED`, circuit open.

- não faça retry financeiro;
- supervisor encerra geração antiga;
- backoff/circuit breaker governam restart;
- Core entra em recovery/reconciliation;
- Deriv shadow perde subscriptions e exige backfill/overlap da nova geração;
- outra corretora deve permanecer isolada.

Se startup/handshake falhar, confirme cleanup do subprocesso. Não deixe worker órfão.

O Launcher pode reiniciar de forma bounded somente Auth Agent e Deriv read-only. O Simulated Worker
financeiro permanece degradado após kill; substituir seu cliente sob o Core ativo poderia ignorar a
reconciliação da geração anterior. Faça safe shutdown e novo startup completo.

## 10. Ordem `UNKNOWN`

Nunca:

- reenviar automaticamente;
- marcar rejeitada por timeout;
- liberar reserva;
- resolver por tempo decorrido;
- apagar outbox/ordem.

Faça:

1. bloquear novas entradas no escopo;
2. manter reserva/exposição;
3. consultar status/histórico pelo reconciliador;
4. validar identidade, conta, símbolo, moeda e valor;
5. aplicar somente evidência consistente;
6. encaminhar para revisão manual auditada se irrecuperável.

## 11. `SETTLEMENT_UNKNOWN`

Contrato foi potencialmente aberto/aceito, mas liquidação não é comprovada. Continue acompanhando
eventos e reconciliação. Exposição permanece até settlement válido ou revisão auditada. Evento
tardio idêntico é idempotente; conflito fecha gate.

## 12. Evento duplicado, fora de ordem ou gap

- duplicata idêntica: idempotente;
- duplicata conflitante: incidente/reconciliação;
- evento terminal tardio: não regredir estado;
- sequence gap: bloquear e reconciliar;
- scope/account/currency mismatch: não aplicar.

## 13. Lease expirada/revogada

- bloquear somente novas entradas no escopo;
- não matar worker por causa da licença;
- não interromper event pump/reconciliation/settlement;
- não liberar reserva;
- exigir renovação/reautenticação para nova entrada;
- manter broker credential fora do identity service.

## 14. Estratégia suspensa ou incompatível

- impedir nova avaliação/entrada;
- não afetar ordem existente;
- preservar strategy/version/config no histórico;
- revalidar manifesto/hash/status/entitlement;
- nunca carregar código remoto arbitrário.

## 15. Market data gap/stale/suspensão

Sinais: `MD_GAP_DETECTED`, `MD_SOURCE_STALE`, `MD_CLOCK_UNTRUSTED`, reconnect required.

- parar delivery ao runtime;
- invalidar cotação/candle parcial;
- executar backfill bounded com overlap;
- rejeitar resposta de geração antiga;
- restaurar subscription somente depois de continuidade/warm-up;
- divergência shadow/replay deixa gate `FAILED`.

## 16. Backpressure e recursos

Fila/batch saturado é reason explícito, não autorização para descarte crítico. Host/soak podem
encerrar shadow read-only em RSS/CPU/lag/recovery limit. O resultado é fail-closed e não cria ordem.

## 17. Backup

Use `DatabaseBackupService` em harness controlado. Destino novo, diferente do banco original. O
serviço publica somente após full integrity check. Não há comando de restore de produção.

O ensaio automatizado suportado é:

```powershell
python -m pytest tests/integration/test_backup_restore_drill.py -q
```

Ele opera exclusivamente em `tmp_path`, restaura para outro perfil e compara o hash do original.
Não adapte o teste para apontar ao perfil do usuário.

## 18. Soak

Execute somente em perfil/diretório de diagnóstico isolado:

```powershell
python -m apps.core.soak_cli --run-soak-matrix --duration-seconds 5 --max-cycles 100 --max-reports 10
python -m apps.core.soak_cli --run-soak-matrix --profile fast --fault-preset heavy_load
```

A matriz é local, sequencial, bounded, sintética e read-only. Verifique:

- opt-in explícito por flag ou `DUALTRADE_RUN_SOAK_MATRIX=1`;
- transportes fake por padrão;
- duração e máximo de ciclos;
- máximo de cenários/amostras;
- relatório sem segredo/payload financeiro;
- shutdown final;
- outcome agregado `FAILED` se qualquer cenário falhar.

Exit codes: `0` para matriz aprovada, `1` para matriz reprovada/falha operacional e `2` para opt-in
ausente ou argumento inválido. Não edite relatórios após a publicação. A retenção remove somente
arquivos `soak_matrix_*.json` mais antigos no diretório configurado; falha ao remover deve ser
tratada como falha operacional, nunca ignorada.
Perfis prolongados continuam opt-in. Confira no JSON que cada fault programado possui evento
`INJECTED` e correspondente `OBSERVED`/`RECOVERED`; ausência de recuperação não pode ser corrigida
manualmente no artefato. O scanner roda antes da publicação e não imprime o valor detectado.

## 19. Encerramento do incidente

- estado financeiro reconciliado ou explicitamente ainda bloqueado;
- Health Gate só reabre com evidência;
- nenhum processo órfão;
- banco íntegro/backup verificado;
- segredo rotacionado se aplicável;
- teste de regressão criado;
- documentação e worklog atualizados;
- risco residual registrado.

## 20. Escalonamento

Interrompa e solicite decisão explícita se recovery exigir:

- usar conta/credencial real;
- reclassificar ordem sem evidência;
- editar banco manualmente;
- violar isolamento de broker;
- habilitar modo real;
- abandonar ordem aberta;
- enviar dado sensível a terceiro.
