# Strategy Lab — Worklog

Registro append-only das decisões e implementações do subprojeto.

## 2026-09-02 — Isolamento do produto e correção da ordem P09/P10

**Requisitos:** R-ISO-1..6, R-PRIM-6..7, R-MAN-2, R-PUB-2, R-BOT-1..4.

Os documentos passaram a definir o Strategy Lab como subprojeto autônomo, com processo, ambiente,
configuração, persistência, testes, agendamento e build próprios. O Strategy Lab não entra no EXE
principal e não compartilha imports, IPC, banco ou arquivos privados com o
`trading-lab-desktop`. A única integração operacional é o manifesto JSON assinado e versionado,
distribuído por HTTPS e mantido em cache validado pelo bot.

A compatibilidade numérica deixou de depender de import cruzado: lab e bot mantêm implementações
locais, executam o mesmo vetor público de 10.000 velas e comparam `primitives_version` e
`primitives_parity_sha256` presentes no manifesto.

O conflito de dependência foi eliminado trocando a ordem e o conteúdo de P09/P10. P09 agora cria o
`manifest_client` fail-closed e comprova os vetores públicos no bot. P10 implementa `publish` e seu
preflight isolado somente depois de P09, sem importar código do projeto principal.

**Fora desta mudança:** nenhum código de produção, banco, Supabase, conector, build ou dependência
foi criado. A alteração é exclusivamente documental.

## 2026-09-02 — P01: primitives incrementais e contrato de paridade

**Requisitos:** R-PRIM-1..7 e R-ISO-1..6.

Foi criado o ambiente Python 3.12 próprio do Strategy Lab, com `pyproject.toml`, lock reproduzível
e `.gitignore` isolados. O pacote `tl-primitives` v1.0.0 implementa 14 indicadores incrementais,
cada um tipado como Regime, Trigger ou Confirm, com parâmetros declarados e aritmética `Decimal`
sob precisão 28 e `ROUND_HALF_EVEN`.

O contrato público de conformidade usa uma série determinística de 10.000 velas, seed 20260902,
com volatilidade agrupada e valores decimais serializados como strings. Todos os outputs são
serializados em ordem canônica. O SHA-256 fixado é
`f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10`.

**Validação:** 33 testes aprovados. O hash foi recalculado repetidamente em processos separados sem
divergência. Cada indicador possui três cenários manuais, além de testes de reset/determinismo,
registro/categorias, validação estrita e AST sem `float`. Ruff, formatação e mypy strict foram
aprovados. A medição com `trace` da biblioteca padrão cobriu 602 de 649 statements do pacote,
equivalente a **92,76%**.

**Fora desta mudança:** não houve integração com o EXE principal, rede, IQ Option, Supabase,
persistência, estratégia executável ou envio de ordem. A confirmação em uma segunda máquina física
fica para o job de CI multiplataforma; nesta máquina foram comprovadas execuções independentes.

## 2026-09-02 — P01: fechamento do empacotamento isolado

**Requisitos:** R-PRIM-1..7 e R-ISO-1..6.

O pacote `tl-primitives` passou a declarar o arquivo público `primitives/VERSION` como dado de
pacote e o lock passou a fixar também o backend de build `setuptools==80.9.0`. A instalação
editável, sem dependências adicionais, foi validada no ambiente Python 3.12 próprio do Lab; um
processo sem `pythonpath` de testes importou `primitives` e confirmou a versão `1.0.0`.

**Validação final:** 33 testes aprovados; Ruff e formatação aprovados; mypy strict aprovou os 22
arquivos-fonte; `compileall` aprovado; scanner local examinou 35 arquivos e encontrou zero
segredos; scanner de isolamento não encontrou import, banco ou arquivo privado compartilhado.

**Fora desta mudança:** permanecem fora rede, Supabase, conector IQ Option, execução financeira,
manifesto operacional e qualquer modificação no EXE principal. A execução em segunda máquina
física continua reservada ao CI, sem declaração antecipada de aprovação.

## 2026-09-02 — P02: contrato de manifesto, assinatura e vetores públicos

**Requisitos:** R-MAN-1..7; isolamento R-ISO-1..6 preservado. Após parada por divergências,
o operador autorizou formalizar o contrato antes de implementar. Criado tl-manifest-schema 1.0.0
no ambiente próprio do Lab, com modelos strict, faixas/grades, Ed25519 canônica, ingestão limitada,
export, schema público, fixture assinada, 60 vetores públicos e testes independentes jsonschema.

**Decisões de contrato:** F1–F5 compõem uma primitiva por categoria; parâmetros de construtores
derivam do registry P01; adx_max e width_ratio_max são gates de composição com faixas próprias
documentadas, sem alterar P01. Strings de params são obrigatórias; epochs, contagens e versões
estruturais permanecem inteiros. Omissão e null não são intercambiáveis na assinatura. A chave
pública de teste é recusada por padrão, inclusive se rotulada A ou B. Supabase não é necessário
nesta fase local e nenhuma credencial foi utilizada, registrada ou testada.

