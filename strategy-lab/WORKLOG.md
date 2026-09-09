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

## WL-2026-09-03-02 — Contrato aditivo de warmup no manifesto

R-MAN-1/3/4, R-PUB-1, R-PRIM-1/3/6, R-ISO-2..6.

- Pacote tl-manifest-schema 1.1.0; schema_version inteiro 1 preservado,
  schema_revision 1.1 aditiva exige warmup_required em cada estratégia nova.
  Campo calculado por instanciação dos primitivos locais conforme FAMILY_BINDINGS;
  nenhuma importação do projeto Desktop. F1/F2/F3/F4/F5 default: 28/20/1/39/15.
- Validação equivalente no JSON Schema e Hub. Manifestos e fixtures históricos
  sem revisão permanecem compatíveis. Preflight preserva fields-set do modelo
  ao verificar assinatura, evitando adicionar defaults aos bytes históricos.
- Volume opcional no Candle e comportamento explícito do TickVolumeRatio sem
  volume. Canonicalização do canário somente ampliou a anotação de retorno para
  None; fixture e comparação estrita não foram alteradas.
- Primeira suíte completa: 306 passed, 3 staging skipped por ausência de URL.
  Subset schema/publish após endurecimento v1.1: 131 passed. Nova rodada completa
  com testes adicionais está registrada no fechamento abaixo quando disponível.
- Fora do escopo: Supabase remoto, publicação, coleta autenticada, ordens,
  avaliação incremental e rebuild. Não foi validada uma segunda máquina.

Fechamento: **312 passed, 3 skipped** (exclusivamente staging sem URL);
**mypy --strict: 79 arquivos aprovados**; **Ruff check: aprovado**.
Format-check global encontrou somente formatação anterior em
`tests/test_closing_checklist_lab.py`, não alterado. Compileall aprovado.
Paridade numérica pública mantida; Desktop executou seu próprio vetor e testes
de isolamento (86 testes focados aprovados no conjunto final).
Teste Deno aditivo criado mas não executado por ausência de runtime local.
Nenhuma alteração de Supabase/deploy ou conta de corretora foi realizada.

## 2026-09-05 — CAT-00: baseline e contrato de semântica (diagnóstico)

R-ISO-1..6, R-PRIM-1..7, R-RES-1..11, R-BOT-1..13.

- O CAT-00 registrou o baseline conjunto em
  `../docs/CAT00_BASELINE_AND_CATALOG_AUDIT.md` e a decisão estrutural em
  `../docs/ADR_EXECUTION_SEMANTICS_AND_BOOTSTRAP.md`.
- LAB: 312 testes passaram; 3 testes staging foram ignorados por ausência de
  `SUPABASE_STAGING_DB_URL`. Ruff check, mypy strict (79 arquivos) e pip check passaram.
  Format-check apontou o arquivo preexistente `tests/test_closing_checklist_lab.py`.
- Deno check/test/fmt não foi executado porque o runtime não está disponível neste host.
- O manifesto foi somente auditado: assinatura aceita, mas o run de origem
  `run_complete_catalog` não foi localizado e Wilson 0.557 não reproduz o cálculo atual
  para 578/1000 (0.5471483340786791358059675999). Nenhum dado foi reclassificado.
- Encontrado, não corrigido: replay e bot divergem em bootstrap, gates de composição,
  timeframe, horário e tratamento da próxima vela; o relatório escreve `8/8` e deriva
  `holdout_passed` do estado geral de aprovação.
- Fora do escopo: código operacional, Supabase remoto, migration, coleta, pesquisa,
  publicação, exclusão, build e corretoras. Nenhuma ordem foi enviada.

## 2026-09-05 — CAT-01: inventário e orçamento Supabase somente leitura

R-HUB-1..8, R-COL-1..13, R-OPS-1..4, apenas diagnóstico.

- Relatório conjunto criado em `../docs/CAT01_SUPABASE_INVENTORY_AND_CAPACITY_BUDGET.md`.
- Supabase CLI 2.116.0 pinada foi verificada; acesso remoto recusado sem token seguro.
  URL/ref de staging e ref de produção também não estão configurados, então staging não foi
  inferido pelo nome e toda medição remota foi marcada `BLOCKED`.
- Seis migrations, onze tabelas pretendidas, cinco Edge Functions e contratos de Storage foram
  inventariados localmente. Foram registrados, sem alterar código aplicado, os riscos de archive
  stub, callback por contagem, grants de funções, publicação parcial, calendário incompleto e
  índices potencialmente redundantes.
- Modelo de crescimento e budgets warning/critical foram propostos, mas médias/p95, quota e
  capacidade restante continuam desconhecidos até o inventário autenticado de staging.
- Nenhuma migration editada, nenhum deploy, DDL, DELETE, coleta, pesquisa, publicação, acesso a
  broker ou ordem. Credenciais anteriormente expostas não foram usadas.

## 2026-09-05 — CAT-02: contrato público v1.2 de receita e capacidades

R-MAN-1..7, R-ISO-1..6, R-PRIM-1..7; contrato preparatório de R-RES/R-BOT, sem pesquisa ou
execução.

