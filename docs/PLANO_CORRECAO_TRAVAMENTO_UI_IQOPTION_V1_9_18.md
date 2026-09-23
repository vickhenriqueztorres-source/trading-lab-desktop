# Plano de Correção v1.9.18 — Travamento da UI e Deadlock Operacional da IQ Option

**Status:** proposta técnica consolidada para implementação e validação  
**Data:** 2026-09-15  
**Baseline:** v1.9.11  
**Versão corretiva alvo:** v1.9.18  
**Relatório de origem:** `docs/DIAGNOSTICO_TRAVAMENTO_IQOPTION_2026-09-15.md`

## 1. Resumo executivo

Foram confirmados dois defeitos independentes, mas que se amplificam mutuamente:

1. **Travamento da interface (`AppHangB1` / `Responding=False`)**: o thread gráfico Qt executa trabalho redundante em alta frequência. O snapshot integral muda continuamente por campos voláteis, como idade do saldo, logs e telemetria; com isso, o dirty checking atual não elimina renders. O ciclo ainda reaplica QSS, executa `unpolish/polish`, recria células do radar e recalcula alturas de linhas.
2. **Deadlock operacional da IQ Option**: a reconciliação primária compara o símbolo canônico da ordem com o campo `active` retornado pela IQ Option, que pode ser um identificador numérico. O falso `IQOPTION_SYMBOL_MISMATCH` gera `CONFLICT`, mas a ordem permanece persistida como `ACCEPTED`. O scheduler deixa de reconciliá-la e o AutoTrader continua interpretando-a como exposição em voo, bloqueando novas operações indefinidamente.

A correção precisa preservar as invariantes financeiras: desligar e ligar o bot **não pode** apagar uma exposição ambígua, liberar sua reserva de risco nem fabricar um resultado. A retomada só pode ocorrer após resultado autoritativo da corretora ou revisão manual auditada com evidência.

## 2. Evidências confirmadas

### 2.1 Interface

- O processo gráfico do executável v1.9.17 foi observado com `Responding=False`.
- Amostras de cinco segundos registraram 4,08 s e 4,84 s de CPU no processo de UI, equivalentes à saturação aproximada de um processador lógico.
- O Windows registrou evento de aplicação 1002 e assinatura `AppHangB1`.
- A comparação do snapshot completo é invalidada continuamente por `balance_age_seconds`, logs operacionais, ranking e telemetria.
- Componentes ainda executam atualizações de texto, estilo e geometria no caminho quente, mesmo sem mudança semântica relevante.
- O journal chegou a aproximadamente 1,63 evento/s, com predominância de `iqoption_decision`, fazendo a projeção de logs mudar quase a cada refresh.

### 2.2 IQ Option

- A ordem de prefixo `5f945c50` possui `broker_order_id`, está em `ACCEPTED` e não possui P&L final.
- Existem tentativas de reconciliação `FAILED` e `CONFLICT` com `IQOPTION_SYMBOL_MISMATCH`.
- A consulta de candidatos exclui uma ordem após `CONFLICT`, porém a consulta de ordens não terminais continua retornando-a.
- O `IqOptionAutoTrader` interpreta a ordem como exposição ativa e retorna `IQOPTION_ORDER_IN_FLIGHT` indefinidamente.
- A rota exata `get_betinfo` pode retornar `active` numérico; o fallback de histórico já possui normalização parcial de `active_id`, mas a rota primária não usa o mesmo resolvedor.
- O `PRAGMA quick_check` do banco retornou sucesso; a causa não é corrupção física do SQLite.
- Noventa e um testes direcionados passaram, mas não cobrem a combinação crítica `CONFLICT persistido + estado da ordem + retomada do AutoTrader`.

## 3. Objetivos e não objetivos

### 3.1 Objetivos

- Eliminar falsos conflitos causados por diferenças de representação do ativo.
- Garantir que conflito genuíno resulte em estado financeiro explícito, auditável e recuperável.
- Preservar a reserva de risco até a comprovação do desfecho da ordem.
- Permitir retomada segura do AutoTrader após liquidação ou revisão manual concluída.
- Remover trabalho redundante do thread Qt e manter a interface responsiva sob carga operacional.
- Corrigir a ordem já presa sem edição manual direta do banco.
- Disponibilizar diagnóstico suficiente para suporte e auditoria.

