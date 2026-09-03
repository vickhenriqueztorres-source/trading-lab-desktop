# P09 Validation — `manifest_client` Fail-Closed & Compliance Contract

Data/hora local: 2026-09-02 22:25 BRT.
Baseline: v1.9.11.

## Escopo

Implementação e validação formal do consumidor de manifestos assinado e fail-closed no repositório principal `trading-lab-desktop`, cumprindo os requisitos **R-ISO-2..6** e **R-BOT-1..4**, mantendo isolamento total em relação ao Strategy Lab.

Arquivos implementados:
- `apps/core/manifest_keys.py`: trust store segregando build de produção e teste.
- `apps/core/manifest_client.py`: motor de validação estrito, canonical JSON, verificação Ed25519, cache atômico e cliente HTTP fail-closed.
- `tests/unit/test_manifest_keys.py`: inspeção do módulo em produção.
- `tests/contract/test_manifest_acceptance_vectors.py`: suite contratual com 60 casos de teste e vetor de paridade.
- `tests/unit/test_manifest_client_hostile.py`: suite de cenários hostis (CI intocável).
- `tests/unit/test_no_network_in_evaluation_cycle.py`: prova de ausência de rede durante o ciclo de avaliação.
- `tests/security/test_strategy_lab_isolation.py`: inspeção AST e de build garantindo ausência do Strategy Lab.

---

## Requisitos Cobertos

| Requisito | Evidência |
| --- | --- |
| **R-BOT-1** | `ManifestClient` consome e valida manifestos Ed25519 (chaves A e B). Fail-closed em qualquer divergência de schema, assinatura, chave não confiável, versão ou integridade de parâmetros. |
| **R-BOT-2** | Ciclo de avaliação não realiza I/O de rede ou disco. `current()` lê diretamente de memória atômica. Polling desacoplado a cada 900 s. |
| **R-BOT-3** | Cache atômico em disco (`manifest.json.tmp` $\to$ `flush` $\to$ `fsync` $\to$ `os.replace`). Validação de integridade ao carregar cache local. Descarte automático de arquivo adulterado. |
| **R-BOT-4** | Expiração validada contra cabeçalho `Date` do CDN (ou tolerância offline de 24h a partir do relógio local). Emissão de eventos `manifest_applied`, `manifest_rejected` e `manifest_expired`. |
| **R-ISO-2..6** | Isolamento arquitetural rigoroso: bot não importa código do laboratório, dependências exclusivas não constam em `pyproject.toml`, e varredura AST confirma zero referências. |

---

## Resultados dos Testes

### 1. Testes de Unidade, Contrato e Segurança P09

```powershell
python -m pytest tests/unit/test_manifest_keys.py tests/contract/test_manifest_acceptance_vectors.py tests/unit/test_manifest_client_hostile.py tests/unit/test_no_network_in_evaluation_cycle.py tests/security/test_strategy_lab_isolation.py -v
```

Resultado:
```text
68 passed in 3.76s
```

Detalhes:
- `test_manifest_keys.py`: 2 passed (ausência de `TEST_KEY` em build de produção comprovada).
- `test_manifest_acceptance_vectors.py`: 61 passed (60/60 casos do contrato público com 100% de correspondência nos reason codes + validação de paridade SHA-256).
- `test_hostile_manifests_rejected`: 11 cenários hostis aprovados, com preservação inalterada do manifesto anterior em todos os casos de falha.
- `test_no_network_in_evaluation_cycle`: 10.000 avaliações sem tocar na rede.
- `test_strategy_lab_isolation.py`: varredura AST e verificação de dependências limpas.

### 2. Suíte de Regressão Parcial de Unidade e Segurança

```powershell
python -m pytest tests/unit tests/contract tests/security -q
```

Resultado:
```text
769 passed, 2 skipped in 86.54s
```

### 3. Qualidade de Código e Tipagem

```powershell
python -m ruff check apps packages tests
python -m ruff format --check apps packages tests
python -m mypy
python -m compileall apps packages
```

Resultado:
- `ruff check`: 0 erros.
- `ruff format --check`: 436 arquivos formatados.
- `mypy`: 266 arquivos fonte verificados sem erros.
- `compileall`: compilação concluída com sucesso.

---

## Critérios de Aceite

- [x] `test_hostile_manifests_rejected` verde ($\ge 10$ cenários testados: 11 cenários cobertos).
- [x] Build prod: `TEST_KEY` ausente (teste inspeciona módulo e dicionário).
- [x] Nenhum acesso a rede/disco no ciclo de avaliação (`test_no_network_in_evaluation_cycle` verde).
- [x] Vetores de conformidade aceitos/rejeitados com os mesmos reason codes do contrato (60/60 casos idênticos).
- [x] Build principal comprovadamente não contém o Strategy Lab (AST scan e dependências de build verificados).