- `tl-manifest-schema` atualizado de 1.1.0 para 1.2.0. A revisão aditiva 1.2 exige semântica
  `tl.candle-close.v2`, revisão/fingerprint determinístico da receita, composição fechada F1..F5,
  evidência do dataset e requisitos de produto, timeframe, warmup e tick volume.
- Modelo Pydantic, JSON Schema exportado e oráculo de testes foram sincronizados. A revisão 1.1
  preserva compatibilidade e rejeita contrabando de campos 1.2; documentos históricos assinados
  e o vetor v1 permanecem imutáveis.
- `strategy_contract_vectors.v2.json` é público, canônico, auto-hasheado e consumido
  independentemente pelo Lab e pelo Desktop. Abrange F1..F5, consenso, bootstrap, tie=loss,
  elegibilidade/capabilities, assinatura, campos hostis, origem sintética e telemetria opt-in.
- Dataset sintético nunca pode publicar estratégia `approved`. Não foi introduzido código
  arbitrário, família, parâmetro, fórmula ou mudança na paridade dos primitivos.
- Rollout fixado: leitor 1.2 primeiro; dataset/replay; engine incremental/paridade; shadow no bot;
  só depois Hub e publisher 1.2. O publisher atual continua emitindo 1.1.
- Validação: schema/contrato **140 passed**; vetor CAT-02 isolado **16 passed**; suíte integral
  **328 passed, 3 skipped** (staging sem URL). Ruff check aprovado; mypy strict em 81 arquivos e
  pip check aprovados. Format-check mantém apenas o arquivo preexistente
  `tests/test_closing_checklist_lab.py`.
- O Desktop executou sua suíte contratual independente com **83 passed** e recusa a semântica v2
  por capability enquanto o engine correspondente não existe. Scanner de segredos e diff-check
  aprovados.
- Nenhuma chamada Supabase, migration, função, Storage, publicação, corretora ou ordem. Segredos
  expostos na conversa não foram usados nem gravados; rotação externa permanece necessária.

## 2026-09-05 — CAT-03: dataset real, identidade e payout as-of

R-COL-3..9, R-RES-1, R-RES-4, R-RES-11, R-ISO-1..6.

- `ResearchDataset` passou a gerar `DatasetSnapshot` com origem, fonte, ativo exato, timeframe,
  intervalo, qualidade e fingerprint imutável. `EURUSD` e `EURUSD-OTC` não são normalizados nem
  misturados.
- Pesquisa real agora exige fonte explícita (`--supabase` ou Parquet completo); sintético só roda
  com `--synthetic` e seus relatórios seguem `production_eligible=false`.
- Agregação M1->M5/M15 exige buckets UTC completos e recusa vela corrente/parcial. Cobertura usa
  sessão de mercado e gaps `in_session`, sem penalizar período fechado.
- Payout ganhou regra point-in-time: observações têm `observed_at`; média horária legada sem
  as-of recebe `RES_PAYOUT_LEGACY_NO_ASOF` e não alimenta sinais.
- Criada migration local `0007_payout_observations.sql`; `FakeRepository`, `PostgresRepository`
  e sampler foram adaptados para observações pontuais e agregado horário compatível.
- Volume ausente permanece `None`; candidatos que dependem de `tick_volume_ratio` ficam
  inelegíveis com `RES_TICK_VOLUME_UNAVAILABLE`.
- Relatórios de pesquisa incluem evidência pública do snapshot e elegibilidade de produção.
- Testes novos em `tests/test_cat03_dataset_contract.py` cobrem ativo exato, TFs, vela corrente,
  payout futuro/samples zero/legado, volume ausente, sessões, fingerprint e fonte explícita.
- Validação: **336 passed, 3 skipped**; Ruff check e format-check aprovados; mypy strict em
  81 arquivos aprovado; compileall aprovado; `scrub_secrets.py --all` e `git diff --check`
  aprovados.
- Não executado: Supabase remoto/staging, deploy de migration, coleta real, archive hot/cold,
  publicação, corretora ou ordem. Skips staging continuam por ausência de `SUPABASE_STAGING_DB_URL`.

## 2026-09-05 — CAT-04: replay de referência e contrato público

R-RES-4, R-RES-5, R-RES-6, R-BOT contrato independente.

- `replay_simulator.py` passou a aplicar os gates de composição F1 `adx_max` e F4
  `width_ratio_max`, respeitar `candidate.tf`, `candidate.hours` e derivar `SessionWindow`
  diretamente de `hours_utc`.
- A liquidação do replay agora exige a próxima vela completa do mesmo timeframe; a próxima linha
  após gap é classificada como `SETTLEMENT_GAP`, não como `t+1`.
- Volume ausente em família com `tick_volume_ratio` bloqueia como
  `TICK_VOLUME_UNAVAILABLE` após warmup suficiente.
- Criado o vetor público `contracts/replay_contract_vectors.v1.json`, SHA-256
  `1059d58db4ead251e9be720218427b5dcb883437142d1af76a07ce43782acdde`, com 7 casos cobrindo
  F1..F5, múltiplos ativos/TFs, gate, volume ausente, fora de horário, empate, gap e
  reinicialização por prefixo.