### 3.2 Não objetivos

- Não permitir que o toggle do bot descarte ordem aceita ou ambígua.
- Não inferir P&L a partir de saldo isolado quando houver mais de uma causa possível para a variação.
- Não transformar conflito real em `REJECTED` apenas para liberar novas operações.
- Não reduzir ou ocultar eventos financeiros críticos para melhorar performance da UI.
- Não usar um catálogo estático como única autoridade para ativos dinâmicos, OTC ou sintéticos.

## 4. Decisões técnicas obrigatórias

### 4.1 Versão

A correção será padronizada como **v1.9.18** em `pyproject.toml`, título da janela, recursos/metadados do executável, informações de diagnóstico e artefatos de release. O payload interno e o nome externo do executável devem informar a mesma versão.

### 4.2 Fluxo canônico da ordem ambígua

Será adotado um único fluxo persistido:

```text
ACCEPTED ou OPEN
        |
        v
SETTLEMENT_UNKNOWN
        |
        v
RECONCILING
   |           |
   v           v
SETTLED   MANUAL_REVIEW
                |
                v
           RECONCILING
            |       |
            v       v
         SETTLED  REJECTED
```

Regras:

- Não haverá transição direta `ACCEPTED -> MANUAL_REVIEW`.
- `MANUAL_REVIEW` é não terminal e mantém a reserva de risco ativa.
- `REJECTED` após aceitação só é permitido quando houver evidência autoritativa de que a ordem não foi executada ou foi anulada sem efeito financeiro.
- Se houve execução e o resultado financeiro for conhecido, o destino é `SETTLED`, inclusive para perda.
- Uma ordem em revisão manual bloqueia novas entradas apenas no escopo financeiro apropriado, sem impedir acompanhamento, consulta, reconciliação e encerramento de ordens existentes.

### 4.3 Resolução manual tipada e auditada

Não será criado um método genérico com `target_state` arbitrário e P&L opcional. A API deve expressar a origem da decisão:

```python
resolve_with_broker_evidence(...)
confirm_not_executed(...)
record_manual_financial_settlement(...)
```

Todos os comandos devem exigir:

- `order_id`;
- versão esperada da ordem para compare-and-swap;
- `idempotency_key`;
- `correlation_id`;
- operador autenticado e identificável;
- `reason_code` enumerado e observação sanitizada;
- `evidence_id`, origem da evidência e hash do payload sanitizado;
- timestamp UTC gerado pelo Core;
- resultado financeiro em minor units quando aplicável;
- moeda coerente com a ordem;
- validação de que a reserva ainda não foi consumida ou liberada.

`confirm_not_executed` só poderá concluir em `REJECTED` com evidência negativa autoritativa. `record_manual_financial_settlement` concluirá em `SETTLED` e deverá registrar explicitamente se a evidência veio de histórico da corretora, comprovante externo ou intervenção de suporte.

### 4.4 Concorrência e precedência

- Aplicar lock lógico por `broker_account_id + order_id` durante reconciliação ou resolução manual.
- Usar versão da ordem e atualização condicional no banco; uma versão vencida deve retornar conflito de concorrência, nunca sobrescrever o estado atual.
- Evidência tardia e autoritativa da corretora tem precedência sobre uma revisão ainda não concluída.
- Depois de um estado terminal, mensagens repetidas devem ser idempotentes. Uma divergência posterior deve gerar incidente auditável, sem aplicar P&L ou liberar reserva pela segunda vez.
- Duplo clique, retry IPC, reinício do Core e timeout do cliente não podem duplicar o lançamento financeiro.

## 5. Bloco P0-A — Identidade robusta de ativos IQ Option

### 5.1 `packages/brokers/iqoption/community_read_only.py`

Implementar um resolvedor de identidade bidirecional, associado à geração da sessão:

```python
ActiveIdentityResolver
  resolve_symbol(raw_active, generation, product_kind) -> CanonicalActive
  get_active_id(symbol, generation, product_kind) -> int | None
  get_symbol(active_id, generation, product_kind) -> str | None
```

O objeto canônico deve preservar:

- símbolo solicitado pelo Core;
- símbolo informado pela corretora;
- `active_id` bruto;
- símbolo canônico normalizado;
- tipo de produto/mercado, como binary, turbo, digital, forex ou OTC;
- geração da conexão/catálogo;
- fonte e instante da resolução.

Requisitos:

- O catálogo dinâmico da sessão é a fonte primária.
- `IQOPTION_ACTIVE_IDS` pode existir apenas como fallback versionado e diagnosticável.
- O cache deve ser invalidado em reconnect, troca de conta e mudança de geração do catálogo.
- Detectar colisões: o mesmo número não pode ser aceito silenciosamente para símbolos diferentes na mesma geração e produto.
- Ativos como `SPX/GOLD`, OTC e instrumentos dinâmicos não podem depender exclusivamente de tabela estática.
- `get_betinfo`, `get_options`, eventos `option-closed` e histórico devem consumir o mesmo resolvedor.

### 5.2 `apps/iqoption_worker/reconciliation.py`

Substituir a comparação textual direta por identidade canônica:

- validar primeiro `broker_order_id` exato;
- resolver o `active` bruto dentro da mesma geração de sessão;
- comparar símbolo canônico, produto, conta e demais campos disponíveis;
- normalizar direção e moeda sem depender de caixa ou separadores;
- preservar o valor bruto no envelope de evidência sanitizado;
- retornar `FOUND` apenas quando a identidade for compatível;
- retornar resposta incompleta/retryable quando o catálogo ainda não puder resolver o identificador;
- retornar conflito somente quando houver incompatibilidade positiva, não por ausência temporária de mapeamento.

O contrato exato por `broker_order_id` é evidência forte, mas não autoriza ignorar uma divergência real de conta, produto ou identificador. A resposta deve carregar confiança e campos usados na validação.

### 5.3 Protocolo do worker

Versionar o payload de reconciliação para transportar:

- `raw_active`;
- `canonical_symbol`;
- `active_id`;
- `product_kind`;
- `catalog_generation`;
- `resolution_source`;
- `broker_order_id`;
- `broker_observed_at_utc`;
- `evidence_hash`.

Payloads antigos devem continuar legíveis durante a janela de compatibilidade, mas não podem produzir falsa certeza quando faltarem campos.

## 6. Bloco P0-B — Persistência, reconciliação e revisão manual

### 6.1 `packages/persistence/writer.py`

Atualizar `ALLOWED_TRANSITIONS` somente com a cadeia canônica da seção 4.2.

Criar operações explícitas, evitando booleanos vagos como `transition_order=True`:

- `mark_settlement_unknown(...)`;
- `start_order_reconciliation(...)`;
- `record_reconciliation_success(...)`;
- `record_reconciliation_conflict(...)`;
- `resolve_with_broker_evidence(...)`;
- `confirm_not_executed(...)`;
- `record_manual_financial_settlement(...)`.

`record_reconciliation_conflict` deve executar em uma única transação SQLite:

1. validar o estado e a versão atuais da ordem;
2. concluir a tentativa de reconciliação como `CONFLICT`;
3. transicionar `RECONCILING -> MANUAL_REVIEW`;
4. manter a reserva de risco como `ACTIVE`;
5. registrar evento financeiro/outbox e evidência sanitizada;
6. atualizar a versão da ordem;
7. confirmar a transação.

Os métodos de resolução devem, também em uma única transação:

1. validar autorização, evidência, versão e idempotência;
2. mover `MANUAL_REVIEW -> RECONCILING`;
3. persistir a decisão e o vínculo com a evidência;
4. aplicar o P&L exatamente uma vez quando houver settlement;
5. atualizar métricas/risk ledger exatamente uma vez;
6. liberar ou consumir a reserva exatamente uma vez;
7. concluir em `SETTLED` ou `REJECTED`;
8. publicar evento/outbox após commit.

### 6.2 Migração do banco

Criar uma migração versionada para:

- adicionar campos de versão/idempotência/evidência se ainda não existirem;
- suportar o estado `MANUAL_REVIEW` sem alterar registros terminais;
- criar índices para estado, broker, conta, tentativa e chave de idempotência;
- validar que não existem duas resoluções financeiras aplicadas à mesma ordem;
- manter rollback transacional da migração em caso de falha.

