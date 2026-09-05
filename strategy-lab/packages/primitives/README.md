# tl-primitives

Implementação de referência dos indicadores incrementais do Strategy Lab.

`VERSION` e `tests/parity/EXPECTED_SHA256` formam o contrato numérico público. Qualquer alteração
que mude o hash exige incremento de `VERSION`, novo manifesto e validação independente no bot.
O bot não importa este pacote: ele executa o mesmo vetor público com sua implementação local.

`warmup_required` documenta a primeira saída com dados válidos; em
`quadrant_majority` a contagem refere-se às velas elegíveis da janela temporal.
`tick_vol=None` representa volume não disponibilizado, sem substituição sintética.
`TickVolumeRatio` retorna `None` nesse caso. Fórmulas e o vetor público de volumes
inteiros permanecem inalterados; a versão numérica continua 1.0.0.
