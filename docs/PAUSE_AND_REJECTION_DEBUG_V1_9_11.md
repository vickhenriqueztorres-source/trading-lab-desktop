# Debug de pausa, retomada e rejeições — v1.9.11

**Data:** 2026-08-28  
**Escopo financeiro:** Deriv Demo apenas; conta Real permaneceu sem submissão.

## Sintoma reproduzido

Depois de uma perda, o bot parecia entrar em pausa e não voltar. A atividade mostrava novas linhas
`REJECTED`, mas não registrava o motivo da corretora. O problema atingia mais de uma estratégia de
dígitos porque o cálculo de recuperação é compartilhado.

## Evidência observada

- uma perda de USD 1,00 iniciou a recuperação;
- a recuperação seguinte liquidou com +USD 0,97;
- restaram USD 0,03 na sequência;
- o cálculo antigo produziu USD 0,34 em `DIGITDIFF` e USD 0,04 em `DIGITODD`;
- as ordens foram rejeitadas antes de obter `broker_order_id`;
- cada reserva foi liberada exatamente uma vez e não houve exposição pendente;
- o auto trader continuou recebendo ticks e gerando sinais, portanto não era atraso da análise.

## Causas

1. A recuperação quote-aware não aplicava o piso da stake base. Um prejuízo residual pequeno podia
   gerar stake inferior ao mínimo já validado para o broker.
2. `HG_COOLDOWN_ACTIVE` era consultado pelo auto trader antes de atualizar sua expiração. Sem nova
   ordem/evento financeiro, o blocker podia permanecer ativo além do prazo.
3. O motivo seguro retornado pelo worker era descartado ao persistir o resultado de dispatch, o que
   deixava a UI e o diagnóstico apenas com `REJECTED`.

## Correções

- recuperação usa `max(stake_base, stake_necessária)` e continua sujeita ao teto de stake, Stop Loss,
  exposição e orçamento restante;
- o auto trader atualiza o cooldown antes da verificação global do Health Gate;
- a expiração é persistida uma única vez e a análise retoma no primeiro tick posterior;
- rejeição confirmada preserva `reason_code` no outbox e no journal;
- submissão rejeitada não consome tentativa do lote de performance;
- a descrição operacional diferencia cooldown de risco, cooldown de performance e rejeição.

## Regressões adicionadas

- residual `−100 + 97 = 3` calcula stake válida de 100 minor units, não 34;
- toda recuperação é maior ou igual à stake base;
- cooldown vencido é atualizado antes do gate e permite nova avaliação;
- rejeição síncrona limpa cache em voo, expõe o motivo e não informa sucesso;
- motivo de rejeição é persistido e a reserva é liberada.

## Validação local

- `pytest`: 840 passed, 4 skipped;
- Ruff check/format: aprovado;
- mypy: aprovado;
- compileall: aprovado;
- `git diff --check`: aprovado.

## Build e smoke do artefato

- pipeline canônico: `build_scripts/compile_trading_lab.py`;
- PyInstaller onedir/windowed: aprovado;
- scanner de segredo: aprovado;
- manifesto: 453 arquivos;
- health check do build: aprovado;
- smoke com profile temporário isolado: startup, worker, Safe Stop e shutdown aprovados;
- integridade do banco de smoke: `ok`;
- processos restantes após shutdown: zero.

Artefato portátil:

`dist_pause_fix/TradingLab-Desktop-v1.9.11-PAUSE-RECOVERY-FIX.exe`

SHA-256:

`4525C17A7A916B062D16B7A7AFF21173D4E85E986F82E9A122A2D32A0BD3B231`

## Validação externa Deriv Demo

**Status:** EXECUTED / PASS.

O artefato compilado foi conectado exclusivamente pelo transporte `live-demo`. Depois do ARM
explícito, foram observadas duas sequências naturais com perda de USD 1,00. Nas duas, o bot retomou
automaticamente, sem novas rejeições por stake abaixo do mínimo:

- primeira sequência: `-USD 1,00`, continuação válida com piso de USD 1,00 e recuperação liquidada
  com stake de USD 10,12;
- segunda sequência: `-USD 1,00`, continuação válida com piso de USD 1,00 e recuperação liquidada
  com stake de USD 10,12;
- 12 ordens Demo novas, todas liquidadas;
- zero ordens novas `REJECTED`;
- `pnl_application_count` máximo: 1;
- `release_count` máximo: 1;
- zero reservas ativas ao final;
- zero ordens Deriv não terminais ao final;
- `PRAGMA integrity_check`: `ok`;
- fechamento por Safe Stop: aprovado;
- processos restantes após fechamento: zero.

Nenhuma conta Real foi selecionada e nenhuma ordem Real foi enviada.