Não executar `UPDATE` manual na base operacional. O reparo de dados deve usar um comando de recuperação idempotente do Core, com backup anterior e trilha de auditoria.

### 6.3 `packages/persistence/reader.py`

Separar conceitos que hoje aparecem misturados:

- `list_active_financial_exposures()`: qualquer ordem cuja exposição ainda não tenha desfecho comprovado, incluindo `MANUAL_REVIEW`;
- `list_automatic_reconciliation_candidates()`: apenas ordens elegíveis para tentativa automática;
- `list_manual_review_orders()`: fila explícita para o operador;
- `list_nonterminal_orders()`: visão administrativa ampla, sem ser usada como sinônimo de candidato automático.

O AutoTrader deve bloquear novas entradas consultando exposição financeira ativa, e o scheduler deve consultar candidatos automáticos. Uma ordem em revisão continua bloqueando com motivo específico, mas deixa de gerar polling infinito.

### 6.4 `apps/core/reconciliation.py`

- Persistir toda passagem por `SETTLEMENT_UNKNOWN` e `RECONCILING`.
- Em conflito genuíno, chamar `record_reconciliation_conflict`, sem depender de flag booleana.
- Classificar respostas em pelo menos `FOUND`, `NOT_FOUND_AUTHORITATIVE`, `NOT_FOUND_RETRYABLE`, `INCOMPLETE`, `CONFLICT` e `TRANSPORT_ERROR`.
- Diferenciar ausência temporária de catálogo de incompatibilidade real.
- Bloquear `HG_RECONCILIATION_CONFLICT` no escopo correto e emitir evento sanitizado.
- Continuar acompanhando ordens abertas mesmo com licença expirada ou revogada, conforme as regras do projeto.

### 6.5 `apps/core/iqoption_auto_trader.py`

- Expor `IQOPTION_BOT_ARMED_REVIEW_REQUIRED` quando houver revisão pendente.
- Não tratar `MANUAL_REVIEW` como ordem automaticamente reconciliável.
- Preservar o bloqueio de novas entradas até resolução segura.
- Após o settlement/rejeição auditada, reconstruir o estado do gate e permitir novo sinal, se todos os demais gates estiverem abertos.
- Rate-limitar somente decisões repetitivas e não financeiras. Eventos de ordem, settlement, conflito, reserva, P&L, autenticação e segurança nunca devem ser descartados.

O rate limit deve registrar `suppressed_count`, primeira e última ocorrência, emitindo imediatamente quando o motivo ou o estado mudar.

## 7. Bloco P0-C — Recuperação da ordem já presa

Adicionar um comando administrativo versionado e idempotente, executado pelo Core, para importar a ordem existente ao novo fluxo.

Procedimento:

1. colocar o sistema em Safe Stop para novas entradas;
2. criar backup verificável do banco e registrar hash/timestamp;
3. executar a migração de schema;
4. localizar a ordem completa pelo UUID, nunca apenas pelo prefixo `5f945c50`;
5. validar `broker_order_id`, conta, moeda, stake, reserva e tentativas existentes;
6. converter o estado usando a máquina de estados e o writer, preservando a reserva;
7. consultar a IQ Option com o resolvedor corrigido;
8. se houver resultado autoritativo, liquidar normalmente;
9. se persistir conflito verdadeiro, concluir em `MANUAL_REVIEW`;
10. reconstruir gates, projeções e outbox após restart;
11. comprovar que o reparo repetido é no-op e não duplica P&L.

O runbook deve incluir recuperação após crash em cada ponto do processo e instruções para restaurar o backup caso a migração não conclua.

## 8. Bloco P0-D — Performance e protocolo da UI

### 8.1 Revisões semânticas no protocolo

Atualizar `packages/protocol/ui_messages.py`, `apps/core/ui_service.py` e `INTERFACE_CONTRACTS.md` para fornecer:

- `snapshot_revision` global;
- revisões por fatia: estado global, cartões, radar, ordens, atividade e autenticação;
- cursor monotônico de logs;
- endpoint/consulta `logs_after_sequence(sequence, limit)`;
- `balance_observed_at_utc`, em vez de idade recalculada pelo Core em todo snapshot;
- limites explícitos e paginação para atividade.

As revisões devem mudar apenas quando o conteúdo semântico daquela fatia mudar. O timestamp de geração do snapshot não deve invalidar todos os componentes.