| Antes | Depois | Efeito numérico |
|---|---|---|
| Família mencionada sem composição | Bindings F1–F5 e gates explícitos | Não muda cálculos P01 |
| params misturavam inteiros e strings | Todos os valores de params são strings decimais | Muda representação, não valor |
| Exemplo com versão/hash/assinatura placeholders | Fixture 1.0.0 com hash P01 real e assinatura de teste | Sem evidência financeira nova |
| payout_min sem resolução de publicação definida | Menor valor seguro na grade 0.01, para cima | Wilson 0.561 → mínimo publicado 0.84 |
| JSON Schema genérico presumido suficiente | Schema + perfil semântico v1 obrigatório + Ed25519 | Expiração, ranges e grades realmente testados |

**Validação:** 150 testes aprovados (33 P01 + 117 P02); 9 vetores aceitos e 51 hostis recusados
com reason code exato; cada byte da assinatura adulterado isoladamente foi recusado. Ruff,
format, mypy strict (30 fontes), compileall e pip check aprovados. Cobertura P02 pelo trace da
biblioteca padrão: 457/470 = 97,23%. Schema sincronizado com export e fixture idêntica à
Arquitetura §6. Wheel isolado gerado e inspecionado, sem testes/fixtures/chaves privadas.
Resultados, comandos e hashes em docs/P02_VALIDATION.md.

**Limitações e fora do escopo:** JSON Schema padrão não compara epochs ou números guardados
como strings; o perfil obrigatório foi documentado e testado em adaptador jsonschema independente.
Deno/hub e bot devem portar o perfil e executar os vetores em suas fases; não foram implantados.
Não houve migração, Supabase remoto, IQ Option, ordem, EXE novo, commit ou push.

**Encontrado, não corrigido (P01):** alguns construtores não validam todo o máximo declarado no
param_spec (por exemplo, BBCloseOutside.length e RSIExtreme.period). P02 aplica integralmente as
faixas antes de aceitar params; não alterei os construtores nem o contrato de paridade do P01.

## 2026-09-02 — P03: vendor e adaptador de coleta isolado

**Requisitos:** R-VEND-1..3; R-ISO-1..6 preservados. O operador autorizou patches
mínimos de segurança, lock fora do snapshot e coleta real manual posterior.

O candidato comunitário original não possui licença explícita e não foi adotado;
a cópia de análise permanece em state/rejected-upstream-8a903cc (ignorada).
Escolhido victalejo/iqoptionapi@acac6e08333466ae188c7dfa7fd2a03174e34ca2,
com licença MIT declarada: 86 arquivos upstream, 3 modificados (TLS, logging,
relógio) e 83 byte-idênticos. LICENSE preservado, UPSTREAM_COMMIT, PATCHES.md,
diff de segurança e hashes antes/depois adicionados. Dependências sync pinadas
em vendor/REQUIREMENTS.txt e no lock exclusivo do Lab.

Implementados IQClientProtocol, IQClient, FakeIQClient e CLI record-fixture.
O adaptador reutiliza recursos/channels do vendor com fronteiras somente leitura;
não usa connect/retry/handlers financeiros legados. TLS obrigatório, JSON Decimal,
deadline monotônico, pacing 0,5–2s, ID via catálogo e -OTC preservado.
Lote inválido não retorna subconjunto válido; payload do erro só tem preços seguros.
Recorder exige cobertura exata de até 1.000 M1 fechadas, não sobrescreve e gera hash.
Credenciais ficam no destino próprio StrategyLab/IQOption/collection no Windows
ou env namespaced na VPS. Nenhuma credencial do operador foi utilizada.

**Validação final:** 240 testes aprovados (150 anteriores + 90 P03); Ruff, format
(69 arquivos), mypy strict (37 fontes), compileall, pip check, CLI --help e
git diff --check aprovados. Scan heurístico de segredos não encontrou matches.
Testes incluem os componentes reais do vendor com I/O fake, falhas de envio,
timeout/close, mensagens financeiras negadas, JSON hostil e gravação de 1.000
linhas sintéticas apenas em diretório temporário. Relatório: docs/P03_VALIDATION.md.
Setuptools 80.9.0, já previsto no lock, foi reinstalado com caminho estendido
Windows após WinError 206; não houve mudança arbitrária de dependência.

**Aceite pendente / fora do escopo:** não houve conexão externa IQ, coleta real,
fixture real commitada, conta Real, ordem, Supabase, build do EXE, commit ou push.
A fixture de três velas commitável está claramente marcada synthetic; NÃO comprova
a coleta real de 1.000 velas. NTP/canário/cota diária/backfill pertencem ao P04.
O CLI funciona no checkout editável, não como wheel standalone distribuível.
Nenhuma mudança nos cálculos/paridade P01 ou no contrato de manifesto P02.

