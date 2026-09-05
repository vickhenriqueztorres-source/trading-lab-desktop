# Contrato de warmup — Desktop v1.9.11 / manifesto v1.1

Data: 2026-09-03. Escopo: aquecimento, diagnóstico de avaliação, volume e
contrato assinado. Nenhuma ordem externa foi enviada. Não foi gerado EXE nesta etapa.

## Causa reproduzida

O histórico fixo de 20 velas era insuficiente para F1 (28) e F4 (39).
Mesmo com todas as 20 velas fechadas, essas famílias não podiam concluir o
aquecimento. Isso é diferente de concluir a análise e não encontrar consenso.

| Família (parâmetros default) | Regime | Trigger | Confirm | Warmup | Count solicitado |
| --- | ---: | ---: | ---: | ---: | ---: |
| F1 | 28 | 21 | 15 | 28 | 31 |
| F2 | 20 | 20 | 1 | 20 | 23 |
| F3 | 1 | 1 | 1 | 1 | 4 |
| F4 | 39 | 21 | 21 | 39 | 42 |
| F5 | 1 | 3 | 15 | 15 | 18 |

Os números não são constantes de execução: cada instância calcula o máximo dos
seus componentes. Alterar os parâmetros altera o contrato. Por exemplo, F5 com
RSI 7 usa warmup 8; RSI 14 usa 15. QuadrantMajority conta velas elegíveis
(minutos 2–4/7–9); o mínimo 3 pressupõe início em uma janela elegível. Sua regra
temporal e sua saída fora da janela não foram modificadas.

## Implementação

- `evaluate_detailed` devolve direção, estágio, contagens e outputs dos componentes.
  `evaluate` permanece compatível e devolve apenas a direção. Velas abertas não
  contam para aquecimento.
- A janela é `min(120, max(warmups ativos do símbolo/TF) + 3)`. A cache de
  dimensionamento é invalidada por manifesto/estratégia; o histórico é reutilizado
  dentro do intervalo monotônico do timeframe. Troca de worker descarta o histórico
  antigo. Não se busca histórico quando não há estratégia aplicável.
- A guarda de mensagens continua sendo adquirida uma vez por request, não por
  quantidade de velas. Não há bypass do orçamento. O piso legado de 15 velas no
  adaptador foi substituído por 1, permitindo o request de 4 velas da F3.
- Radar mostra `AQUECENDO have/need`, sem confundir aquecimento com neutralidade.
  A ausência de volume é `TICK_VOLUME_UNAVAILABLE`.
- `MarketCandle.tick_volume` é aditivo. O campo `volume` do broker é validado como
  inteiro não negativo. Ausência continua `None`; nenhum volume é inventado.
- Não houve mudança de fórmula, limiar, risco, consumo de epoch antes do dispatch
  ou retry financeiro. Avaliação incremental opcional não foi implementada.

## Manifesto e isolamento

`schema_version: 1` continua sendo o identificador principal. A revisão aditiva
é `schema_revision: "1.1"`, com `warmup_required` inteiro obrigatório em cada
entrada nova. Manifestos históricos sem revisão continuam válidos e seus bytes
assinados não são normalizados/adicionados durante a verificação.

O Lab instancia seus próprios primitivos; o Desktop instancia suas famílias
locais e confere o valor declarado. Divergência gera `WARMUP_MISMATCH` e exclui
somente a estratégia correspondente, preservando o restante do manifesto e o
ciclo de retirada das estratégias com ordem em voo. O evento tem buffer limitado
e encaminhamento ao event sink do Core quando disponível.

Não existem imports operacionais entre produtos. O JSON Schema público foi
regenerado. Atualizar os consumidores antes de publicar v1.1: consumidores antigos
com validação estrita de chaves podem rejeitar os novos campos.

## Replay de 24 horas

Fixture **sintética determinística**, 1.440 velas M1, volume conhecido, parâmetros
default, sem restrição horária adicional. Teste reproduzível:
`tests/replay/test_family_stage_distribution.py`.

| Família | WARMING_UP | REGIME | TRIGGER | CONFIRM | DISAGREE | NO_SIGNAL | OK |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| F1 | 27 | 1413 | 0 | 0 | 0 | 0 | 0 |
| F2 | 19 | 64 | 0 | 1357 | 0 | 0 | 0 |
| F3 | 0 | 0 | 0 | 0 | 0 | 1440 | 0 |
| F4 | 38 | 1270 | 0 | 0 | 0 | 132 | 0 |
| F5 | 14 | 0 | 464 | 139 | 591 | 107 | 125 |

Conclusão restrita à fixture: o aquecimento termina; a ausência posterior de
sinal tem causa explícita. F1/F4 recusam principalmente pelo regime nesta série.
Estes números não medem rentabilidade, frequência em mercado real nem justificam
afrouxar filtros. Não houve coleta externa; o teste do adaptador usa resposta
WebSocket simulada, não uma nova captura autenticada do broker.

## Estado de validação

- Regressão final Desktop: **86 passed**, incluindo manifesto assinado,
  fronteiras dos 14 primitivos, famílias, window sizing, orçamento, volume,
  hash público dos primitivos e quatro testes de isolamento do Strategy Lab.
- Replay de 24 horas: 1 passed; contagens acima reproduzidas.
- Strategy Lab: rodada final **312 passed, 3 skipped** (staging sem URL);
  **mypy --strict aprovado em 79 arquivos**, Ruff check aprovado. O hash público
  dos primitivos permanece inalterado em ambos os produtos.
- Suíte completa Desktop foi iniciada, acumulou falhas e deixou de avançar;
  a execução foi interrompida. O teste isolado
  `test_deriv_contract_01_to_03_handshake_and_public_capabilities` reproduziu
  `read-only worker did not connect before deadline`, antes da avaliação de
  qualquer família. Não houve aumento de timeout nem aprovação global.
- Verificações globais também localizaram problemas anteriores fora deste diff:
  formatação/lint em UI, launcher e testes; erros de tipo na UI de seleção.
  No Desktop: 28 diagnósticos Ruff, 8 arquivos a formatar e quatro erros mypy
  em `apps/ui/app.py` (atribuição/reuso de variáveis de configuração), arquivo
  não alterado nesta tarefa. No Lab: apenas formatação anterior em
  `tests/test_closing_checklist_lab.py` impede format-check global.
- Arquivos Python deste diff: lint e formatação verificados; subconjunto central
  Desktop com mypy aprovado em 34 arquivos. `compileall` dos dois produtos,
  scanner de segredos e `git diff --check` aprovados.
- Deno não estava disponível no PATH/caminhos locais examinados; teste aditivo
  do Hub foi escrito, **não executado**. Nenhum deploy remoto foi feito.

Veredito: correção coberta pela regressão local focada, **validação global e
release pendentes**. Não entregar este working tree como EXE homologado até
resolver/revalidar as falhas globais. Os testes de staging, uma captura externa
de volume e a repetição em segunda máquina também permanecem não executados.