### 8.2 `apps/ui/app.py`

- Remover `controller.auth_status()` síncrono do startup; usar status em cache e atualização assíncrona.
- Substituir a igualdade do snapshot integral por revisões por fatia.
- Atualizar somente a página visível; ao trocar de página, aplicar imediatamente a última revisão daquela fatia.
- Calcular localmente a idade visual do saldo a partir de `balance_observed_at_utc`, sem pedir novo snapshot nem rerenderizar o card inteiro.
- Instrumentar duração de cada ciclo de UI, fila pendente e quantidade de widgets alterados.

Enquanto o protocolo versionado não estiver disponível, assinaturas locais podem ser usadas como compatibilidade temporária, mas não devem ser a arquitetura final.

### 8.3 `apps/ui/components/broker_card.py`

- Manter assinatura do conteúdo renderizado.
- Retornar sem chamadas Qt quando texto, cor, conexão, conta, saldo e confirmação não mudarem.
- Separar atualização do cronômetro visual de freshness da atualização estrutural do card.
- Não reaplicar `setStyleSheet` em conteúdo idêntico.

### 8.4 `apps/ui/components/iqoption_workspace.py`

- Alterar `objectName` apenas quando a classe visual mudar.
- Executar `unpolish/polish` somente nessa mudança.
- Não reformatar saldo, conexão ou freshness quando o respectivo valor não mudou.

### 8.5 `apps/ui/components/iqoption_asset_radar.py`

- Definir altura fixa de linha no `__init__`.
- Remover `resizeRowsToContents()` do caminho de refresh.
- Criar itens uma vez e atualizar somente células modificadas.
- Evitar sorting/layout enquanto o lote é aplicado; restaurar ao final, quando necessário.
- Aplicar mudanças de foreground/background somente quando a cor efetivamente mudar.
- Atualizar pills e estilos apenas na mudança de estado.

### 8.6 `apps/ui/pages/overview_page.py`

- Cachear texto e cor dos indicadores.
- Atualizar radar e ordens por revisão independente.
- Não recalcular formatação, geometria ou estilo em refresh sem mudança.

### 8.7 `apps/core/ui_service.py`

- Manter buffer limitado de logs com sequência monotônica.
- Retornar deltas a partir do cursor, em vez de reconstruir e retransmitir toda a lista.
- Impor limite por resposta e indicar truncamento/lacuna de cursor.
- Separar telemetria de alta frequência de eventos financeiros.

## 9. Interface de revisão manual

A v1.9.18 deve disponibilizar revisão na aba **Atividade/IQ Option**; CLI administrativa pode existir como suporte secundário, não como única interface.

A tela deve mostrar:

- identificação da ordem, sem segredos;
- conta/modo, símbolo, produto, direção, stake e horário;
- último estado conhecido;
- tentativas de reconciliação e motivo estável;
- evidências disponíveis e sua origem;
- impacto da reserva de risco;
- ações permitidas conforme o tipo de evidência.

Antes de concluir, exigir confirmação explícita e reapresentar o efeito financeiro. A UI envia um comando tipado; ela não escolhe livremente qualquer estado de destino. Erros de versão vencida devem recarregar a ordem e informar que o estado mudou.

## 10. Observabilidade e códigos estáveis

Adicionar ou padronizar códigos:

- `IQOPTION_ACTIVE_ID_UNRESOLVED`;
- `IQOPTION_ACTIVE_ID_COLLISION`;
- `IQOPTION_EVIDENCE_INCOMPLETE`;
- `IQOPTION_RECONCILIATION_CONFLICT`;
- `IQOPTION_MANUAL_REVIEW_REQUIRED`;
- `IQOPTION_MANUAL_REVIEW_STALE_VERSION`;
- `IQOPTION_RESOLUTION_ALREADY_APPLIED`;
- `UI_SNAPSHOT_CURSOR_GAP`;
- `UI_EVENT_LOOP_SLOW_CYCLE`.

As métricas mínimas devem incluir:

- duração P50/P95/P99 do refresh Qt;
- contagem de widgets alterados por ciclo;
- tamanho e atraso da fila de eventos;
- logs enviados, suprimidos e descartados por limite;
- ordens por estado de reconciliação;
- idade da revisão manual mais antiga;
- tentativas de resolução duplicadas ou rejeitadas por versão.

