# Relatório de teste — RSI IQ Option Practice

**Data:** 2026-08-31
**Estratégia:** `iqoption-rsi-demo` v1.0.0
**Ambiente financeiro externo:** não executado

## Configuração validada

- conta-alvo: Practice;
- ativo: `EURUSD-OTC`;
- timeframe: 60 segundos;
- RSI: Wilder 14;
- CALL: RSI < 30;
- PUT: RSI > 70;
- stake planejada: USD 1,00;
- posições simultâneas: 1;
- operações diárias: no máximo 10;
- inicialização: read-only e trading desligado.

## Resultado local

- cálculo e fronteiras RSI: PASS;
- somente candle fechado: PASS;
- warm-up de 15 candles: PASS;
- catálogo, runtime e entitlement: PASS;
- arbitragem e orçamento: PASS;
- persist-before-dispatch: PASS;
- intenção/reserva/outbox/ordem exactly-once no cenário: PASS;
- monitor bounded e redigido: PASS;
- testes focados: 10 passed;
- regressão geral: 910 passed, 4 skipped, 6 falhas ambientais;
- cinco testes de subprocesso repetidos isoladamente: PASS;
- scanner segmentado: 472 arquivos, 0 achados;
- Ruff focado: PASS;
- mypy focado: PASS.

## Validação externa IQ Option

**NOT EXECUTED.** A sessão externa atual é comunitária e read-only. Ela ainda não publica candles,
submissão financeira, eventos de contrato ou reconciliação. Nenhuma ordem foi enviada e nenhum
resultado financeiro, win rate ou P&L externo foi inventado.

## Veredito

`LOCAL_STRATEGY_FLOW_VALIDATED` — a estratégia está pronta para teste local controlado. Ainda não
está aprovada para execução financeira externa até a fronteira Practice do worker ser comprovada.
