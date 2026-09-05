# IQ Option — rejeições com recuperação por escopo (Causa 5)

Baseline: v1.9.11. Data: 2026-09-03. Validação exclusivamente local, com fakes.

## Diagnóstico e mudança

O antigo `_sticky_failure_reason` bloqueava todo o radar depois de uma rejeição.
O rearme apagava tanto a falha quanto os epochs consumidos. Isso misturava
indisponibilidade de um ativo, parâmetros incorretos e ambiguidade financeira.

O trader agora usa `IQFailurePolicy`. A política não executa ordens, não altera
stake e não libera reservas. As estratégias, o warmup, o payout gate, o SPRT,
a arbitragem e os bloqueios Real permanecem.

| Evidência / motivo | Escopo | Condição para prosseguir |
| --- | --- | --- |
| Rejeição confirmada: ativo suspenso/fechado | Ativo exato / BINARY_OPTION | Espera inicial 300 s, consulta read-only válida e novo sinal |
| Rejeição confirmada: prazo de compra expirado / indisponibilidade temporária explícita | Ativo exato / BINARY_OPTION | Espera inicial 30 s, consulta read-only válida e novo sinal |
| Rejeição confirmada: rate limit | Sessão IQ | Espera inicial 60 s, orçamento disponível, consulta válida e novo sinal |
| Stake abaixo do mínimo / parâmetros inválidos | Configuração do ativo | Alteração pertinente validada; novo payout e todos os gates |
| Motivo remoto desconhecido | Ativo exato | Revisão manual; não é presumido transitório |
| Falha de submissão sem comprovação de fase | Conta IQ | Revisão/reconciliação; não há liberação por tempo |
| UNKNOWN / RECONCILING / SETTLEMENT_UNKNOWN | Conta IQ | Evidência financeira persistida e gates de reconciliação existentes |
| HealthGate / risco / validação pré-admissão | Escopo da autoridade original | A autoridade precisa permitir; nenhum latch duplicado é criado |

Os motivos temporários são uma allowlist pequena, não uma interpretação livre de
qualquer mensagem remota. EURUSD e EURUSD-OTC continuam distintos. Não é criado
seletor de ativos novo e a configuração de risco não é ampliada automaticamente.

## Cadência e limites

- Backoff dobra após rejeições consecutivas no contexto, limitado a 1.800 s.
- A quinta rejeição consecutiva exige revisão manual naquele escopo; uma cotação
  read-only bem-sucedida não zera o contador de rejeições.
- Sucesso financeiro confirmado de uma **nova intenção**, após os gates, encerra
  a sequência de falhas do contexto. Não há reenvio da intenção rejeitada.
- Sondas são preguiçosas: só consultam payout/disponibilidade quando há novo sinal
  elegível. Não há polling independente nem compra usada como sonda de disponibilidade.
- Falha da consulta read-only reagenda a verificação (30–300 s) sem comprar.
- O orçamento de mensagens existente continua obrigatório.
- A política guarda no máximo 64 contextos. Saturação resulta em bloqueio de conta,
  não em expulsão silenciosa de uma proteção. O snapshot tem limite de 64 KiB.

## Persistência e crash

A migration **0009_iqoption_execution_state** é aditiva. Não altera migrations 0001–0008.
A tabela é escrita somente por `SingleDatabaseWriter`.

O snapshot contém epochs consumidos, política de falhas e correlação de uma
submissão pendente. É gravado antes de chamar o pipeline financeiro. Uma falha nessa
gravação impede a chamada. Intenção/reserva/outbox continuam na transação financeira
original, anterior a qualquer envio.

Na primeira utilização do runtime, um único carregamento restaura a projeção.
Se houve crash durante submit, a correlação é consultada nas ordens/outbox:

- rejeição persistida restaura a proteção correspondente;
- ordem não terminal permanece responsabilidade do recovery e HealthGate;
- ausência de intenção/ordem comprova que não houve admissão durável;
- o epoch continua consumido, inclusive no crash anterior ao envio.

Não há leitura nova de banco por vela para essa política. Os acessos preexistentes
do trader à exposição e à resposta persistida não foram refatorados nesta etapa.
Espera é monotônica durante a execução; o snapshot guarda tempo UTC e duração
restante para restart, sem reutilizar um timestamp monotônico de outro processo.