**Ocorrência de conferência:** uma chamada estática final foi executada por engano
no diretório pai, somente leitura; mypy passou e Ruff encontrou erros em conteúdo
preexistente, inclusive Markdown salvo como docs/## Arquitetura.py. Não foi
modificado. As verificações foram repetidas no ambiente/diretório correto do Lab
e passaram. Esses resultados do pai não integram o aceite P03.

## 2026-09-02 — P04: collect diario idempotente com canario

**Requisitos:** R-COL-1..13; isolamento R-ISO-1..6 preservado. Implementado o
job `strategy-lab collect` com Clock injetavel, preflight NTP, credenciais via
keyring do SO e fallback de ambiente apenas em `STRATEGY_LAB_ENV=vps` usando
`IQ_EMAIL`/`IQ_PASSWORD`. Nenhuma credencial do bot principal e nenhum arquivo
privado compartilhado foram lidos.

**Implementacao:** adicionados canario de cinco velas, Repository Protocol,
FakeRepository, backfill M1 por watermark em lotes ate 1000, corte de vela
fechada, gaps contra grade de 60 s e calendario Forex/OTC, sampler de payout
por hora, invariantes de serie (monotonicidade, duplicata e salto > 8*ATR(14)),
runner orquestrado e comandos `collect --dry-run` e `status --dry-run`. O runner
aborta canario antes de qualquer escrita no repositorio. O repositorio Supabase
real continua fora desta fase e pertence ao P05.

**Decisoes:** como P05 ainda nao existe, execucao persistente real fica recusada
na CLI; `--dry-run` usa FakeIQClient + FakeRepository para validar o fluxo sem
gravar Supabase. `record_run` nao e persistido em abort do canario para cumprir
R-COL-2/I-7. Duracao de CLI usa monotonic; tempo de dados permanece epoch UTC.

**Validacao:** 254 testes aprovados; Ruff check, Ruff format --check, mypy
strict (46 fontes), compileall, pip check, `collect --dry-run`, `status --dry-run`
e `git diff --check -- strategy-lab` aprovados. Testes P04 cobrem canario,
idempotencia, DST/vela corrente, vela invalida, gaps, payout sem amostra,
invariante de salto, segredo em output e AST contra `time.time()`/`datetime.now()`
ingenuo em `collect/`. Relatorio: docs/P04_VALIDATION.md.

**Limitacoes / fora do escopo:** nao houve conexao externa IQ Option, coleta real,
ordem, Supabase, migration P05, build do EXE, commit ou push. Cobertura percentual
nao foi medida porque `pytest-cov`/`coverage` nao estavam autorizados nas
dependencias do prompt; a suite comportamental ficou verde. O diretorio
`strategy-lab/` segue inteiro como untracked no repo pai, entao o diff textual do
Git so ficara disponivel apos adicionar o subprojeto ao controle de versao.

## 2026-09-02 — P05: Supabase schema, RLS, PostgresRepository e backup

**Requisitos:** R-HUB-1, R-HUB-2, R-HUB-7, R-HUB-8 e R-OPS-1; isolamento
R-ISO-1..6 preservado. Foram criadas migrations Supabase para candles, payouts,
market_sessions, gaps, research_runs, manifests, live_outcomes, collect_runs e
archive_jobs, com checks e indices exigidos. RLS foi habilitado em todas as
tabelas; anon nao tem leitura e so pode inserir `live_outcomes` quando
`client_id` bate com o JWT. Service role permanece no bypass padrao do Supabase.

**Implementacao:** adicionado seed de sessoes Forex/OTC, script
`scripts/supabase_staging.sh`, stub da Edge Function `archive`, funcao
`archive_old_candles()` com `pg_cron`/`pg_net` e etapa separada
`complete_archive_job()` que apaga candles somente quando a contagem arquivada
confere. Adicionado `PostgresRepository` com `psycopg` v3, UPSERT idempotente
com protecao de `source` e opcao `--force-source`. A CLI `collect` agora usa
PostgresRepository quando `--dry-run` esta desligado. Adicionado
`strategy-lab backup` com `pg_dump` + `age` via variaveis de ambiente, sem DB URL
ou senha em argumentos de linha de comando.

**Validacao local:** 257 testes aprovados e 3 testes staging pulados por falta de
`SUPABASE_STAGING_DB_URL`; Ruff check, Ruff format --check, mypy strict
(48 fontes), compileall, pip check, `collect --dry-run`, `status --dry-run`,
falha controlada de `backup` sem segredo e `git diff --check -- strategy-lab`
aprovados. Relatorio: docs/P05_VALIDATION.md.

