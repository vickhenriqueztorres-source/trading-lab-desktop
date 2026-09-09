# CAT-05 — Evidência durável e holdout protegido no Hub

Data: 2026-09-05.

Escopo executado: migrations locais do Hub, contratos de evidência do Strategy Lab, repositório
Postgres/Fake, integração fail-closed no `run_research_pipeline` e testes locais. Nenhum Supabase
remoto/staging, broker, ordem, publicação ou build foi executado.

## Resultado

CAT-05 foi implementado localmente.

O pipeline de pesquisa agora impede aprovação de dataset elegível para produção quando a camada de
evidência durável não está disponível. Para runs com evidência habilitada, o Lab registra:

- snapshot do dataset;
- reserva persistente de holdout;
- bloqueio de holdout já queimado;
- tentativa por candidato;
- contagens do replay;
- artefato privado hash-addressed para `TradeLog`;
- transição de holdout para `opened`, `burned` ou `rolled_back`.

## Migration nova

Criada somente localmente:

`strategy-lab/apps/hub/supabase/migrations/0008_research_evidence.sql`

Tabelas adicionadas:

- `dataset_snapshots`;
- `holdout_reservations`;
- `research_attempts`;
- `research_evidence_artifacts`.

RLS foi habilitado nas quatro tabelas e acesso `anon`/`authenticated` foi revogado. Cliente não lê
datasets, holdouts, tentativas nem artefatos de evidência.

## Código implementado

- `strategy_lab.research.evidence`
  - contratos tipados de evidência;
  - `FakeEvidenceRepository`;
  - `MemoryArtifactStore`;
  - serialização canônica do `TradeLog` sem duplicar JSON de candles;
  - proteção de concorrência para reserva de holdout.
- `strategy_lab.research.pg_evidence_repository`
  - implementação Postgres para snapshot, reserva, abertura, queima, rollback, tentativas e
    artefatos.
- `strategy_lab.research.runner`
  - `production_eligible` exige evidência durável, salvo override explícito de teste;
  - consulta persistente de holdout queimado;
  - reserva antes da avaliação;
  - rollback quando não há pré-aprovados;
  - burn quando holdout é consumido.
- `strategy_lab.research.holdout`
  - removeu `except/pass` em gravação de banco;
  - carrega ranges queimados do banco no startup quando conexão é fornecida;
  - falha de banco agora aborta em vez de parecer sucesso.

## Testes novos

Arquivo:

`strategy-lab/tests/test_cat05_evidence_contract.py`

Cobertura:

- dataset de produção sem evidência aborta com `RES_DURABLE_EVIDENCE_REQUIRED`;
- pipeline registra snapshot, tentativa, artefato e queima de holdout;
- holdout queimado em run anterior bloqueia run posterior;
- run sem candidato pré-aprovado faz rollback da reserva;
- duas reservas concorrentes do mesmo holdout não passam juntas;
- falha de leitura/escrita de evidência aborta;
- `HoldoutManager` legado não engole erro de banco.

## Validação executada

Strategy Lab:

- `pytest -q`
  - Resultado: 345 passed, 3 skipped.
  - Skips: staging sem `SUPABASE_STAGING_DB_URL`.
- `ruff check .`
  - Resultado: aprovado.
- `ruff format --check .`
  - Resultado: aprovado.
- `mypy`
  - Resultado: aprovado, 84 source files.
- `compileall packages tools tests`
  - Resultado: aprovado.
- `git diff --check`
  - Resultado: aprovado.

Scanner de segredos no repositório raiz:

- `scripts/scrub_secrets.py --all`
  - Resultado: nenhum segredo detectado.

Observação Windows: o `pytest` continua emitindo aviso pós-exit-code-0 de limpeza do diretório
temporário `pytest-current` por `PermissionError`; não alterou o status da suíte.

## Limitações externas

- Migration `0008_research_evidence.sql` não foi aplicada em Supabase remoto/staging.
- Testes staging continuam pulados por ausência de `SUPABASE_STAGING_DB_URL`.
- Constraints/RLS foram implementadas em SQL e testadas indiretamente/localmente, mas não
  demonstradas contra um projeto Supabase real nesta etapa.
- Storage privado real para artefatos ainda não foi exercitado; o teste usa `MemoryArtifactStore`.

## Próximo passo

CAT-06: aprovação estatística sem atalhos ou relatórios fabricados.
