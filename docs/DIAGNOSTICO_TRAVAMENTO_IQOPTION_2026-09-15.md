# Relatório de diagnóstico — travamento da UI e bloqueio infinito da IQ Option

**Projeto:** Trading Lab Desktop  
**Executável auditado:** `TradingLab-Desktop-v1.9.17-PRO.exe`  
**Baseline interna declarada pelo processo:** `1.9.11.0`  
**Data do diagnóstico:** 2026-09-15  
**Classificação:** incidente crítico de disponibilidade da UI + incidente crítico de continuidade operacional IQ Option  
**Escopo desta etapa:** diagnóstico e plano de correção; nenhuma credencial foi lida, exibida ou incluída neste relatório.

## 1. Resumo executivo

Foram confirmados dois defeitos independentes, que se agravam mutuamente:

1. **A interface Qt entra em saturação do thread gráfico.** O Windows marcou a janela como `Responding=False`; em duas amostras de cinco segundos, o processo da UI consumiu respectivamente **4,08 s** e **4,84 s** de CPU, equivalentes a aproximadamente **82% e 97% de um núcleo lógico**. A memória da UI permaneceu próxima de 109–112 MB, portanto o sintoma principal não é vazamento de memória: é trabalho excessivo e repetitivo no event loop gráfico.
2. **Uma ordem IQ Option permanece indefinidamente como `ACCEPTED`.** A ordem de prefixo `5f945c50` recebeu `IQOPTION_SYMBOL_MISMATCH`; a tentativa de reconciliação foi marcada como `CONFLICT`, mas o estado persistido da ordem não foi movido para um estado coerente de revisão. O reconciliador deixa de consultá-la, enquanto o AutoTrader continua encontrando-a em `list_nonterminal_orders()` e a considera uma operação em voo. Resultado: o robô fica infinitamente sem operar, mesmo após desligar e ligar.

O executável v1.9.17 não deve ser tratado como correção definitiva. O próprio Windows registrou `AppHangB1`, e a instância v1.9.17 observada continuou não responsiva durante o diagnóstico.

## 2. Conclusão objetiva

### 2.1 Por que o aplicativo trava

O mecanismo de atualização tenta evitar redesenhos com:

```python
if snapshot == self._last_snapshot and connected == self._last_connected:
    return
```

Esse teste quase nunca é verdadeiro em operação real porque o snapshot contém campos voláteis:

- `balance_age_seconds`, que muda continuamente;
- `operational_logs`, atualizados em alta frequência;
- ranking/telemetria da IQ Option, que acompanha o mercado.

Com isso, a cada 500 ms a UI volta a executar atualizações globais e da aba ativa. Ainda ocorrem:

- `setStyleSheet()` repetido;
- `style().unpolish()` e `style().polish()` repetidos;
- atualização de cartões das duas corretoras mesmo quando a página não precisa ser redesenhada;
- recriação de células do radar quando o ranking muda;
- `resizeRowsToContents()` e novo cálculo de altura da tabela no hot path;
- cópia e comparação do snapshot completo, incluindo até 160 logs operacionais.

A otimização anterior de “renderizar somente a aba ativa” reduziu parte do custo, mas não resolveu o gatilho principal nem o trabalho global feito antes da seleção da aba.

### 2.2 Por que a IQ Option para para sempre

O fluxo atual possui duas listas com semânticas diferentes:

- `StateReader.list_reconciliation_candidates()` exclui ordens com tentativa `CONFLICT`;
- `StateReader.list_nonterminal_orders()` inclui qualquer ordem cujo estado não seja `SETTLED` ou `REJECTED`.

Depois do conflito, a ordem `5f945c50` continuou como `ACCEPTED`. Assim:

1. o scheduler não tenta mais reconciliá-la;
2. o AutoTrader chama `_has_nonterminal_iq_order()`;
3. `ACCEPTED` faz parte de `exposure_states`;
4. a avaliação retorna `IQOPTION_ORDER_IN_FLIGHT` e nenhuma nova ordem pode ser criada;
5. desligar/ligar altera a intenção do operador, mas não altera corretamente o estado financeiro da ordem.

Esse comportamento infinito não é um problema de botão. É uma inconsistência entre a máquina de estados, a persistência da reconciliação e o gate do AutoTrader.

## 3. Evidências coletadas

### 3.1 Processo e responsividade

Árvore observada: um launcher portátil e sete processos `TradingLab.exe`, de acordo com a arquitetura multiprocesso.