**Atualizacao CLI:** instalado Supabase CLI oficial v2.116.0 em
`state/tools/supabase-cli-v2.116.0/`, fora do Git, com SHA-256 conferido contra
`checksums.txt` da release. Adicionado `scripts/supabase_staging.ps1` para Windows
e atualizado `scripts/supabase_staging.sh` para usar `supabase db query` e
`supabase db push --db-url`, sem depender de `psql`.

**Bloqueios externos:** nao foi possivel aplicar remotamente nesta maquina:
`SUPABASE_STAGING_DB_URL` nao esta configurado, `SUPABASE_ACCESS_TOKEN` nao esta
configurado, `psql` nao existe no PATH e as chaves informadas nao provam um banco
staging. A disponibilidade real de `pg_net` tambem nao foi comprovada; o script
faz precheck e para se ela nao existir, conforme pedido. Nao houve conexao remota,
ordem, coleta externa, migration aplicada em producao, commit ou push.

## 2026-09-02 — P06: Hub Edge Functions publish, outcomes, mirror e client_token

**Requisitos:** R-HUB-3..6; isolamento R-ISO-1..6 preservado. Implementadas as
Edge Functions Deno/TypeScript estritas em `apps/hub/supabase/functions/`:
`publish`, `outcomes`, `mirror` e `client_token`, com modulos compartilhados para
canonicalizacao, Ed25519, encoding, JWT anonimo, acesso Supabase REST/Storage,
validacao de manifesto e mirror R2/S3-compatible.

**Implementacao:** `publish` valida JSON sem chave duplicada, aplica perfil do
schema de manifesto, verifica assinatura Ed25519 por chave A/B, bloqueia trust
root de teste fora de staging, impede versao regressiva com 409, grava
`v{manifest_version}.json` e `current.json` no bucket `manifests`, insere a linha
em `manifests` e invoca `mirror` sem bloquear a resposta. `outcomes` exige JWT
anonimo com `client_id`, lote <= 500, janela de 7 dias, rejeicao de futuro,
rate-limit 60/h via `consume_rate_limit()` e `ON CONFLICT DO NOTHING`.
`client_token` emite token anonimo de 1 ano para UUID gerado pelo bot. `mirror`
copia os dois objetos do Supabase Storage para R2 com assinatura AWS SigV4.

**Banco/Storage:** adicionada migration `0005_rate_limits.sql` com tabela
`rate_limits` protegida por RLS e funcao security-definer `consume_rate_limit`.
Criado `apps/hub/README.md` documentando buckets `manifests` publico e `parquet`
privado, secrets das functions, comandos Supabase CLI e smoke esperado com ETag
e Cache-Control.

**Validacao local:** instalado Deno oficial v2.9.6 em `state/tools/deno-v2.9.6/`,
fora do Git, com checksum oficial conferido. `deno fmt --check`, `deno check`,
`deno lint` e `deno test` aprovados; 12 testes Deno passaram cobrindo assinatura
A/B, assinatura invalida, chave de teste bloqueada em prod, versao regressiva,
schema invalido, canonicalizacao Python->Deno, outcomes futuro, rate limit,
injecao de `client_id` pelo JWT, emissao de client token e mirror fake.

**Regressao Python:** 257 testes aprovados e 3 staging pulados por falta de
`SUPABASE_STAGING_DB_URL`; Ruff check, Ruff format --check, mypy strict,
compileall e `git diff --check` aprovados. Relatorio:
`docs/P06_VALIDATION.md`.

**Bloqueios externos:** `supabase functions serve` + `curl` nao foi executado
porque Docker/Podman nao existe no PATH. Deploy remoto, criacao real de buckets
e teste de GET publico nao foram executados porque `SUPABASE_ACCESS_TOKEN` e
`SUPABASE_STAGING_DB_URL` nao estao configurados fora do repositorio. Nenhuma
credencial real foi persistida, nenhuma conexao de broker foi feita e nenhuma
ordem foi enviada.

## 2026-09-02 — P07: nucleo research, dataset, replay e cobertura

**Requisitos:** R-RES-1, R-RES-4, R-RES-5, R-RES-6 e R-RES-10 parcial; isolamento
R-ISO-1..6 preservado. Criado `tools/strategy_lab/research/` com dataset,
payout lookup, simulacao fim-de-vela, replay incremental, triagem Polars,
penalidade deterministica de atraso, identidade de candidato e geradores
sinteticos.

**Implementacao:** `ResearchDataset` carrega candles/payouts/gaps de Supabase
via `psycopg` ou Parquet local via DuckDB, calcula cobertura por grade de 60 s
e recusa pesquisa com cobertura < 95% ou gap `in_session` nao resolvido.
`PayoutLookup` usa `hour_ts = ts - ts % 3600` e retorna `None` quando
`samples == 0`, excluindo a operacao. `settle()` liquida no fechamento seguinte
com empate como perda. `replay_candidate()` instancia os tres primitivos do
candidato, alimenta vela a vela e so usa `t+1` depois da decisao em `t` para
resultado. `apply_delay_penalty()` usa subtracao direta de p_hat, deterministica,
em vez de reclassificacao aleatoria.