## 11. Plano de testes automatizados

### 11.1 Identidade e reconciliação IQ Option

- `active` inteiro, string numérica e símbolo textual convergem para o mesmo ativo.
- Símbolos com separadores, caixa e sufixo OTC são normalizados corretamente.
- `SPX/GOLD` e ativo dinâmico são resolvidos pelo catálogo de sessão.
- Colisão de `active_id` em uma geração gera erro fechado.
- Reconnect invalida o catálogo antigo.
- `broker_order_id` correto com ativo compatível resulta em `FOUND`.
- Divergência genuína de ativo, conta, produto, moeda ou direção resulta em conflito.
- Ausência temporária de catálogo não vira conflito permanente.
- Resultado de ganho, perda, empate/zero e cancelamento é interpretado corretamente.

### 11.2 Estado financeiro e idempotência

- `CONFLICT` e `MANUAL_REVIEW` são persistidos na mesma transação.
- Falha antes do commit não deixa tentativa e ordem divergentes.
- Falha depois do commit é recuperada pela outbox sem repetir P&L.
- Reserva permanece ativa durante `SETTLEMENT_UNKNOWN`, `RECONCILING` e `MANUAL_REVIEW`.
- Settlement libera/consome a reserva e aplica P&L exatamente uma vez.
- `confirm_not_executed` falha sem evidência negativa autoritativa.
- Duplo clique e retry com a mesma chave de idempotência retornam o mesmo resultado.
- Chave diferente contra versão antiga falha por concorrência.
- Resultado da corretora chegando durante revisão manual é serializado corretamente.
- Resultado tardio após conclusão divergente gera incidente, não segundo lançamento.
- Restart reconstrói ordens, reservas e gates sem desbloqueio indevido.
- Expiração/revogação de licença bloqueia entrada nova, mas não interrompe reconciliação.

### 11.3 Scheduler e AutoTrader

- Ordem em `MANUAL_REVIEW` não volta ao scheduler automático.
- A mesma ordem permanece em exposição financeira ativa.
- O status específico de revisão substitui o spam de `ORDER_IN_FLIGHT`.
- O log repetitivo é agregado com `suppressed_count`.
- Eventos financeiros não são suprimidos.
- Após resolução válida, o próximo sinal elegível pode ser operado.
- Toggle off/on não altera a ordem, a reserva nem o bloqueio financeiro.

### 11.4 UI e protocolo

- Snapshot sem mudança semântica não chama setters de widgets.
- Idade do saldo muda visualmente sem atualizar o card inteiro.
- Página oculta não é renderizada; ao abrir, recebe o estado mais recente.
- Cursor de logs retorna somente deltas e trata lacuna/truncamento.
- Radar reutiliza itens e não chama `resizeRowsToContents` no refresh.
- `polish/unpolish` só ocorre em mudança visual real.
- Startup não executa auth síncrono no thread Qt.
- Uma tempestade de telemetria não impede clique, navegação ou redimensionamento.

### 11.5 Comandos de validação

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

Além da suíte completa, criar testes focados em normalização, máquina de estados, crash points, idempotência, concorrência, migração e revisões/deltas da UI.

## 12. Soak test e critérios de aceite

Executar soak de **no mínimo 30 minutos**, preferencialmente no hardware equivalente ao incidente: Windows 10, 2 cores/4 processadores lógicos e 8 GB de RAM.

Cenário:

- Deriv e IQ Option conectadas;
- radar ativo e sinais/telemetria a pelo menos 2 eventos/s;
- alternância periódica de todas as abas;
- redimensionamento da janela;
- reconnect da IQ Option e renovação do catálogo;
- atualização de saldos e ordens;
- uma reconciliação bem-sucedida e um conflito genuíno controlado.

Critérios obrigatórios:

- nenhuma ocorrência de Windows Application Hang 1002/AppHangB1;
- janela `Responding=True` durante toda a execução;
- P95 do ciclo Qt abaixo de 16 ms e P99 abaixo de 50 ms;
- nenhuma operação Qt bloqueante no thread gráfico;
- crescimento de memória estabilizado, sem crescimento contínuo de itens/logs;
- nenhuma repetição de P&L ou reserva após restart;
- falso mismatch por `active_id` igual a zero nos casos cobertos;
- conflito genuíno termina em `MANUAL_REVIEW` com reserva ativa;
- retomada de novas entradas somente após desfecho auditado;
- toggles não removem bloqueio de exposição ambígua.