| Evidência | Resultado |
|---|---:|
| Processo da janela, primeira amostra | PID 3004, `Responding=False`, 4,08 s de CPU em 5 s |
| Processo da janela, segunda instância/amostra | PID 10780, `Responding=False`, 4,84 s de CPU em 5 s |
| Memória da UI | aproximadamente 109–112 MB |
| Processos auxiliares | responsivos |
| Evento Windows | `Application Hang`, ID 1002 |
| Windows Error Reporting | `AppHangB1`, Hang Signature `13f3` |

O Windows registrou que `TradingLab.exe` “parou de interagir com o Windows e foi fechado”. A janela atualmente observada também permaneceu `Responding=False`.

### 3.2 Ambiente

| Item | Valor |
|---|---|
| Sistema | Windows 10 Home Single Language, build 19045 |
| CPU | Intel Core i5-7200U, 2 núcleos / 4 lógicos |
| RAM | 7,9 GB |
| RAM livre durante a coleta | aproximadamente 1,5 GB |

O hardware amplifica o impacto, mas não é a causa-raiz. Uma UI com polling bounded não deve saturar um núcleo em repouso, independentemente de a máquina possuir dois ou oito núcleos.

### 3.3 Taxa de telemetria

Nos 60 segundos finais da amostra do journal:

- 98 eventos operacionais;
- 97 eram `iqoption_decision`;
- taxa aproximada de 1,63 evento/s.

Como o controller busca projeção a cada 500 ms, a tupla de logs muda na maioria dos ciclos. Isso invalida a comparação de snapshot e reabre o caminho caro de renderização.

### 3.4 Integridade do banco

O `PRAGMA quick_check` retornou `ok`. Não há evidência de corrupção física do SQLite.

Contagem observada:

| Broker | Estado | Quantidade |
|---|---|---:|
| Deriv | `SETTLED` | 4.496 |
| Deriv | `REJECTED` | 306 |
| IQ Option | `SETTLED` | 164 |
| IQ Option | `REJECTED` | 339 |
| IQ Option | `ACCEPTED` | **1** |

A única ordem IQ Option não terminal é justamente a ordem presa.

### 3.5 Ordem IQ Option presa

Dados não secretos usados para diagnóstico:

| Campo | Valor |
|---|---|
| Prefixo local da ordem | `5f945c50` |
| Estado persistido | `ACCEPTED` |
| Ativo local | `SPX/GOLD` |
| Direção | `CALL` |
| Stake | 100 minor units, USD |
| P&L realizado | ausente |
| Primeiro registro | 2026-09-15T18:51:12Z |
| Resultado mais recente de reconciliação | `CONFLICT` |
| Motivo | `IQOPTION_SYMBOL_MISMATCH` |

Houve várias tentativas anteriores `FAILED / IQOPTION_SYMBOL_MISMATCH`. A tentativa mais recente foi marcada `CONFLICT`, mas a linha da ordem permaneceu `ACCEPTED` e a reserva/exposição não ganhou um fluxo de resolução auditado.

## 4. Causas-raiz detalhadas

### RC-UI-01 — Dirty checking baseado no objeto errado

**Arquivo:** `apps/ui/app.py`, método `_refresh_projection()`.

O snapshot completo é usado como chave de mudança. Campos de apresentação temporal e logs tornam o objeto diferente mesmo quando nenhum componente visual importante mudou.

**Impacto:** o early return praticamente não funciona em sessão IQ ativa.

**Severidade:** crítica.

### RC-UI-02 — Restyling e repolish no hot path

**Arquivos:**

- `apps/ui/components/broker_card.py`;
- `apps/ui/components/iqoption_workspace.py`;
- `apps/ui/pages/overview_page.py`.

Vários setters aplicam novamente o mesmo texto, QSS e `polish/unpolish`. Em Qt, isso invalida estilo, geometria e paint, podendo gerar relayout em cascata.

**Impacto:** fila de mensagens do Windows fica sem tempo para processar input/paint; a janela recebe `Not Responding`.

**Severidade:** crítica.

### RC-UI-03 — Tabela de radar recalculada no caminho de mercado

**Arquivo:** `apps/ui/components/iqoption_asset_radar.py`.

Quando o ranking muda, o widget recria itens e executa `resizeRowsToContents()` seguido do recálculo da altura de todas as linhas. Como RSI/condição/ranking podem mudar com frequência, esse caminho é acionado repetidamente.