**Triagem:** `vector_scan_candidate()` retorna DataFrame Polars. O caminho
vetorizado real cobre `session_window + range_break + candle_rejection`; outras
combinacoes retornam timestamps equivalentes ao replay como fallback conservador
para impedir aprovacao por uma segunda matematica ainda incompleta. A aprovacao
continua sendo somente por replay incremental.

**CLI:** adicionado `strategy-lab research --coverage-report --assets --from --to`
com leitura de Parquet local ou Supabase via `SUPABASE_DB_URL`. O comando imprime
cobertura por asset e retorna erro quando algum asset fica abaixo de 95% ou possui
gap in-session nao resolvido.

**Dependencias:** adicionadas dependencias autorizadas P07 ao `pyproject.toml` e
`requirements.lock`: Polars, DuckDB, NumPy e PyArrow. Instaladas somente no venv
proprio do Strategy Lab.

**Identidade e hash:** `Candidate` implementa `hash()` e `stable_hash()` produzindo o mesmo SHA-256 canônico independente da ordem dos parâmetros, além de `__hash__()` inteiro para compatibilidade com coleções Python contendo dicionários de parâmetros.

**Validacao:** 13 testes P07 aprovados (incluindo teste explícito de recusa com código de saída 1 na CLI quando a cobertura fica abaixo de 95%, e testes estáveis de hash e penalidade). A suite completa ficou com 270 testes aprovados e 3 staging pulados por falta de `SUPABASE_STAGING_DB_URL`. Ruff check, Ruff format --check, mypy strict (57 fontes), compileall e testes aprovados. Teste `test_replay_never_sees_future` cobre todos os 14 primitivos reais do `REGISTRY`. Relatorio: `docs/P07_VALIDATION.md`.

**Fora do escopo:** nao houve coleta externa, pesquisa real contra Supabase
staging, ordem, broker, alteracao no EXE principal, commit ou push. R-RES-2,
R-RES-3, R-RES-7, R-RES-8, R-RES-9, R-RES-11 e R-RES-12 permanecem para prompts
posteriores.

## 2026-09-02 — P08: portões estatísticos e teste da moeda

**Requisitos:** R-RES-7, R-RES-8 e R-RES-10; isolamento R-ISO-1..6 preservado. Criado o pacote `tools/strategy_lab/research/gates/` com os cinco portões estatísticos sequenciais, orquestrador de pipeline fail-closed com curto-circuito e critério formal de aprovação.

**Implementação:**
- `wilson.py`: limite inferior 95% do Wilson Score Interval calculado em aritmética `Decimal` exata (precisão 28, $z = 1,959964$).
- `walk_forward.py`: gerador de janelas ancoradas (treino 6 meses / teste 2 meses rolando) e avaliação de estabilidade intertemporal (nenhuma janela com $\hat{p} < p_{min}$ e desvio-padrão entre janelas $\sigma < 3\text{ pp}$).
- `multiple_testing.py`: controle de FDR Benjamini-Hochberg a 5% sobre $p$-valores binomiais sob hipótese nula $H_0: p \le p_{min}$ utilizando o total $N$ de candidatos avaliados na rodada; teste de permutação Monte Carlo 1.000× com seed exigindo $\hat{p} > P_{99}$.
- `neighborhood.py`: perturbação de hiperparâmetros em $\pm 15\%$, ajustada à grade de `param_spec` de cada indicador, com re-simulação via `replay_candidate` e exigência de $\text{mediana}(\hat{p}_{vizinhos}) \ge p_{min} + 1,5\text{ pp}$.
- `pbo.py`: Combinatorially Symmetric Cross-Validation (CSCV) com 16 blocos temporais contíguos e $\binom{16}{8} = 12.870$ partições treino/teste, vetorizado em NumPy; exigência de $\text{PBO} < 20\%$.
- `pipeline.py`: execução dos portões na ordem fixa Walk-Forward $\to$ Estabilidade $\to$ FDR + Permutação $\to$ Vizinhança $\to$ PBO, com curto-circuito imediato na primeira falha.
- `approve.py`: critério cumulativo de aprovação (R-RES-8) exigindo todos os portões aprovados, $n_{oos} \ge 500$ e Wilson inferior com penalidade pessimista ($-1,0\text{ pp}$) $\ge p_{min} + 1,5\text{ pp}$.
- `README.md`: documentação teórica e matemática completa do pipeline em `tools/strategy_lab/research/README.md` e `research/README.md`.

