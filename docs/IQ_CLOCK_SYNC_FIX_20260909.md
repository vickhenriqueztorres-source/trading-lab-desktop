# IQ Option — correção da confirmação do relógio (2026-09-09)

## Evidência e escopo

A captura do operador mostra `MD_CLOCK_UNTRUSTED`, conta Practice conectada e saldo presente.
O código utilizava a duração completa de `_connect_websocket` como `round_trip_seconds`.
O contrato de domínio aceita RTT de até 1000 ms e desvio absoluto de até 2000 ms. Assim,
uma abertura lenta mantinha o relógio reprovado até o encerramento da sessão, sem medir a
latência atual. A captura não contém as medições para atribuir todo bloqueio externo a essa causa.

Também existiam fallback para horário local sem timestamp do broker e extrapolação ilimitada
de uma amostra antiga. Estes caminhos não comprovam sincronização e foram removidos.

## Implementação

- RTT medido por Ping/Pong WebSocket correlacionado, com a biblioteca já instalada. Não faz
  login adicional, consulta financeira ou nova dependência. Timeout de 2 s; resultado e falha
  têm cadência de 10 s por sessão. O limite de confiança permanece 1000 ms, sem ampliação.
- Timestamp da corretora mantém precisão Decimal em milissegundos. Extrapolação monotônica
  limitada a 30 s; ausência, desconexão, amostra velha e salto wall/monotonic >1 s falham fechado.
- Pong confirma transporte, não horário. Sempre se exige timestamp da corretora independente.
- Reconexão invalida amostras e RTT. Não existe substituição pelo relógio do computador.
- Core bloqueia apenas o escopo IQ enquanto a amostra estiver indisponível/não confiável.
  `IQOPTION_CLOCK_UNAVAILABLE` não dispara recuperação/login. A próxima amostra válida remove
  somente o bloqueio de relógio; não rearma bot nem remove outros bloqueios.
- Projeção não ressuscita o relógio antigo do lifecycle quando o trader invalida sua amostra.
- UI explica o bloqueio e exibe a latência disponível também enquanto aguarda sincronização.

## Verificação

`tests/unit/test_iqoption_clock_sync.py` cobre startup de 8 s com RTT de 125 ms, cache,
RTT >1 s, recuperação sem login, ausência de timestamp, salto positivo/negativo do relógio,
amostra velha, precisão, desvio >2 s, desconexão, Pong ausente e reconexão sem timestamp.
Teste Core usa submissão simulada para provar bloqueio/retomada sem afetar a Deriv.
Teste de UI confirma mensagem acionável. Nenhuma ordem externa foi enviada nestes testes.

Resultado final: 1372 passed, 4 skipped (435,76 s), Ruff/format (522 arquivos), mypy
(310 fontes), compileall e diff-check aprovados. Pytest retornou exit 0; a limpeza temporária
posterior em pytest-current gerou aviso WinError 5, sem falha de teste.
Build canônico passou scanner, integridade e health-check. Smoke headless do portátil com perfil
isolado encerrou com exit 0, sem processo residual, quick_check=ok e zero intenções/reservas/outbox/ordens.

Artefato: `dist/iq-clock-release-20260909/TradingLab-Desktop-v1.9.11-IQ-CLOCK-FIX.exe`.
SHA-256: `3C88177948EABC448E0B44AB491EF9121C2510DA7223962A325CA08C21531701`.

## Limites da entrega

Validação local não comprova aceitação/liquidação externa. O catálogo/payout continuam
independentes: ativo fechado, produto somente detecção, receita sem elegibilidade ou payout
abaixo do manifesto continuam impedindo entrada. Não houve alteração de receita, limiar,
permissão Real, Risk Ledger, banco do cliente, Supabase ou Strategy Lab.

Após fechar a versão anterior por `Cerrar Seguro`, usar o novo portátil. Confirmar horário
sincronizado e só então armar IQ Option manualmente em Practice. Se houver novo bloqueio,
registrar seu código e latência; não assumir que conexão ou saldo significam permissão financeira.
