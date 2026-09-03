# tl-primitives

Implementação de referência dos indicadores incrementais do Strategy Lab.

`VERSION` e `tests/parity/EXPECTED_SHA256` formam o contrato numérico público. Qualquer alteração
que mude o hash exige incremento de `VERSION`, novo manifesto e validação independente no bot.
O bot não importa este pacote: ele executa o mesmo vetor público com sua implementação local.

