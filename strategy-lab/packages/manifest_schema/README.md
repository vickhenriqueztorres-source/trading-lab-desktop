# tl-manifest-schema 1.1.0 — R-MAN-1..7

Contrato local do Strategy Lab. Não é importado pelo aplicativo principal e não envia ordens.
Pydantic strict + JSON Schema + Ed25519, com strings decimais preservadas byte a byte.
O runtime depende apenas de pydantic, cryptography e do tl-primitives **deste Lab**.
jsonschema é utilizado somente pelos testes.

## Revisão aditiva 1.1 (warmup)

`schema_version` continua `1`. Novas publicações declaram `schema_revision: "1.1"`
e `warmup_required` inteiro por estratégia. O builder calcula esse valor a partir
dos primitivos e parâmetros da família, sem importar o bot. O consumidor deve
recalcular e rejeitar a entrada divergente (`WARMUP_MISMATCH`).

Manifestos legados sem `schema_revision` continuam aceitos. Preserve
`exclude_unset=True` na serialização de objetos históricos assinados: acrescentar
defaults modifica os bytes e invalida a assinatura. Atualize os consumidores
antes de publicar a revisão 1.1. O exemplo histórico assinado da Arquitetura §6
e seus vetores públicos não foram reescritos.

## Instalação e validação (raiz strategy-lab)

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e packages/primitives -e packages/manifest_schema
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe tools/check_manifest_coverage.py
```

## APIs

- `Manifest`, `StrategyEntry`, `Validated`, `Management`: estrutura e semântica estritas.
- `canonical_bytes(unsigned_dict)`: bytes UTF-8, ordenados, sem signature na raiz.
- `sign(manifest, private_key_bytes, key_id)`: valida e retorna uma cópia assinada.
- `verify(manifest, public_keys)`: booleano fail-closed; chave de teste recusada por padrão.
- `evaluate(raw_bytes, public_keys, ...)`: fronteira pública recomendada; recusa floats,
  chaves JSON duplicadas, excesso de tamanho, schema/assinatura/versão/paridade inválidos.
  Retorna `(manifest, MANIFEST_ACCEPTED)` ou `(None, reason_code)`; não altera cache/estado.
- `python -m manifest_schema.export`: exporta o schema local.
- `python tools/generate_manifest_contract.py`: regenera apenas artefatos PÚBLICOS de teste.

`allow_test_keys=True` é exclusivamente para testes; nunca habilitar no publish/bot de produção.
A assinatura autentica key_id e todos os campos presentes. Omitido ≠ null; "2" ≠ "2.0".
Sem normalização ou aplicação implícita de defaults ao conteúdo assinado.
Instâncias mutadas via dicionários/listas internos são revalidadas ao assinar/verificar.

## Contrato público

- `schema/manifest.v1.schema.json`
- `tests/fixtures/manifest_example.json` (mesmo exemplo assinado da Arquitetura §6)
- `contracts/manifest_acceptance_vectors.json`
- `contracts/README.md` (perfil semântico obrigatório)
- `tests/keys/` (somente chave pública de teste por design)

O JSON Schema padrão sozinho NÃO executa comparações entre campos ou aritmética de strings.
O perfil semântico obrigatório e a verificação Ed25519 são camadas adicionais, não opcionais.
O adaptador Python independente existe nos testes; Deno e o bot precisam de suas implementações
e da execução destes mesmos vetores nas etapas futuras. Não há import de código do Lab pelo bot.

Não foram modificados primitivos ou estratégias, nem configurados Supabase, RLS, storage, funções
Edge, credenciais ou execução financeira. O contrato não prova a veracidade de métricas assinadas;
essa responsabilidade pertence à pesquisa/publicação. Expiração no relógio atual e antirrollback
de cache são responsabilidades do consumidor P09.
