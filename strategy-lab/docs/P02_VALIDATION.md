# P02 — validação local do contrato de manifesto

Data: 2026-09-02. Requisitos: R-MAN-1..7; R-ISO-1..6 preservados.
Ambiente: Python 3.12.13 do Strategy Lab, não o ambiente do EXE.

## Diff funcional

- Novo tl-manifest-schema 1.0.0: models, families, rules, canonical, signing, acceptance, export.
- JSON Schema público, exemplo assinado e corpus de aceitação com hashes próprios.
- Chave de teste intencionalmente pública exclusivamente em tests/keys.
- Instalação independente, dependências autorizadas fixadas no requirements.lock.
- Arquitetura/PRD formalizados com F1–F5, strings decimais e perfil semântico obrigatório.
- Nenhum arquivo de primitives foi modificado; VERSION/hash do P01 preservados.
- Nenhuma estratégia executável, conector, banco, EXE ou projeto remoto foi alterado.

## Provas de aceite

| Critério | Evidência local |
|---|---|
| Manifest, StrategyEntry, Validated, Management | Modelos strict/extra=forbid e testes de estrutura |
| Ranges e grades por família | FAMILY_BINDINGS deriva registry; gates próprios declarados; todos os parâmetros testados abaixo/acima dos limites |
| Exemplo da Arquitetura §6 | Igualdade exata com tests/fixtures/manifest_example.json; assinatura A/B testada |
| Export sincronizado | test_export_is_synchronized compara bytes com export; CLI testado |
| Casos hostis | 51/51 vetores rejeitados com reason code exato; 9/9 válidos aceitos |
| Assinatura | 64 alterações individuais de byte rejeitadas, ID adulterado, trust store ausente, encoding inválido, chave de teste recusada por padrão |
| JSON Schema/Pydantic | Mesmos resultados sobre vetores e mutações estruturais; comparador independente jsonschema + perfil |
| Leitura segura | Floats, duplicatas, JSON truncado, tamanho, profundidade e Unicode inválido recusados |
| Paridade P01 | Suíte de 10k continua passando sem alterar indicador ou hash |
| Tipagem e lint | Ruff, format, mypy strict (30 arquivos) aprovados |
| Suíte inteira | 150 passed (33 P01 + 117 P02), zero falhas |
| Cobertura P02 | 457/470 linhas executáveis pela análise stdlib trace = 97,23%; sem dependência nova de cobertura |
| Build | wheel isolado validado, 13 entradas: código/py.typed/metadata; sem fixtures/chaves privadas |
| Credenciais | Scan local dos artefatos de código/JSON: zero JWT, chave privada PEM ou sb_secret |
| Dependências | pip check sem inconsistências; cryptography runtime e jsonschema só em testes |

## Hashes

- Vetores (hash canônico do envelope sem próprio sha256):
  `30f8c479534392fc10bbbe82cfbbf1510b02df2ad21cfcc223f8be535c785e3b`.
- Arquivo schema/manifest.v1.schema.json:
  `55969f99be2f08913d0c0f91d89efedcfa12c2d1bf100a2a22cbc786af9dc270`.
- Wheel dist/p02/tl_manifest_schema-1.0.0-py3-none-any.whl:
  `3c61e35cd3bbe97e8211ff7f410edfda0c9e98a0f9ddbaa64d6f7a677243d4ba`.
- Hash numérico P01:
  `f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10`.

## Comandos

Na raiz strategy-lab, todos usando .venv/Scripts/python.exe:

- -m pytest -q
- -m ruff check .
- -m ruff format --check .
- -m mypy
- -m compileall -q packages
- -m pip check
- tools/check_manifest_coverage.py
- tools/generate_manifest_contract.py
- -m pip wheel --no-deps packages/manifest_schema --wheel-dir dist/p02

## Limitações explícitas

O JSON Schema padrão sozinho NÃO é equivalente à validação completa. O perfil
manifest-policy-v1 é obrigatório para comparações entre campos, limites/grades decimais e
consistência de payout; a assinatura é uma terceira camada. Isso é demonstrado em teste,
não ocultado com um schema permissivo. contracts/README.md define o contrato portável.

Não foi implementado/testado o adaptador Deno nem o manifest_client do bot nesta fase.
Eles devem rejeitar a ausência do perfil e passar os vetores públicos antes de qualquer deploy.
Nenhuma credencial fornecida foi usada ou salva. Não houve migration, login Supabase,
deploy Edge, acesso IQ Option, ordem Demo/Real, alteração no EXE ou publicação remota.

O corpus hostil é finito: 100% de rejeição dos casos testados, não garantia contra toda entrada
possível. Nenhuma chave pública de teste é segura para produção. As métricas do exemplo são
ilustrativas, não resultados de pesquisa nem promessa de lucro.

Os artefatos estão no working tree. Este trabalho não cria commit nem faz push.
