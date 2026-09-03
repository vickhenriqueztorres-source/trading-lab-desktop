# AGENTS.md — Strategy Lab

Regras para qualquer agente de código (Codex, Claude, Cursor) que toque neste repositório.
Leia integralmente antes da primeira edição. Em conflito entre este arquivo e um prompt,
**este arquivo vence** — pare e reporte.

Documentos de referência: `strategy-lab/01-ARCHITECTURE.md`, `strategy-lab/03-PRD.md` (IDs `R-*`). Todo commit, teste
e entrada de WORKLOG referencia pelo menos um ID.

---

## 1. Antes de codificar

1. Leia `01-ARCHITECTURE.md`, `03-PRD.md` e este arquivo.
2. Confirme a estrutura real dos arquivos citados no prompt (`grep`, `ls`). Se divergir do
   descrito, **pare e reporte** antes de decidir sozinho.
3. Identifique os IDs `R-*` que a tarefa cobre e liste-os no início do seu plano.
4. Nunca instale dependência nova sem que o prompt a autorize explicitamente.

## 2. Invariantes absolutas (violar = tarefa reprovada)

| # | Invariante | Onde se aplica |
|---|---|---|
| I-1 | Sem `float` em dinheiro, probabilidade, payout ou parâmetro publicado. `Decimal` (prec 28) / `numeric`. No manifesto, números são **strings decimais**. | tudo |
| I-2 | Tempo: epoch inteiro UTC em dados e banco; `time.monotonic()` para prazos e timeouts no bot; `time.time()`/`datetime.now()` sem `tz=UTC` proibidos. | tudo |
| I-3 | Vela corrente nunca é gravada nem usada para decidir sobre si mesma (`ts < floor(now,60) − 60`). | collect, research |
| I-4 | Decisão em `t` só vê `[.., t]`. Qualquer acesso a `t+1` fora do cálculo de resultado é lookahead → bug. | research |
| I-5 | `vendor/iqoptionapi` só é importado em `tools/strategy_lab/collect/iq_client.py`. | tudo |
| I-6 | Escrita no banco só via UPSERT idempotente; rodar duas vezes produz o mesmo estado. | collect, hub |
| I-7 | Fail-closed: entrada inválida (vela, manifesto, assinatura, versão, schema) → descartar tudo e manter estado anterior. Nunca "aceitar o que der". | tudo |
| I-8 | Nenhum token, senha, `loginid`, saldo, service key ou chave privada em log, evento, UI, teste, fixture ou commit. | tudo |
| I-9 | Máximo 1 ordem em voo por conta; zero leitura de banco no ciclo de avaliação do bot. | bot |
| I-10 | Nenhuma lógica preditiva de preço/dígito (frequência, quente/frio, Markov, ML). Só primitivos da gramática. | research, bot |
| I-11 | Códigos de motivo, `strategy_key`, ids de família e nomes de colunas são contrato: só adicionar, nunca renomear. | tudo |
| I-12 | Assinaturas públicas preservadas; extensões keyword-only. | tudo |
| I-13 | Nenhum texto de UI promete lucro; toda taxa de acerto aparece com o mínimo (`p_min`) ao lado e com `n`. | bot/UI |
| I-14 | Strategy Lab e `trading-lab-desktop` têm processos, ambientes, estado e builds independentes. Sem import cruzado, IPC, banco ou arquivo privado compartilhado; a única integração operacional é o manifesto assinado/versionado. | tudo |

## 3. Convenções de código

### Ajuste P03 autorizado pelo operador em 2026-09-02

R-VEND-1 permite patches mínimos de segurança no snapshot, enumerados em PATCHES.md e
verificados por hashes upstream/patched. O lock fica em vendor/REQUIREMENTS.txt, fora do
snapshot, evitando a colisão Windows com requirements.txt upstream. O lint de importação
aplica I-5 ao código próprio do Lab; imports internos do terceiro preservado não são imports
cruzados entre produtos. Exemplos e rotas financeiras herdados não são executáveis pelo adaptador
de coleta: sua fronteira é somente leitura. Código upstream não alcançado permanece identificado
como terceiro; os caminhos executados de coleta exigem TLS validado, redação e deadlines
monotônicos. A fixture real é uma coleta manual pendente, nunca substituída por dados sintéticos.