- O Desktop Bot ganhou teste contratual independente que consome apenas o JSON público e valida
  as famílias locais sem importar Strategy Lab.
- Validação: Lab focado **27 passed**, Ruff check/format e mypy aprovados; Bot contrato
  **7 passed**, Ruff check/format e mypy scoped aprovados.
- Não executado: Supabase remoto/staging, coleta real, publicação, corretora, ordem, build do EXE
  ou aprovação de estratégia nova.

## 2026-09-05 — CAT-05: evidência durável e holdout protegido

R-HUB-1, R-HUB-2, R-HUB-8, R-RES-2, R-RES-11.

- Criada migration local `apps/hub/supabase/migrations/0008_research_evidence.sql` com
  `dataset_snapshots`, `holdout_reservations`, `research_attempts` e
  `research_evidence_artifacts`; RLS habilitado e acesso `anon/authenticated` revogado.
- Criado `research/evidence.py` com contratos tipados, `FakeEvidenceRepository`,
  `MemoryArtifactStore`, reserva concorrente de holdout e artefatos privados hash-addressed.
- Criado `research/pg_evidence_repository.py` para persistir snapshot, reserva, abertura,
  burn/rollback de holdout, tentativas e artefatos no Postgres/Supabase.
- `run_research_pipeline` agora falha fechado quando dataset elegível para produção não possui
  repositório de evidência; quando fornecido, registra snapshot, reserva holdout, bloqueia range
  queimado, grava tentativa por candidato e artefato de TradeLog sem duplicar candles.
- `HoldoutManager` legado passou a carregar ranges queimados do banco no startup e não engole
  falhas de banco com `except/pass`.
- Testes novos em `tests/test_cat05_evidence_contract.py` cobrem falta de evidência, burn após
  run aprovado, bloqueio em restart/run seguinte, rollback sem pré-aprovados, concorrência,
  falha de leitura/escrita e erro de banco não silenciado.
- Validação: suíte integral **345 passed, 3 skipped**; Ruff check/format, mypy strict em
  84 arquivos, compileall, scanner de segredos raiz e `git diff --check` aprovados.
- Não executado: Supabase remoto/staging, aplicação da migration, Storage real, deploy,
  publicação, corretora, ordem, aprovação de estratégia nova ou build do EXE.

## 2026-09-05 — CAT-06: aprovação estatística sem atalhos

R-RES-2, R-RES-7, R-RES-8, R-RES-9, R-RES-11.

- Produto alterado: Strategy Lab. Relatório criado em
  `docs/CAT06_STATISTICAL_APPROVAL_NO_SHORTCUTS.md`.
- `approve_candidate` agora registra gates preliminares reais (`payout_available`,
  `sample_size`, `pessimistic_wilson`) e reprova payout ausente com `RES_PAYOUT_MISSING`.
- O FDR/BH usa o conjunto real de candidatos da rodada, com p-values derivados de `TradeLog`,
  em vez de tratar todo candidato como rank 1.
- `windows_passed` no `candidates.json` agora resume janelas reais do walk-forward; `gates_passed`
  ficou separado para contagem total dos portões.
- `holdout_passed` deixou de ser derivado de `approved`; no runner real só é preenchido após a
  abertura controlada do holdout.
- Dataset elegível a produção com candidato pré-aprovado falha fechado em holdout curto com
  `RES_HOLDOUT_WINDOW_TOO_SHORT`.
- Família desconhecida não cai mais em F1; falha com `RES_UNKNOWN_FAMILY`.
- Caminhos de permutação/FDR e PBO foram mantidos em `Decimal`, sem chamada a `float`.
- Validação do Lab: focados **23 passed**; regressão P10/CAT03/CAT06 **10 passed**; suíte integral
  **351 passed, 3 skipped**; Ruff check/format, mypy, compileall, scanner de segredos raiz e
  `git diff --check` aprovados.
- Não executado: Supabase remoto/staging, aplicação de migration remota, coleta real, publicação,
  corretora, ordem, aprovação de estratégia nova ou build do EXE.

## 2026-09-05 — CAT-07: pesquisa ampla, limitada e compatível com executor

R-RES-3, R-MAN-3, R-ISO-6.

- Produto alterado: Strategy Lab. Relatório criado em
  `docs/CAT07_EXECUTOR_COMPATIBLE_RESEARCH_GRAMMAR.md`.
- A gramática passou a consumir capacidades do executor antes da amostragem: famílias, TFs,
  tick volume, ativos suportados, warmup máximo e budget de trials.
- Criado budget nomeado `DEFAULT_TRIAL_BUDGET = 500`; enumeração agora usa amostragem
  determinística por seed + hash, em fluxo e com deduplicação.
- `GrammarResult.audit_report()` registra universo teórico, elegíveis, amostrados, descartes por
  motivo e diversidade por família/ativo/timeframe/horário.