**Impacto:** custo proporcional a linhas × colunas em cada atualização, justamente no thread da UI.

**Severidade:** alta.

### RC-UI-04 — O snapshot transporta log completo, não delta

**Arquivos:**

- `apps/core/ui_service.py`;
- `packages/protocol/ui_messages.py`.

Cada projeção inclui até 160 registros operacionais. Mesmo com a aba Atividade invisível, esses registros são construídos, serializados, transferidos, desserializados e comparados.

**Impacto:** CPU extra no Core e na UI, pressão de alocação e invalidação permanente do snapshot.

**Severidade:** alta.

### RC-UI-05 — Chamada síncrona de autenticação ainda existe no startup

**Arquivos:** `apps/ui/app.py` e `apps/ui/controller.py`.

O polling posterior usa `cached_auth_status` em background, o que é correto. Porém a inicialização ainda chama `self._controller.auth_status()` sincronicamente. Se o Auth Agent atrasar, a janela pode nascer bloqueada.

**Impacto:** risco de travamento durante abertura, distinto da saturação contínua observada.

**Severidade:** alta.

### RC-IQ-01 — Comparação incompatível entre símbolo canônico e `active_id`

**Arquivos:**

- `apps/iqoption_worker/reconciliation.py`;
- `packages/brokers/iqoption/community_read_only.py`.

No caminho exato por broker order ID (`get_betinfo`), o payload pode trazer `active` como identificador numérico. O reconciliador faz:

```python
contract_symbol = str(contract.get("active", contract.get("symbol", "")))
if contract_symbol and contract_symbol != query.symbol:
    return INVALID_RESPONSE / IQOPTION_SYMBOL_MISMATCH
```

Já o fallback de histórico reconhece explicitamente que `active` pode ser numérico, compara contra `_active_id(symbol)` e normaliza para o símbolo canônico. Essa normalização não está aplicada ao caminho primário por ID.

**Impacto:** uma resposta correta para o broker order ID exato pode ser rejeitada como símbolo divergente.

**Severidade:** crítica.

### RC-IQ-02 — Conflito encerra o polling, mas não encerra a inconsistência de estado

**Arquivos:**

- `apps/core/reconciliation.py`;
- `packages/persistence/reader.py`;
- `apps/core/iqoption_auto_trader.py`;
- `packages/persistence/writer.py`.

`INVALID_RESPONSE` conclui a tentativa como `CONFLICT` e retorna `MANUAL_REVIEW_REQUIRED`, porém `_manual_review()` apenas bloqueia o Health Gate e emite evento; não persiste uma transição da ordem.

Ao mesmo tempo:

- `list_reconciliation_candidates()` exclui a ordem por causa do registro `CONFLICT`;
- `list_nonterminal_orders()` continua devolvendo a ordem `ACCEPTED`;
- `_has_nonterminal_iq_order()` continua bloqueando novas entradas.

**Impacto:** dead state lógico permanente.

**Severidade:** crítica.

### RC-IQ-03 — A máquina de estados não oferece caminho completo para esse incidente

`ALLOWED_TRANSITIONS` não permite `ACCEPTED -> MANUAL_REVIEW` nem `OPEN -> MANUAL_REVIEW`. Portanto, simplesmente “gravar MANUAL_REVIEW” exige uma decisão arquitetural explícita: transição intermediária para `SETTLEMENT_UNKNOWN/RECONCILING`, ou ampliação validada da máquina de estados.

**Impacto:** correções pontuais no reader apenas escondem o problema; não resolvem estado financeiro, exposição nem auditoria.

**Severidade:** crítica.

## 5. Por que desligar e ligar não pode liberar automaticamente

Permitir nova operação somente com o toggle seria perigoso. A ordem aceita pode ter produzido ganho ou perda real ainda não aplicada ao ledger. Ignorá-la poderia:

- duplicar exposição;
- calcular Martingale sobre resultado errado;
- liberar reserva indevidamente;
- ultrapassar Stop Loss ou limite de perdas;
- quebrar a trilha de auditoria.

Isso violaria `AG-INV-002`, `AG-INV-003`, `R-ORD-005`, `R-STATE-006` e `R-RISK-002`.

A UX desejada deve ser: desligar/ligar preserva a intenção, mas o sistema só volta a operar depois que a ordem for liquidada com evidência ou resolvida por revisão manual auditada. O toggle não deve ser usado como mecanismo de limpeza de estado financeiro.

## 6. Cobertura de testes e lacuna encontrada

Comando executado:

```text
python -m pytest \
  tests/unit/test_iqoption_martingale.py \
  tests/unit/test_iqoption_auto_trader.py \
  tests/unit/test_reconciliation_scheduler.py \
  tests/contract/test_iqoption_worker_contract.py \
  tests/integration/test_reconciliation_protocol.py \
  tests/unit/test_ui_overview_redesign.py \
  tests/unit/test_ui_terminal_regression.py \
  tests/contract/test_pyside6_headless.py -q
```

**Resultado:** `91 passed in 45.88s`.

Os testes passam porque validam que o conflito é excluído de `list_reconciliation_candidates()`. Eles não validam simultaneamente:

1. estado persistido após `INVALID_RESPONSE`;
2. presença da mesma ordem em `list_nonterminal_orders()`;
3. resultado de `_has_nonterminal_iq_order()`;
4. possibilidade de nova entrada depois de resolução auditada;
5. payload real com `active_id` numérico na rota `get_betinfo`;
6. responsividade da UI sob 1–2 eventos/s por vários minutos.

O aviso de `PermissionError` no cleanup temporário do pytest ocorreu depois do sucesso da suíte e não altera os 91 resultados, mas deve ser limpo no ambiente de CI para evitar ruído.

## 7. Plano de correção recomendado

### P0-A — Corrigir a recepção e normalização do resultado IQ Option

1. Criar uma função única de identidade de ativo no boundary do worker:
   - aceitar símbolo canônico textual;
   - aceitar `active_id` numérico;
   - resolver pelo catálogo da mesma geração de conexão;
   - rejeitar somente quando a identidade normalizada realmente divergir.
2. Aplicar a mesma normalização em:
   - eventos `option-closed`;
   - `get_betinfo`;
   - `get-options` por ID;
   - fallback por fingerprint.
3. Quando o broker order ID exato coincide, preservar essa evidência forte; não descartar a resposta apenas porque `active` veio numérico.
4. Persistir hash/proveniência da evidência normalizada, sem payload sensível bruto.

### P0-B — Corrigir a máquina de estados e o fluxo de revisão

1. Definir uma transição financeira explícita para resultado conflitante após aceite:
   - opção recomendada: `ACCEPTED/OPEN -> SETTLEMENT_UNKNOWN -> RECONCILING -> MANUAL_REVIEW`;
   - manter reserva ativa até resolução.
2. Fazer a conclusão `CONFLICT` e a transição da ordem ocorrerem na mesma transação.
3. Unificar a semântica das consultas:
   - “candidato a reconciliação automática”;
   - “exposição não terminal”;
   - “revisão manual pendente”.
4. Criar ação auditada de revisão manual que:
   - reconsulta fontes autoritativas;
   - exige evidência/justificativa;
   - aplica P&L e libera reserva exatamente uma vez;
   - só então permite novo sinal após rearme.
5. Nunca resolver `UNKNOWN/SETTLEMENT_UNKNOWN` apenas por tempo ou toggle.

### P0-C — Remover trabalho pesado do thread gráfico

1. Substituir igualdade do snapshot completo por revisões estáveis por fatia, por exemplo:
   - `global_revision`;
   - `broker_cards_revision`;
   - `iq_radar_revision`;
   - `orders_revision`;
   - `logs_after_sequence`.
2. Calcular idade de saldo localmente na UI a partir de `balance_observed_at_utc`; não mudar o snapshot inteiro apenas para atualizar um contador visual.
3. Enviar logs por delta/sequence cursor e atualizar o terminal somente quando a aba Atividade estiver visível.
4. Em cada widget, guardar a última assinatura semântica e não repetir:
   - `setText`;
   - `setStyleSheet`;
   - `setObjectName`;
   - `unpolish/polish`.
5. No radar:
   - atualizar apenas células alteradas;
   - usar altura fixa de linha;
   - remover `resizeRowsToContents()` do loop periódico;
   - limitar repaint a no máximo 4–5 Hz, independentemente da taxa de ticks.
6. Não atualizar workspaces escondidos antes da seleção da aba.
7. Mover também o primeiro `auth_status()` para o worker de polling; a UI nunca deve fazer `recv()` no thread Qt.

### P1 — Instrumentação obrigatória

Adicionar métricas locais e bounded:

- duração P50/P95/P99 de `_refresh_projection`;
- número de widgets/células alterados por ciclo;
- ciclos ignorados por dirty check;
- tamanho serializado da projeção;
- tempo de IPC da projeção e auth;
- atraso do Qt event loop (watchdog monotônico);
- motivo e idade de cada ordem não terminal.