**Validação:** 6 novos testes em `tests/test_gates_p08.py`. Suíte completa atingiu 276 testes aprovados e 3 staging pulados por falta de `SUPABASE_STAGING_DB_URL`. O teste intocável de CI `test_coin_flip_approves_zero` submeteu 2.000 candidatos a passeio aleatório em 3 seeds independentes (6.000 avaliações) e aprovou exatamente 0 candidatos. Ruff check, Ruff format --check, mypy strict (65 fontes) e compileall 100% aprovados. Relatório: `docs/P08_VALIDATION.md`.

**Fora do escopo:** Holdout selado (R-RES-2), gramática formal (R-RES-3), ranking/relatórios finais (R-RES-9, R-RES-11), publish e revalidação ao vivo permanecem para os prompts subsequentes.

## 2026-09-02 — P10: `strategy-lab publish`, preflight hermético, diff, assinatura e upload

**Requisitos:** R-PUB-1..5, R-RES-9, R-RES-11; isolamento hermético R-ISO-2..3 estritamente preservado.

**Implementação:**
- `research/scorer.py`: módulo financeiro implementando `margin = wilson_lower - p_min`, `score = margin * sqrt(ops_per_day)`, pior sequência contínua de perdas (`worst_streak`), projeção de resultado para 1.000 operações a stake 10 (`result_1000_ops_stake10`) e `payout_min` como o menor payout na grade 0,70..0,95 (passo 0,01) onde `wilson_lower >= 1 / (1 + payout) + 0.015`.
- `research/report.py`: gerador do relatório de ranking em Markdown (`ranking.md`) ordenado por score decrescente com os 5 números fundamentais e veredito detalhado por portão estatístico; serializador do arquivo canônico `candidates.json`; e função `run_synthetic_research` para testes controlados.
- `packages/sprt`: pacote autônomo com implementação de referência do Teste de Razão de Verossimilhança Sequencial de Wald (`WaldSprt`), cálculo dos limiares de absorção $A$ e $B$, decisões explícitas (`CONTINUE`, `ACCEPT_H0`, `REJECT_H0`), memória de rejeição e método `is_eligible_for_promotion()` (exigindo $\ge 200$ operações ou $\ge 30$ dias sem qualquer rejeição pelo teste).
- `publish/builder.py`: montagem do manifesto canônico a partir de `candidates.json`, respeitando filtros `--include` e `--exclude`. Toda nova estratégia nasce com `status="observation"` (invariante R-PUB-5). Estratégias já em `approved` no manifesto vigente continuam `approved` apenas se aprovadas na rodada. Flag `--promote` verifica elegibilidade via SPRT contra `live_outcomes`.
- `publish/preflight.py`: auditoria completa do manifesto assinado sem importar nenhuma linha de código de `apps/core/manifest_client.py` ou do projeto principal (R-ISO-2..3). Executa todos os 60 vetores públicos de conformidade de `contracts/manifest_acceptance_vectors.json` comprovando 100% de paridade com o bot.
- `publish/differ.py`: cálculo de diferenças detalhadas (adições, remoções, alterações de parâmetros e inalteradas) e confirmação manual obrigatória no terminal exigindo a digitação do número exato de estratégias a publicar. A flag `--yes` é expressamente proibida (R-PUB-3).
- `publish/signer.py`: carregamento de chave privada Ed25519 em `~/.strategy-lab/keys/{A,B}.pem`, validação estrita de modo `0600` em ambientes POSIX (recusando qualquer permissão insegura como `0644`) e assinatura detached de bytes canônicos RFC 8785.
- `publish/uploader.py`: cliente HTTPS para envio do payload assinado para a Edge Function `publish` do Supabase, com tratamento semântico e claro dos códigos `201 Created`, `401 Unauthorized`, `409 Conflict` e `422 Unprocessable Entity`.
- `tools/strategy_lab/cli.py`: integração dos comandos `publish` (com `--run-id`, `--key-id`, `--include`, `--exclude`, `--promote`, `--dry-run`, `--allow-test-keys`, recusa da flag `--yes`) e `research --synthetic`.

**Validação:** 13 testes em `tests/test_publish_p10.py` aprovados, cobrindo scorer, relatórios, Wald SPRT, builder com observation/promotion, execução local dos 60 vetores contratuais, scanner AST provando ausência de imports do bot no lab, diff, verificação de modo 0600 vs 0644, simulação de status codes do uploader e CLI dry-run. A suíte completa atingiu 289 testes aprovados (3 pulados por dependência de staging). Ruff check, Ruff format, mypy strict e compileall 100% aprovados. Relatório: `docs/P10_VALIDATION.md`.

## 2026-09-03 — P14: gramática de candidatos, holdout selado e pipeline de pesquisa

**Requisitos:** R-RES-2, R-RES-3, R-RES-12, R-ISO-2..3.

