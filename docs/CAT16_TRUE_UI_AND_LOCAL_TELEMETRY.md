# CAT-16 — UI verdadeira e telemetria local da execução

Data: 2026-09-06
Escopo: Desktop Bot / IQ Option (somente projeção local e telemetria opt-in)

## Objetivo

Eliminar estados visuais inventados no radar IQ Option e tornar observável, de forma local e
redigida, o caminho entre evidência de mercado, decisão, envio e aceite. Esta etapa não altera
matemática de estratégia, não cria autorização financeira e não altera os bloqueios de Real.

## Alterações

- O radar inicia vazio e só mostra valores depois de receber um snapshot do Core. Sem evidência,
  exibe `SEM EVIDÊNCIA DO CORE`; não há RSI, sinal ou status sintético.
- `SINAL OBSERVADO`, `order_submitted` e `order_accepted` são campos independentes. Uma ordem
  aceita não é apresentada como um novo sinal e uma rejeição não é mascarada como sucesso.
- A projeção IPC ganhou evidência de fonte, modo, revisão, readiness, séries, nós de indicadores,
  reuso de cache, latências, fila, espera e evidência estatística. O parser permanece compatível
  com snapshots antigos.
- O `SeriesHub` e o cache incremental alimentam as métricas sem leitura de banco no ciclo quente.
  A decisão registra latência e motivo de espera; a amostra de métricas é limitada e não contém
  credenciais, tokens, payloads de conta ou preços sensíveis.
- Outcomes v2 são enfileirados somente após settlement, com `event_id` determinístico, contexto
  mínimo (receita/manifesto/semântica/ativo/timeframe/produto/ambiente), fila limitada a 10.000
  itens e expiração de sete dias. A migration 0010 preserva os checksums históricos.
- O uploader v2 é desligado por padrão. Só inicia quando
  `DUALTRADE_OUTCOMES_OPT_IN=1` e o endpoint configurado é HTTPS. O token é obtido sob demanda e
  nunca é persistido; sem endpoint nenhum POST é feito.

## Garantias preservadas

- Deriv e IQ Option continuam com botões, workers e shutdown independentes.
- Nenhum caminho de reconexão, refresh de manifesto ou telemetria rearma trading.
- As guardas de ambiente Real, risco, lease/fencing e exatamente-uma-vez permanecem ativas.
- Nenhum login de corretora, conta financeira, ordem ou rede externa foi usado nesta validação.

## Verificação

- Testes CAT-16: 4 testes cobrindo separação sinal/execução, round-trip IPC, opt-in, contrato v2,
  idempotência e flush.
- Regressão focada UI/IPC/outcomes: 23 testes aprovados.
- Suíte completa: 1.291 aprovados e 4 ignorados. O teste histórico de migração foi atualizado
  para reconhecer a nova migration 0010 sem reescrever checksums; o cenário de supervisor que
  falhou sob a carga completa passou isoladamente e permanece coberto pela suíte de launcher.
- Ruff (arquivos alterados), mypy (306 arquivos), compileall e `git diff --check` executados.
  A execução de `ruff check .` global continua encontrando o arquivo legado `docs/##  Arquitetura.py`,
  que é Markdown salvo com extensão `.py`, além de avisos preexistentes em `scripts/scrub_secrets.py`;
  nenhum desses arquivos pertence à CAT-16.

## Fora do escopo

Não foi feita medição externa de corretora, upload para Supabase/R2, build do EXE ou envio de
ordem. A telemetria remota só poderá ser habilitada posteriormente por configuração explícita e
endpoint HTTPS aprovado.