Rearmar não apaga falhas nem epochs. Um ARM explícito exige candle fechado depois
do armamento. Uma espera comprovadamente pré-admissão do monitor pode liberar o
epoch novo, mas preserva o epoch anterior já consumido.

## Certeza do envio

O worker distingue validação anterior ao transporte de erro depois da chamada buy.
Somente negativo explícito, sem ID de contrato contraditório, é rejeição confirmada.
Status ausente/inválido, confirmação sem ID, ID inválido e falha após possível envio
resultam em `TIMEOUT_AFTER_POSSIBLE_SEND` no contrato atual do worker, que o Core
projeta como UNKNOWN / outbox AMBIGUOUS / reserva ACTIVE.

O adapter comunitário também deixou de inventar rejeição só porque faltou ID.
Mensagem sem confirmação negativa suficiente permanece ambígua. Falha de parâmetros
comprovadamente anterior a `_request_message` carrega evidência NOT_SENT.
Não foi alterada a API financeira para incluir retry.

## Interface e operação

O radar apresenta: AGUARDANDO VERIFICAÇÃO, CORRIGIR PARÂMETROS ou REVISÃO MANUAL.
A condição/tooltip inclui motivo, ativo/conta, tempo restante e condição de liberação.
Eventos redigidos: `iqoption_execution_failure` e `iqoption_execution_recovered`.

Uma proteção manual não é removida por reiniciar, por aumentar Stop Loss ou por
trocar de estratégia. Não apague state.db nem edite o snapshot para destravar.
Motivos não reconhecidos precisam de diagnóstico e evidência de correção antes de
ampliar a allowlist. Não foi criado botão para ignorar uma proteção.

## Evidências executadas

- Regressão consolidada intermediária: **190 passed, 52,98 s**.
- Repetição final após todas as correções: **191 passed, 37,29 s**.
- Replay AUTO de 1.440 minutos, mantendo o operador armado:
  **24 rejeições injetadas, 48 aceitações fake, 1.368 ciclos sem submit,
  72 correlações distintas, zero duplicação**. Sinais são controlados pelo teste,
  não constituem medição de desempenho de estratégia ou da corretora.
- SQLite real temporário: UNKNOWN preserva uma intenção, uma ordem, outbox ambígua
  e reserva ativa após restart; Deriv não recebe o bloqueio IQ.
- Crash após rejeição e antes de salvar projeção: restaura motivo pela correlação.
- Upgrade 0008 → 0009 preserva checksums publicados.
- Qt offscreen: os três estados de recuperação são exibidos com motivo/escopo.
- Mypy: 304 arquivos sem erros. Ruff dos arquivos da etapa aprovado.
- compileall aprovado. Scanner `scripts/scrub_secrets.py --all`: nenhum segredo detectado.

Testes novos:

- `tests/unit/test_iqoption_failure_recovery.py`
- `tests/unit/test_iqoption_failure_ui.py`
- `tests/integration/test_iqoption_failure_persistence.py`
- `tests/replay/test_iqoption_failure_recovery_24h.py`

A regressão consolidada inclui também os testes existentes de trader, gates de
manifesto, lifecycle IQ, projeção de conexão, contrato worker, LiveMonitor, catálogo,
payout, SPRT, candidatos, adapter comunitário e persistência/dispatch.

## Limitações e pendências globais

`python -m pytest -q -x --tb=short` foi executado: **149 passed, 1 skipped, 1 failed**
em 93,44 s. Parou em `test_trading_lab_main_window_headless`: espera 5 abas, mas a
UI atual tem 6. Esse contrato e a estrutura das abas não foram alterados nesta etapa.
Não foi alegada suíte global verde.

Ruff global ainda aponta **24 diagnósticos fora dos arquivos da etapa**. Seis arquivos
preexistentes permanecem pendentes de formatação. Não foram aplicadas correções
cosméticas globais para esconder essas pendências.

Não houve login, cotação ou ordem externa, conta Real, alteração de perfil do operador,
build/EXE, commit ou push. O protocolo comunitário e as causas de rejeição externas
não foram homologados nesta execução. Não há garantia de risco zero.