**Implementação:**
- `apps/hub/supabase/migrations/0006_holdout_burned.sql`: migration aditiva criando a tabela `public.holdout_burned` com `range_id`, `from_ts`, `to_ts`, `burned_at`, `run_id`, restrições de integridade temporal e RLS estrito.
- `tools/strategy_lab/research/grammar.py` (R-RES-3): gerador combinatório de candidatos a partir de 1 Regime × 1 Trigger × 1 Confirm × grade de hiperparâmetros (`param_spec` com validação interna das restrições de domínio dos indicadores) × Timeframes {M1, M5, M15} × faixas horárias UTC {00-06, 06-10, 10-13, 13-16, 16-21, 21-24} × ativos. Exclusão estrita de pares incompatíveis (`INCOMPATIBLE`, e.g. `rsi_extreme` com `quadrant_majority`, `bb_close_outside` com `bb_width_ratio`). Limitação a $\le 5.000$ candidatos por amostragem determinística pseudo-aleatória ancorada em `--seed`. Rastreamento de `total_candidates` preservado para controle de FDR Benjamini-Hochberg.
- `tools/strategy_lab/research/holdout.py` (R-RES-2): isolamento dos últimos 3 meses (90 dias) de candles para teste cego fora da amostra; cálculo de hash SHA-256 estável da partição; `HoldoutManager` com verificação `open_once(run_id)` (lança `RuntimeError` em caso de tentativa de dupla abertura fail-closed); e mecanismo de registro de faixas queimadas `burn(range)` e `refuse_if_burned(range)` impedindo a reutilização de dados de holdout na rodada seguinte.
- `tools/strategy_lab/research/live_merge.py` (R-RES-12): agregação anônima de `live_outcomes` por `strategy_key`; avaliação como janela extra out-of-sample no walk-forward; e combinação ponderada demonstrando que a degradação na performance ao vivo reduz monotonicamente $\hat{p}$ e o limite inferior de Wilson.
- `tools/strategy_lab/research/report.py`: atualização de `generate_ranking_markdown` com suporte a `active_manifest_keys` e geração automática da seção `## Novas oportunidades` no relatório Markdown `ranking.md`, contendo a ficha completa em português com os 5 números fundamentais para estratégias aprovadas ausentes do manifesto vigente.
- `tools/strategy_lab/research/synthetic.py`: adição dos primitivos sintéticos de referência (`AlwaysRegime`, `BodyTrigger`, `BodyConfirm`) e função geradora de candidato com edge injetado (`make_injected_edge_candidate`).
- `tools/strategy_lab/research/runner.py`: orquestrador do fluxo completo de 10 passos da Arquitetura §5 (0 a 9):
  0. Cobertura: recusa execução se cobertura de velas $< 95\%$.
  1. Holdout selado: separação dos últimos 3 meses antes de qualquer processamento.
  2. Gramática: enumeração combinatória respeitando regras de compatibilidade e amostragem determinística.
  3. Replay: simulação sem lookahead contra o histórico de treino e validação.
  4. Portões 1..5: execução dos 5 filtros estatísticos com curto-circuito.
  5. Pré-aprovação e pontuação: cálculo dos 5 números para cada candidato.
  6. Holdout: unseal único de holdout para aprovados; desclassificação de reprovados e queima da faixa.
  7. Ranking e pontuação final.
  8. Sanidade: re-simulação e aprovação sobre série aleatória/embaralhada (`random_walk`) da rodada exigindo exatamente 0 aprovados, sob pena de abortar a rodada com `SanityCheckFailedError` e `status="aborted"`.
  9. Saída: emissão de `ranking.md` e `candidates.json`.
- `tools/strategy_lab/cli.py`: integração dos argumentos `--seed`, `--max-candidates`, `--active-manifest` e execução end-to-end do pipeline.

**Validação:**
- 14 novos testes automatizados em `tests/test_grammar.py`, `tests/test_holdout.py`, `tests/test_live_merge.py` e `tests/test_research_runner.py`:
  - Gramática nunca gera 2 primitivos da mesma categoria e exclui pares incompatíveis.
  - `total_candidates` preservado e amostragem determinística reprodutível com a mesma seed.
  - Separação dos últimos 3 meses e hash determinístico.
  - `open_once` bloqueia 2ª abertura com `RuntimeError`.
  - `burn` bloqueia reuso da faixa queimada na rodada seguinte.
  - Agregação e merge de live outcomes reduzem $\hat{p}$ e Wilson quando o resultado ao vivo decai.
  - **Critério de aceite 1**: `strategy-lab research --seed 1` sobre dados sintéticos com 1 edge injetado aprova somente ele.
  - **Critério de aceite 2**: Passo 8 (sanidade em random walk) aprova zero candidatos e emite log; série manipulada para aprovar no random walk dispara aborto imediato (`status="aborted"`).
  - Verificação de renderização da seção `## Novas oportunidades` no `ranking.md`.
- Suíte completa do Strategy Lab: 303 testes aprovados, 3 pulados (staging remoto).
- Ruff check: 100% aprovado.
- Ruff format: 100% formatado.
- Mypy: 78 arquivos verificados, 0 erros.
- Isolamento hermético `test_strategy_lab_isolation.py` no app desktop principal: 3 testes aprovados, 0 violações de isolamento.