- Corrigida a eliminação indevida da F5: o trio canônico
  `session_window + quadrant_majority + rsi_extreme` é permitido por existir no contrato público;
  combinações não canônicas com o par incompatível continuam proibidas.
- F3/`level_touch` exige perfil explícito de níveis por ativo e não usa defaults sintéticos 99/101
  para Forex.
- `generate_param_values` preserva grade declarada em min/mid/max; último valor desalinhado ao
  step não é emitido.
- Audit padrão com seed 7: 116.640 elegíveis, 500 amostrados, diversidade F1=123, F2=152,
  F4=119, F5=106.
- Validação do Lab: gramática/CAT07 **10 passed**; suíte integral **357 passed, 3 skipped**;
  Ruff check/format e mypy aprovados; smoke CLI sintético concluído com `status=ok`.
- Não executado: Supabase remoto/staging, aplicação de migration remota, coleta real, publicação,
  corretora, ordem, aprovação de estratégia nova ou build do EXE.

## 2026-09-05 — CAT-11: replay da frequência realmente executável do portfólio

R-RES-4, R-RES-5, R-RES-9, R-RES-11, R-ISO-3.

- Produto alterado: Strategy Lab. Relatório criado em
  `docs/CAT11_PORTFOLIO_EXECUTABLE_REPLAY.md`.
- Criado `research/portfolio_replay.py`, uma camada data-only para transformar oportunidades
  aprovadas pelo replay individual em frequência executável de portfólio.
- O replay de portfólio aplica arbitragem determinística por ativo/timeframe/fechamento, TTL,
  atraso de execução, payout observado, capital, stop/take, limite de perdas, cooldown e exatamente
  uma ordem em voo por conta.
- Cada oportunidade recebe motivo estável: `ELIGIBLE`, `CONFLICT`, `ORDER_IN_FLIGHT`,
  `MISSING_PAYOUT`, `OUTSIDE_HOURS`, `WARMUP`, `STALE`, `RISK`, `DEADLINE_EXPIRED`,
  `EXECUTION`, `RECONNECTING`, `CANDLE_DELAYED` ou `INVALID_DIRECTION`.
- Adicionado carregador do artefato público `contracts/replay_contract_vectors.v1.json`, sem
  importar o Desktop Bot, para provar que o replay de portfólio parte dos mesmos vetores de decisão
  CAT-04.
- Evidência numérica: 4 sinais sintéticos em 10 min equivaleram a 576 sinais/dia, mas apenas 2
  operações executáveis, 288 ops/dia; 50 receitas
  duplicando o mesmo evento produziram 50 sinais e só 1 operação executável, com 49 `CONFLICT`.
- Validação no `.venv` próprio do Lab: CAT-11 **8 passed**; regressão research/contrato
  **33 passed**; suíte completa **365 passed, 3 skipped**; Ruff check/format, mypy, compileall e
  `git diff --check` aprovados.
- Observação: pytest emitiu aviso de cleanup do diretório temporário `pytest-current` no Windows
  após concluir com código 0; não houve falha de teste.
- Não executado: Supabase remoto/staging, coleta real, publicação, corretora, ordem, seleção de
  portfólio CAT-12, benchmark do EXE, build do EXE ou qualquer validação financeira externa.

## 2026-09-05 — Supabase Hub apply remoto preparado, não executado

R-HUB-1, R-HUB-2, R-HUB-3, R-HUB-4, R-HUB-5, R-HUB-6, R-HUB-8, R-OPS-1.

- Validada localmente a CLI pinada `state/tools/supabase-cli-v2.116.0/supabase.exe` e o Deno
  pinado `state/tools/deno-v2.9.6/deno.exe`.
- Edge Functions locais validadas: `deno fmt --check`, `deno lint`, `deno check` e `deno test`
  concluíram com **13 passed**.
- Criado `scripts/supabase_apply_remote.ps1` para aplicar o Hub remoto via CLI sem secrets
  hardcoded: migrations, buckets `manifests`/`parquet`, secrets temporários em `state/` com remoção
  no `finally`, e deploy das Edge Functions.
- O script foi executado sem credenciais ambientais e falhou fechado com
  `SUPABASE_STAGING_DB_URL is required`, como esperado.
- Checagem `rg` confirmou que as credenciais coladas no chat não foram gravadas em arquivos do
  repositório.
- Não executado: `db push`, criação de buckets remotos, `secrets set`, deploy de functions,
  smoke remoto ou alteração de dados, porque `SUPABASE_STAGING_DB_URL`, `SUPABASE_PROJECT_REF` e
  `SUPABASE_ACCESS_TOKEN` não estavam configurados no ambiente seguro.

## 2026-09-05 — Supabase Hub staging aplicado e validado remotamente

R-HUB-1, R-HUB-2, R-HUB-3, R-HUB-4, R-HUB-5, R-HUB-6, R-HUB-7, R-HUB-8.

- Supabase CLI `2.116.0` autenticada pelo fluxo oficial, Hub inicializado e vinculado ao projeto
  staging explicitamente indicado pelo operador.
- Preflight confirmou PostgreSQL `17.6` e extensão `pg_net` disponível.
- Migrations `0001` a `0008` aplicadas; conferência final mostrou todas presentes tanto localmente
  quanto no histórico remoto.