Essas métricas devem aparecer no pacote de diagnóstico sanitizado, sem tokens, credenciais ou payload bruto.

## 8. Testes que devem ser adicionados antes do próximo build

### IQ Option

1. `get_betinfo` com ID exato e `active` numérico correspondente ao símbolo local deve liquidar.
2. `active_id` desconhecido ou pertencente a outro ativo deve entrar em revisão, sem inferir resultado.
3. Alias/canônico (`OTC`, Forex regular e ativos dinâmicos como `SPX/GOLD`) deve ser normalizado de forma determinística.
4. `INVALID_RESPONSE` deve persistir o estado de revisão na mesma transação da tentativa.
5. Ordem em revisão deve manter reserva e impedir nova entrada.
6. Após resolução auditada, a reserva deve ser aplicada/liberada uma única vez e um novo sinal deve poder operar após rearme.
7. Restart no meio da resolução deve ser idempotente.
8. Evento tardio válido deve resolver a ordem sem duplicar P&L.

### UI

1. Soak headless de 30 minutos com 2 snapshots/s e pelo menos 2 decisões IQ/s.
2. Assert de P95 do thread gráfico abaixo de 16 ms e nenhum ciclo acima de 100 ms em estado estável.
3. Snapshot alterado apenas por idade de saldo não pode reconstruir tabelas.
4. Log novo com aba Atividade oculta não pode repintar radar/cards.
5. Troca de abas durante fluxo de mercado deve responder em menos de 100 ms.
6. Auth Agent lento/indisponível no startup não pode bloquear o event loop.
7. Teste Windows de integração deve verificar `Responding=True` durante soak.

## 9. Critérios de aceite para a correção

O próximo release só deve ser aprovado quando todos os itens abaixo forem verdadeiros:

- nenhuma instância `TradingLab.exe` da UI fica `Responding=False` em soak de 30 minutos;
- CPU média da UI em repouso fica abaixo de 5% do total da máquina suportada;
- P95 de refresh gráfico abaixo de 16 ms; P99 abaixo de 50 ms;
- nenhuma tabela é reconstruída se sua assinatura semântica não mudou;
- resultado por broker ID exato aceita `active_id` numérico corretamente normalizado;
- nenhuma ordem fica simultaneamente excluída da reconciliação e marcada como `ACCEPTED/OPEN` sem fluxo de revisão;
- desligar/ligar não apaga exposição nem cria retry financeiro;
- resolução auditada permite retomar com sinal novo;
- testes de restart, duplicidade, evento tardio e conflito passam;
- `python -m pytest`, Ruff, format check, mypy e compileall passam;
- executável e processo filho exibem a mesma versão de release.

## 10. Risco residual e decisão de release

**Decisão recomendada:** não distribuir v1.9.17 como “travamento resolvido”.

O hotfix anterior melhora partes da UI e encerra o polling repetitivo da ordem em conflito, mas deixa dois defeitos críticos:

- saturação do thread gráfico ainda reproduzível no executável final;
- ordem em conflito permanece como exposição não terminal e bloqueia o AutoTrader para sempre.

Uma versão corretiva deve ser tratada como mudança de alto risco em estado de ordens e reconciliação, com atualização de contratos/documentação e testes de falha correspondentes.

## 11. Arquivos diretamente envolvidos

- `apps/ui/app.py`
- `apps/ui/controller.py`
- `apps/ui/components/broker_card.py`
- `apps/ui/components/iqoption_workspace.py`
- `apps/ui/components/iqoption_asset_radar.py`
- `apps/ui/pages/overview_page.py`
- `apps/core/ui_service.py`
- `apps/core/iqoption_auto_trader.py`
- `apps/core/reconciliation.py`
- `apps/iqoption_worker/reconciliation.py`
- `packages/brokers/iqoption/community_read_only.py`
- `packages/persistence/reader.py`
- `packages/persistence/writer.py`
- `packages/protocol/ui_messages.py`

## 12. Observação de versionamento

O arquivo auditado se chama v1.9.17, mas o título da janela, o `FileVersion` observado pelo Windows Error Reporting e o diretório temporário do payload continuam usando v1.9.11. Isso não causa diretamente o hang, porém dificulta identificar qual build realmente falhou. O pipeline deve aplicar versão única ao launcher, payload, metadados PE, título da janela e relatório de diagnóstico.