O alvo de CPU deve ser medido no mesmo hardware e com cenário reproduzível. Um limite absoluto de 5% pode ser registrado como objetivo, mas a aprovação principal será baseada em responsividade, latência do event loop e ausência de saturação sustentada.

## 13. Rollout e rollback

### 13.1 Antes do release

- atualizar `INTERFACE_CONTRACTS.md`, `PERSISTENCE_AND_RECOVERY.md`, `ERROR_AND_HEALTH_CODES.md`, `OPERATIONS_RUNBOOK.md`, `TEST_PLAN.md`, `docs/README.md` e `WORKLOG.md`;
- gerar backup e validar restauração em cópia;
- testar migração a partir de uma base v1.9.17 com a ordem presa;
- verificar que executável, payload, título e diagnóstico exibem v1.9.18;
- revisar logs e fixtures para impedir exposição de token, credencial, device key e payload bruto sensível.

### 13.2 Implantação

1. aplicar primeiro em Practice/Demo;
2. manter Safe Stop para entradas reais durante migração e primeira reconciliação;
3. executar recovery e verificar fila de revisão manual;
4. concluir soak e validar métricas;
5. liberar Real gradualmente;
6. monitorar conflitos, event-loop, reservas e P&L duplicado.

### 13.3 Rollback

- O binário anterior pode ser restaurado somente com schema compatível ou restauração do backup correspondente.
- Nunca fazer downgrade de schema silencioso.
- Se a migração concluir mas a aplicação falhar, manter novas entradas bloqueadas e continuar preservando ordens/reservas até restauração controlada.
- Registrar toda restauração como evento operacional auditado.

## 14. Ordem recomendada de implementação

1. Congelar contratos e máquina de estados da reconciliação.
2. Implementar resolvedor dinâmico de identidade e protocolo de evidência.
3. Implementar writer transacional, idempotência, CAS e consultas separadas.
4. Implementar migração e comando de recuperação da ordem presa.
5. Ajustar scheduler, HealthGate e AutoTrader.
6. Criar interface de revisão manual.
7. Versionar revisões/deltas da UI e otimizar componentes Qt.
8. Adicionar testes de crash, concorrência, restart e performance.
9. Executar recuperação em cópia do banco e soak de 30 minutos.
10. Gerar e validar o release v1.9.18.

## 15. Definition of Done

A correção só estará concluída quando:

- a rota primária e os fallbacks usam a mesma identidade de ativo;
- a ordem presa foi recuperada por fluxo idempotente e auditado;
- nenhum conflito deixa ordem em estado implícito ou invisível ao operador;
- a reserva permanece correta em todos os crash points;
- P&L, métricas e liberação de reserva ocorrem exatamente uma vez;
- toggle do bot não contorna exposição ambígua;
- a UI permanece responsiva durante o soak no hardware-alvo;
- protocolo e documentação refletem revisões, cursores e estados novos;
- a suíte completa, lint, formatação, tipagem e compilação passam;
- o executável v1.9.18 reproduz a versão correta interna e externamente;
- riscos residuais e qualquer limitação da API comunitária da IQ Option estão documentados.

## 16. Riscos residuais

- A API comunitária da IQ Option pode mudar payloads sem aviso. O parser deve falhar fechado, preservar o bruto sanitizado e gerar código estável de diagnóstico.
- Ativos dinâmicos podem mudar por sessão; por isso, o catálogo precisa ser associado à geração da conexão e não reutilizado indefinidamente.
- Resultado manual continua sendo uma operação sensível. Mesmo com evidência, CAS e idempotência, deve permanecer restrito a operador autorizado e auditado.
- Otimizações da UI não substituem limites de telemetria. Um produtor descontrolado ainda precisa de agregação e backpressure fora do thread Qt.
- O soak reduz o risco de recorrência, mas não comprova comportamento de longo prazo. Após release, as métricas de responsividade e reconciliação devem continuar monitoradas.