- As 13 tabelas de domínio presentes no projeto estão com RLS habilitado (13/13).
- Buckets criados idempotentemente: `manifests` público e `parquet` privado.
- Secrets de staging configurados sem persistência no repositório: `HUB_ENV`, uma nova chave
  aleatória `HUB_JWT_SECRET` gerada em memória e a chave pública de teste. O segredo colado na
  conversa não foi reutilizado.
- Edge Functions remotas ativas: `archive`, `mirror`, `publish`, `client_token` e `outcomes`.
  `client_token`/`outcomes` usam autenticação própria; as demais preservam o gate JWT da Supabase.
- Smoke remoto `client_token` retornou 201. O primeiro smoke de `outcomes` revelou contrato
  incompleto: timestamp fora da grade M1 chegava ao check do banco e virava 500. A fronteira agora
  rejeita `ts % 60 != 0` com 422; regressão Deno adicionada; novo smoke retornou 202, persistiu uma
  linha e a limpeza confirmou zero linhas restantes.
- Smoke remoto `publish`: primeira versão 201, reenvio 409, objeto público 200 e ETag presente.
  O metadado de `current.json` e `v14.json` gravou `max-age=900`; o endpoint público hospedado
  respondeu `no-cache`, limitação externa documentada em `supabase/storage#1290`. Fixture, linha de
  manifesto e objetos foram removidos; estado final: zero manifestos, outcomes e objetos de smoke.
- `scripts/supabase_apply_remote.ps1` passou a aceitar projeto já vinculado, rejeitar URL HTTPS no
  lugar de PostgreSQL, usar a sessão salva da CLI, fixar `--agent no` e separar deploy JWT-protegido
  de custom-auth. Reexecução completa passou e informou banco atualizado.
- Validação local final: Deno fmt/lint/check e **15 passed**; Strategy Lab **365 passed, 3 skipped**;
  Ruff check/format, mypy strict, compileall, parse PowerShell, scanner de segredos e
  `git diff --check` aprovados. Os três skips seguem sendo os testes que exigem uma URL PostgreSQL
  staging explícita, embora os smokes equivalentes tenham sido executados remotamente pela CLI.
- Limitações externas reais: R2 não possui credenciais/configuração; `mirror` está implantada mas
  não consegue espelhar. `archive` continua sendo stub e o cron ativo não possui URL/token de
  arquivo; arquivamento frio não foi validado. Chaves Ed25519 A/B de produção não foram fornecidas;
  o Hub permanece estritamente em staging com trust root de teste.
- Nenhuma corretora, conta financeira, ordem ou ambiente Real foi acessado.

## 2026-09-06 — CAT-12: seleção offline por qualidade e complementaridade

Requisitos cobertos: CAT-12; dependências preservadas: CAT-07 e CAT-11.

- Criado `research/portfolio_selection.py`, isolado de publisher, Supabase, coleta, bot e broker.
- A composição recebe somente receitas com veredito individual e referência de evidência. Receita
  reprovada não participa do ranking, mesmo quando possui mais sinais no fixture.
- Seleção gulosa determinística mede ganho marginal no replay executável CAT-11 e desempata por
  robustez, menor sobreposição `(asset, signal_ts)` e `recipe_key` lexical.
- Configuração imutável declara antes do holdout: seed/snapshot, targets 10/20/30/50/100,
  correlação, concentrações por ativo/hora, ganho marginal, capacidade, 6.000 tentativas e gates
  finais. O `config_hash` e o `selection_hash` tornam a escolha reproduzível.
- Pesquisa sintética e histórico real não podem ser misturados na mesma seleção. O holdout exige
  snapshot e intervalo temporal próprios e `HoldoutLedger` recusa uma segunda abertura do mesmo
  snapshot, inclusive após falha.
- Relatório inclui Wilson 95%, payout observado/de equilíbrio, EV/stake, sensibilidades −0,5 pp e
  −1,0 pp, frequência OOS, drawdown, streak, concentração e custo computacional.
- Rascunho de manifesto referencia evidência, permanece sem assinatura e fixa
  `publish_automatically=false`; não existe caminho de publicação automática.
- Comparação diária com 12 receitas prova targets indisponíveis sem inventar receita. Ensaio
  adicional com 100 receitas concluiu com **9 passed**, 100 operações executáveis e 141,61 s; o
  custo fica documentado para CAT-19 e o caso longo não integra a suíte diária.
- Documento: `docs/CAT12_OFFLINE_QUALITY_COMPLEMENTARITY_SELECTION.md` no projeto principal.
- Validação focada CAT-11/CAT-12: **19 passed**. Suíte completa do Lab: **376 passed, 3 skipped**.
  Ruff check/format, mypy strict e compileall aprovados; teste de isolamento do projeto principal:
  **4 passed**. O aviso de cleanup `pytest-current` no Windows ocorreu após exit code 0.
- Nenhum dataset real ou holdout real foi aberto nesta fase; não há alegação de retorno garantido.
  Nenhum backend remoto, corretora, conta financeira ou ordem foi acessado.