## 2026-09-03 — P15: operação sem toque, CI intocável, runbook, agendador e VPS

**Requisitos:** R-OPS-1..4, R-ISO-2..6.

**Implementação:**
- `.github/workflows/ci.yml`: pipeline CI do GitHub Actions com jobs segregados:
  - `lint-and-typecheck`: validação com `ruff check`, `ruff format --check` e `mypy` no Lab e no Bot Desktop.
  - `untouchable-tests` (Job Obrigatório / Bloqueante): executa individualmente os 5 testes canônicos intocáveis da Arquitetura §11:
    1. `test_coin_flip_approves_zero` (Moeda)
    2. `test_primitives_parity_hash` (Paridade de Primitivos)
    3. `test_canary_fixture_matches` (Canário de Coleta)
    4. `test_hostile_manifests_rejected` (60 manifestos hostis rejeitados)
    5. `test_dst_and_current_candle_never_written` (Integridade temporal e sem vela corrente)
  - `unit-and-integration`: suíte geral de testes excluindo staging remoto (`-m "not staging"`).
  - `isolation-and-build-audit`: executa `test_strategy_lab_isolation.py` (varredura em AST de imports proibidos, pyproject.toml limpo, ausência de credenciais Supabase e inspeção dos artefatos em `dist/` e no `.exe` principal) e `scrub_secrets.py --all`.
  - `hub-deno-tests`: `deno check` e `deno test` nas Edge Functions de `apps/hub/supabase/functions`.
  - `staging`: job opcional acionado se o secret `SUPABASE_STAGING_DB_URL` estiver configurado.
- `scripts/scrub_secrets.py` + `.pre-commit-config.yaml`: ferramenta estática para detecção de chaves privadas PEM, JWTs reais, senhas expostas em código e connection strings de banco de dados; instalador de hook `.git/hooks/pre-commit`.
- `scripts/schedule_windows.ps1` + `scripts/run_status_toast.ps1`: automação para o Agendador de Tarefas do Windows registrando 4 tarefas (`\TradingLab\`):
  1. `TradingLab-Collect-Morning` (diário às 07:30 local)
  2. `TradingLab-Collect-Evening` (diário às 19:30 local)
  3. `TradingLab-Backup-Weekly` (domingos às 08:00 local)
  4. `TradingLab-Status-Daily` (diário às 20:00 local com notificação Toast do Windows em caso de anomalia).
- `deploy/vps/`: infraestrutura para VPS headless Linux (Ubuntu/Debian):
  - `install.sh`: provisionamento de usuário dedicado `strategylab`, venv Python 3.12 e permissões `0750`/`0600`.
  - `strategy-lab.service`: serviço oneshot com sandboxing systemd (`ProtectSystem=strict`, `NoNewPrivileges=true`).
  - `strategy-lab-collect.timer` / `service`: coleta diária às 07:30 e 19:30 UTC.
  - `strategy-lab-payout.timer` / `service`: coleta horária de payouts (`collect --payout-only`).
  - `strategy-lab-backup.timer` / `service`: backup semanal aos domingos às 08:00 UTC.
  - `env.example`: template `/etc/strategy-lab/env` configurado com permissões `0600`.
- `strategy-lab/RUNBOOK.md`: manual operacional cobrindo rotinas diária, semanal e mensal, contingências para todos os 11 pontos de falha da Arquitetura §9, incidentes operacionais detalhados, corte e ativação de chave privada A/B, migração para VPS e rotação de chaves.
- `strategy-lab/CHECKLIST-RELEASE.md`: checklist formal para liberação de versões com bump de `primitives_version`, verificação de hash de paridade, release do bot e janela de tolerância para clientes legados.

**Validação:**
- **CI / Testes Intocáveis**: Executados os 5 testes mandatórios via pytest (`test_coin_flip_approves_zero`, `test_primitives_parity_hash`, `test_canary_fixture_matches`, `test_hostile_manifests_rejected`, `test_dst_and_current_candle_never_written`) com 65 passed, 0 failed.
- **Isolamento de Build**: 4 testes de segurança em `test_strategy_lab_isolation.py` aprovados (incluindo inspeção do `dist/TradingLab`).
- **Scrub Secrets**: `python scripts/scrub_secrets.py --all` executado com 0 violações; detecção de chaves PEM e JWTs testada com sucesso.
- **Agendador Windows**: `powershell -ExecutionPolicy Bypass -File scripts\schedule_windows.ps1` executou com sucesso e criou as 4 tarefas no Windows Task Scheduler (`Get-ScheduledTask` confirmou todas em estado `Ready`).
- **Runbook Operacional**: 11/11 pontos de falha da Arquitetura §9 cobertos com ações concretas imediatas.