- Python 3.12, `ruff` + `mypy --strict` conforme `pyproject.toml`. TypeScript estrito nas Edge
  Functions (Deno).
- pydantic v2 para toda fronteira de dados (API externa, banco, manifesto). `model_config =
  ConfigDict(strict=True, extra="forbid")`.
- Logs estruturados (JSON) com `event`, `run_id`, `asset`; sem PII.
- Funções puras onde possível; efeitos (rede, disco, banco) isolados em adaptadores com
  interface explícita para fakes.
- Nomes de arquivo em inglês; textos de UI em pt-BR.
- Sem `print` fora do CLI; sem `# type: ignore` sem justificativa em comentário.

## 4. Testes

- Fakes + clock injetável. Nenhum teste toca IQ Option, Supabase de produção ou rede pública.
- Banco: projeto Supabase **staging** (`SUPABASE_STAGING_URL`); teste que detectar URL de
  produção deve falhar imediatamente.
- Fixtures gravadas de `get_candles` em `tests/fixtures/iq/*.json` (dados públicos de preço;
  sem credenciais).
- Todo teste nomeia o `R-*` que cobre no docstring.
- Os 5 testes de CI são intocáveis sem aprovação humana: `test_coin_flip_approves_zero`,
  `test_primitives_parity_hash`, `test_canary_fixture_matches`,
  `test_hostile_manifests_rejected`, `test_dst_and_current_candle_never_written`.
- Cobertura mínima 90% em `primitives`, `manifest_schema`, `gates`, `manifest_client`.
- Teste de isolamento prova que o EXE principal não empacota `strategy-lab/` e que nenhum dos dois
  produtos importa módulos do outro.

## 5. Banco e migrations (Supabase)

- Toda mudança de schema é uma migration nova, aditiva, em `apps/hub/supabase/migrations/`.
  Nunca editar migration aplicada.
- `numeric` para valores; `bigint` para epoch; `check` constraints obrigatórios (ver
  Arquitetura §3).
- RLS ligado em todas as tabelas. Política nova exige teste que prova que anon **não** lê.
- `pg_cron` jobs idempotentes e com verificação de contagem antes de apagar.

## 6. Segurança

- Chaves privadas Ed25519 (A, B) e service key: fora do repo, arquivo 0600 ou keyring.
- Chave de **teste** é pública por design e vive em `tests/keys/`; o hub de produção e o
  build de produção do bot devem recusá-la (teste obrigatório).
- Credenciais IQ Option: keyring do SO (Windows) ou env (VPS). Nunca em `.env` versionado.
- Fixtures e logs passam por `scripts/scrub_secrets.py` no pre-commit.

## 7. Fluxo de trabalho

1. Um prompt = um PR. Não misturar módulos.
2. Antes do PR: `ruff`, `mypy`, `pytest` verdes; os 5 testes de CI passando.
3. `WORKLOG.md`: entrada com data, IDs `R-*` cobertos, decisões, o que ficou fora, e a
   tabela antes/depois quando houver mudança de comportamento numérico.
4. Se o prompt pedir algo que viole §2, **pare e reporte** com o número da invariante.
5. Se descobrir bug fora do escopo, registre em `WORKLOG.md` (seção "Encontrado, não
   corrigido") — não corrija sem pedido.
6. Comandos do Strategy Lab são executados no ambiente próprio do subprojeto; nunca reutilizar a
   `.venv`, o diretório de estado ou o pipeline de build do aplicativo principal.

## 8. Pare e reporte quando

- A estrutura real difere da descrita no prompt.
- Uma invariante do §2 conflita com o pedido.
- O `vendor/iqoptionapi` não expõe o método necessário.
- Um teste de CI intocável quebra.
- Precisa de dependência nova, credencial ou acesso que não tem.
- Cobertura de dados < 95% e o prompt pede `research`.

O relatório deve ter: o que foi pedido, o que foi encontrado, as opções, e a recomendação.
Não decida sozinho.