## 2026-09-06 — CAT-13: publicação recuperável e ponteiro autoritativo de manifesto

Requisitos cobertos: CAT-13; dependências preservadas: CAT-10, CAT-11 e CAT-12.

- Criada migration `apps/hub/supabase/migrations/0009_publication_pipeline.sql` com
  `publication_journal`, `manifest_pointers`, `manifest_mirror_outbox` e RPCs de reserva,
  confirmação de objeto, commit, marcação de projeção legada, falha, claim/complete/fail do mirror.
- `publish` passou a validar schema/assinatura/política de produção, gravar `vN.json` imutável
  (`x-upsert=false`), verificar SHA-256, commitar o ponteiro apenas após objeto confirmado, reparar
  `current.json` a partir do ponteiro e responder reenvio idempotente com `200`.
- Publicação em produção exige `schema_revision=1.2`, dataset `real_market`, run selado, snapshot
  elegível, tentativa aprovada e artefato `portfolio_selection`; chave de teste continua restrita
  a staging.
- Criada Edge Function pública `manifest_current`, que resolve o ponteiro confirmado no banco,
  verifica hash do objeto imutável, devolve `ETag`/cache e usa fallback last-good quando o ponteiro
  atual estiver indisponível ou corrompido.
- `mirror` deixou de depender de disparo best-effort: agora consome outbox durável, limita tentativas
  com backoff, verifica hash na origem e no destino e registra falha sem Promises soltas.
- `scripts/supabase_apply_remote.ps1` agora implanta `manifest_current` no grupo custom-auth e
  `apps/hub/supabase/config.toml` fixa `verify_jwt=false` para o endpoint público.
- Documentação adicionada em `docs/CAT13_RECOVERABLE_MANIFEST_PUBLICATION.md` e README do Hub
  atualizado com a semântica de saga, `manifest_current`, idempotência e `current.json` legado.
- Aplicado remotamente no projeto staging via Supabase CLI: preflight `pg_net`, `db push` da
  migration `0009`, buckets conferidos e deploy de `archive`, `mirror`, `publish`, `client_token`,
  `outcomes` e `manifest_current`.
- Smoke remoto controlado: criado `research_run` mínimo para fixture, `publish=201`,
  `manifest_current=200`, reenvio idempotente `publish=200`, versão retornada `14`, fallback
  `false`. Registros de smoke e objetos `v14.json`/`current.json` foram removidos; contagem final
  de `publication_journal`, `manifest_pointers`, `manifest_mirror_outbox`, `manifests` e
  `research_run_smoke` voltou a zero.
- Validação local: Deno fmt/lint/check e **20 passed**; teste CAT-13 Python **6 passed**; suíte
  completa do Lab **382 passed, 3 skipped**; Ruff check/format, mypy strict, compileall,
  parse PowerShell, scanner de segredos e `git diff --check` aprovados; isolamento do projeto
  principal **4 passed**.
- Limitações: os três skips seguem exigindo `SUPABASE_STAGING_DB_URL` explícita no pytest. R2 não
  possui credenciais/configuração, então o mirror foi validado localmente com fake e implantado, mas
  não espelhou para R2 no smoke remoto. Nenhuma corretora, conta financeira, ordem ou ambiente Real
  foi acessado.

## 2026-09-06 — CAT-15: outcomes versionados, privados e limitados

Requisitos cobertos: R-HUB-2, R-HUB-4, R-RES-12; invariantes I-1, I-2, I-6, I-7, I-8 e I-14.

- Criadas migrations `0010_outcomes_v2.sql` e `0011_outcome_budgets.sql`. Histórico v1 foi
  preservado; inserção anon direta foi revogada e v2 usa tabelas/RPCs sem grants públicos.
- Payload v2 identifica receita/revisão, manifesto/engines, série, produto, ambiente, origem,
  grupo dependente e evento terminal. Payout é decimal textual; PII/campos extras são recusados.
- Ingestão e agregado são transacionais e exactly-once por evento e cliente+sinal. Relatos do
  mesmo sinal não são tratados como amostras estatísticas independentes.
- JWT novo vincula `client_id` e ambiente. Token legado só escreve v1. Quotas globais e por cliente
  cobrem requests e quantidade de eventos; UUID rotativo não remove o teto global.
- Retenção: evento mínimo normalizado 90 dias, agregado 400 dias; nenhum payload bruto persistido.
  Outcomes não possuem caminho de promoção, aprovação ou mutação de manifesto.
- Migrations `0010` e `0011` aplicadas e funções redeployadas no Supabase staging vinculado.
  Smoke remoto Practice: primeira ingestão `1`, retry duplicado `0`; dados sintéticos removidos.
  Smoke legado v1 retornou `accepted=1`; consulta remota confirmou zero grants/policies/RPC de
  budget para anon. Todos os registros sintéticos dos smokes foram removidos.
- Validação final: Deno fmt/lint/check e **25 passed**; Lab completo **382 passed, 3 skipped**;
  Ruff check/format, mypy strict (**85 arquivos**) e compileall aprovados. Os três skips exigem
  `SUPABASE_STAGING_DB_URL` explícita nos testes Postgres; o apply/smoke remoto foi executado
  separadamente pela CLI vinculada. O aviso `pytest-current` ocorreu após exit code 0 no Windows.
- Nenhuma corretora, conta financeira, ordem, credencial de broker ou ambiente Real foi acessado.

## 2026-09-06 — CAT-17: arquivo frio verificável, hot+cold e restore

Requisitos cobertos: R-HUB-7, R-OPS-1, continuidade de R-COL-3, R-COL-6 e R-RES-1;
invariantes I-1, I-2, I-6, I-7, I-8 e I-14 preservadas.

- Migrations `0012`–`0014` criaram jobs/recortes congelados, claims com lease/token, índice de
  objetos verificados e watermark durável separado da tabela quente. A conclusão antiga por
  contagem foi desabilitada e perdeu grants públicos.
- A remoção segura compara PK e todo conteúdo OHLC/volume/source/collected_at antes de apagar
  apenas as linhas congeladas. Atualização tardia falha sem delete.
- Executor local gera Parquet Zstandard com Decimal, faz upload privado imutável, baixa novamente
  e verifica bytes, schema, contagem, PK, OHLC, payouts point-in-time e fingerprint.
- Reader research une hot+cold sem duplicação e recusa conteúdo divergente. Backup inclui objetos
  frios e manifesto selado; restore foi verificado em destino isolado.
- Edge `archive` virou control-plane autenticado: planeja e alerta backlog, mas nunca converte nem
  remove. O cron usa partições de até 30 dias e piso de 1.000 velas para evitar microarquivos.
- Staging antes da aplicação: zero candles, jobs e objetos. Migrations aplicadas e função archive
  v7 publicada. Smoke HTTP do control-plane: 200, backlog zero, `deletion_performed=false`.
- O primeiro round-trip revelou regex de path superescapada; falhou fechado sem remover as três
  velas. A correção foi adicionada na migration 0013, sem reescrever a 0012 já aplicada.
- Round-trip staging final: 3 velas/payouts, 1 objeto de 4.341 bytes, fingerprint
  `04db071ac8f4755f35b98e950c814bc35063ac9967901fcf5fa36edce658ed9a`, 3 deletes exatos,
  reconstrução research 3/3 e restore isolado 1/1. O fixture `CAT17TEST` foi removido por alvo
  exato; estado final voltou a zero candles, payouts, jobs e objetos do smoke.
- Testes CAT-17: 14 aprovados. Lab completo: **396 passed, 3 skipped** (URL PostgreSQL staging
  ausente no processo pytest). Ruff check/format, mypy strict em 88 fontes, compileall,
  `db lint --linked --level error` e `git diff --check` aprovados.
- Não houve corretora, conta financeira, ordem, conta Real, dado de cliente ou exclusão de
  produção. Medição de compressão representativa aguarda partição real com pelo menos 1.000 linhas.

## 2026-09-06 — CAT-18: retenção e quota com plano fechado

Requisitos cobertos: R-OPS-1, R-HUB-7 e invariantes I-1, I-2, I-6, I-7, I-8 e I-14.

- Criado `retention.py` com inventário tipado, categorias de retenção, política explícita,
  plano determinístico por SHA-256, referências protegidas, confirmação e aplicação fail-closed.
- Produção exige aprovação explícita; qualquer alteração no projeto, snapshot, alvo, tamanho,
  versão ou hash aborta antes do primeiro delete. Wildcards, cascade e drop não existem.
- CLI `strategy-lab retention` é dry-run por padrão e pode salvar plano JSON para revisão. A
  execução exige `--execute`, plano, token `RETENTION-APPLY:<hash>` e, em produção, flag adicional.
- Adaptador REST Supabase lista objetos do bucket `manifests` sem imprimir credenciais e só aceita
  remoção de caminho exato. Migration `0015_retention_plans.sql` registra planos/alvos/eventos
  append-only, com RLS sem acesso anon/authenticated.
- Quota reporta bytes físicos, backlog e severidade; em crítico recomenda pausar ingestão/pesquisa,
  nunca apagar evidências. Índices e dados financeiros permanecem fora da limpeza automática.
- Migration `0015` aplicada no Supabase vinculado (`jciclczthkbpvvqnrnbf`); `db lint --level error`
  e inventário de migrations confirmaram o schema remoto. O inventário Storage, somente leitura,
  retornou zero objetos no bucket `manifests`; seu plano ficou vazio e não autorizou remoção.
  Testes CAT-17/CAT-18: 22 aprovados; suíte integral do Lab: 404 aprovados, 3 skips de staging
  (sem `SUPABASE_STAGING_DB_URL` no processo pytest). Ruff, formatação, mypy strict (90 fontes),
  compileall e `git diff --check` aprovados.
- Nenhuma exclusão remota, corretora, ordem, conta Real ou alteração de produção foi executada.

## 2026-09-08 — Início da pesquisa real: bootstrap seguro da credencial de coleta

**Requisitos:** R-COL-1; invariantes I-7, I-8 e I-14.

- A inspeção remota somente leitura confirmou zero velas, payouts, runs de coleta, snapshots e
  objetos frios no Supabase vinculado. Os rankings locais existentes usam dados sintéticos e não
  constituem evidência de estratégia rentável ou publicável.
- Adicionado `strategy-lab credentials set`, que recebe e-mail e senha somente por prompt
  interativo, mascara a senha, valida a gravação e usa exclusivamente o destino
  `StrategyLab/IQOption/collection` do cofre do sistema operacional.
- Adicionado `strategy-lab credentials status`, que retorna somente disponibilidade booleana e
  não expõe identidade, senha ou conteúdo da credencial.
- O caminho não lê credenciais, vault, estado ou arquivos privados do Desktop Bot. Não houve
  coleta externa, autenticação na corretora, publicação de manifesto ou ordem financeira.
- Testes focados de credencial e coleta: 19 aprovados. Ruff e mypy dos arquivos alterados
  aprovados após o ajuste final.

**Pendente:** o operador deve preencher o prompt mascarado no próprio terminal. Depois disso,
gravar um canário real revisado e iniciar o backfill/payout antes de qualquer pesquisa ou promoção.

## 2026-09-08 — Primeira coleta IQ real e validação point-in-time

**Requisitos:** R-COL-1, R-COL-2, R-COL-3, R-COL-5, R-COL-6, R-COL-7, R-COL-8,
R-COL-10, R-COL-12, R-COL-13 e R-RES-1; invariantes I-1, I-2, I-4, I-7, I-8 e I-14.

- A credencial cadastrada no WinVault foi consumida apenas pelo adaptador externo e sem expor
  identidade ou segredo. Nenhuma credencial foi gravada em relatório, argumento ou arquivo do Lab.
- O catálogo real continha 240 linhas, das quais 64 nomes não canônicos foram explicitamente
  excluídos. Corrupção estrutural, duplicidade de ativo canônico e payout canônico inválido
  continuam falhando fechados; não foi criada normalização ou alias implícito.
- A semântica inclusiva do fim de intervalo do broker foi comprovada e o adaptador passou a
  consultar `end_ts - 1`, preservando o contrato local de limite superior exclusivo.
- Foi gravado e reconsultado um canário real de cinco velas M1 fechadas de `EURUSD-OTC`, com
  SHA-256 `af1604bcc3487937d4593b4fa42eb2d65e38cddb28fd7bab7469c329b955769f`.
- A primeira transação persistente gravou 995 velas reais, uma observação de payout `0.82` e o
  run `7759af72ed9e408c8a81249d4b79b297`. O estado remoto final confirmado contém 995 velas,
  de `1788818160` a `1788878100`, sem ordens ou ações financeiras.
- O horário observado no catálogo para esse ativo foi registrado pela migration
  `0019_eurusd_otc_schedule_20260908.sql`: todos os dias, 00:00–08:00 e 08:30–24:00 UTC.
  A migration foi aplicada no Supabase vinculado; o gap 08:05–08:10 ficou fora de sessão.
- A cobertura point-in-time do intervalo elegível foi 970/970 (`1.000000`), sem gap em sessão.
  As 25 velas presentes na pausa 08:00–08:30 foram preservadas, mas não contam na grade elegível.
- O UPSERT PostgreSQL passou de uma execução por vela para um único lote parametrizado com
  `unnest`. O teste de staging usa ativo aleatório e rollback obrigatório. Uma única vela fake
  deixada pelo teste antigo foi identificada por chave exata e removida; a contagem real voltou
  imediatamente a 995.
- A consulta de research escapou corretamente o operador módulo em SQL parametrizado (`%%`),
  com teste real de staging. Idempotência, constraints e consulta de cobertura foram exercitadas.
- Foi descoberto que o limite documentado de duas tentativas de login IQ por dia não tinha
  implementação. As sessões diagnósticas anteriores à descoberta excederam essa intenção.
  `login_budget.py` agora reserva antes da rede, persiste por dia UTC, usa lock entre processos,
  escrita atômica e falha fechada em corrupção, regressão de relógio ou contenção. O orçamento
  do dia ficou esgotado e nenhuma nova sessão externa foi aberta depois da correção.
- A observação de payout ocorreu depois da última vela coletada e não foi aplicada
  retroativamente. Por isso não houve replay, ranking, aprovação, publicação de manifesto ou
  alegação de assertividade/frequência nesta etapa.
- Validação final: Lab completo com **454 passed, 4 skipped**; integração real de staging com
  **3 passed, 1 skipped** (URL anônima de RLS ausente); Ruff check/format, mypy strict em 92
  fontes de produção, compileall, `db lint --linked --level error` e diff-check com
  `cr-at-eol` para o checkout Windows aprovados.
  O pytest terminou com exit code 0 e depois emitiu apenas o aviso conhecido de permissão ao
  limpar `pytest-current` no Windows.
- Nenhuma ordem, compra, conta Real, retry financeiro, manifesto ou alteração do EXE principal
  foi executada nesta coleta.
