# Relatório de Validação — Tarefa P10: `strategy-lab publish`

**Data:** 2026-09-02  
**Baseline:** v1.9.11  
**Requisitos atendidos:** R-PUB-1..5, R-RES-9, R-RES-11, R-ISO-2..3

---

## 1. Resumo Executivo

A Tarefa P10 implementou o pipeline completo de ponta a ponta para curadoria, montagem, auditoria estrita (preflight), diff legível, assinatura criptográfica Ed25519 e upload do manifesto de estratégias no Hub (Supabase Edge Functions / Storage), além dos motores de ranking financeiro (`scorer`), exportação de relatórios (`ranking.md` e `candidates.json`) e o pacote de referência `sprt` (Wald's Sequential Probability Ratio Test).

Todo o preflight foi implementado com isolamento hermético (R-ISO-2..3), sem importar qualquer módulo ou código do projeto principal `trading-lab-desktop` (`apps/core/manifest_client.py`), alcançando 100% de paridade com os 60 vetores públicos de conformidade (`contracts/manifest_acceptance_vectors.json`).

---

## 2. Componentes Implementados

### 2.1 Métrica Financeira e Scoring (`research/scorer.py` - R-RES-9)
- **`margin`**: Diferença exata `wilson_lower - p_min` em precisão Decimal 28.
- **`score`**: `margin * sqrt(ops_per_day)`.
- **`worst_streak`**: Sequência contínua máxima de derrotas (loss streak).
- **`result_1000_ops_stake10`**: Resultado projetado `1000 * (p̂ * payout_med - (1 - p̂)) * 10`.
- **`payout_min`**: Menor payout na grade 0,70..0,95 (passo 0,01) que satisfaz `wilson_lower >= 1 / (1 + payout) + 0.015`.

### 2.2 Relatórios de Pesquisa e Candidatos (`research/report.py` - R-RES-11)
- Gera `ranking.md`: Tabela Markdown ordenada por score decrescente contendo os 5 números fundamentais ($\hat{p}$, $IC_{95\%}$, $p_{min}$, $payout_{min}$, operações/dia) e veredito detalhado por portão estatístico.
- Gera `candidates.json`: Estrutura canônica de candidatos prontos para consumo pelo builder do manifesto.
- Função `run_synthetic_research`: Execução sintética para testes e auditorias automatizadas.

### 2.3 Implementação de Referência SPRT (`packages/sprt/` - R-PUB-5, R-BOT-7)
- Pacote isolado `tl-sprt` com testes estatísticos sequenciais de Wald (`WaldSprt`).
- Decisões explícitas: `CONTINUE`, `ACCEPT_H0` (edge mantido) e `REJECT_H0` (edge perdido).
- Barreira absorvente com memória de rejeição (`_ever_rejected`).
- Critério de promoção (R-PUB-5): `--promote KEY` só permite promoção de `observation` para `approved` se houver comprovação em `live_outcomes` de $\ge 200$ operações ou $\ge 30$ dias de histórico sem nenhuma rejeição pelo SPRT.

### 2.4 Montagem do Manifesto (`publish/builder.py` - R-PUB-1, R-PUB-5)
- Lê `candidates.json` por `--run-id` ou arquivo direto.
- Aplica filtros de curadoria `--include` e `--exclude`.
- **Invariante R-PUB-5**: Toda estratégia nova nasce obrigatoriamente com `status="observation"`.
- Estratégias já em `approved` no manifesto vigente permanecem `approved` apenas se continuarem aprovadas na rodada.
- Validação estrita de tipos, ranges e coerência de parâmetros contra `FAMILY_SPECS`.

### 2.5 Preflight Hermético e Vetores de Conformidade (`publish/preflight.py` - R-PUB-2, R-ISO-2..3)
- Auditoria estrita antes de qualquer envio de rede:
  - Validação de schema e integridade do envelope JSON.
  - Verificação de assinatura digital Ed25519 contra chaves públicas confiáveis.
  - Verificação do hash público de paridade de primitivas (`DEFAULT_PARITY_SHA256`).
  - Proibição de manifesto vazio (`MANIFEST_EMPTY_STRATEGIES`).
- Verificação local integral dos 60 casos de teste de `contracts/manifest_acceptance_vectors.json`.
- Zero imports do bot (`apps/core/manifest_client.py` ou módulos do EXE principal), verificado por AST scan.

### 2.6 Diff Legível e Confirmação Proibindo `--yes` (`publish/differ.py` - R-PUB-3)
- Calcula detalhadamente adições, remoções, modificações de parâmetros/status e estratégias inalteradas.
- Apresenta relatório em formato tabular e textual legível.
- **Invariante R-PUB-3**: A flag `--yes` é expressamente proibida. A publicação exige que o operador humano digite interativamente o número exato de estratégias contidas no manifesto a ser publicado.

### 2.7 Assinador e Permissões Seguras (`publish/signer.py` - R-PUB-4)
- Carrega a chave privada Ed25519 de `~/.strategy-lab/keys/{A,B}.pem`.
- Em sistemas POSIX, valida estritamente modo `0600`, recusando `0644` ou qualquer bit de leitura por grupo/outros (`InsecureKeyFileError`).
- Serializa em JSON canônico RFC 8785 e anexa a assinatura detached no envelope.

### 2.8 Cliente de Publicação (`publish/uploader.py`)
- Executa POST HTTP seguro para a Edge Function `publish` do Supabase.
- Tratamento semântico de status codes:
  - `201 Created`: Sucesso com retorno do SHA-256 publicado.
  - `401 Unauthorized`: Assinatura inválida ou chave não autorizada (`MANIFEST_SIGNATURE_INVALID`).
  - `409 Conflict`: Versão do manifesto não é estritamente maior que a versão ativa (`MANIFEST_VERSION_NOT_NEWER`).
  - `422 Unprocessable Entity`: Falha semântica de schema ou integridade.

---

## 3. Matriz de Testes e Validações

Executado no ambiente isolado do Strategy Lab (`strategy-lab/.venv`):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_publish_p10.py -v
```

### Resultados dos 13 Testes da Tarefa P10:
1. `test_scorer_metrics`: Validação das métricas financeiras, pior streak, resultado 1000 ops e payout mínimo. **PASSOU**
2. `test_synthetic_research_generates_ranking_and_candidates`: Geração de `ranking.md` e `candidates.json`. **PASSOU**
3. `test_wald_sprt_hypothesis_and_decisions`: Teste das decisões e log-likelihood ratios do Wald SPRT. **PASSOU**
4. `test_sprt_promotion_eligibility`: Regras de promoção para $\ge 200$ ops e $\ge 30$ dias sem rejeição. **PASSOU**
5. `test_builder_observation_and_promotion`: Invariante R-PUB-5 (nascimento em observation e promoção com SPRT). **PASSOU**
6. `test_preflight_verifies_all_contract_vectors_locally`: Execução dos 60 vetores públicos com 100% de paridade. **PASSOU**
7. `test_preflight_validates_assembled_manifest`: Preflight aprova manifestos íntegros e rejeita divergências de paridade. **PASSOU**
8. `test_preflight_strictly_prohibits_bot_imports`: AST scan auditando ausência total de imports do bot no Lab. **PASSOU**
9. `test_differ_and_confirmation_prompt`: Cálculo do diff e confirmação exigindo número exato de estratégias. **PASSOU**
10. `test_signer_checks_permissions_and_signs`: Recusa de permissão insegura 0644 e assinatura canônica. **PASSOU**
11. `test_uploader_status_handling`: Tratamento robusto de códigos 201, 401, 409 e 422 da Edge Function. **PASSOU**
12. `test_cli_publish_prohibits_yes_flag`: Proibição estrita da flag `--yes` na linha de comando. **PASSOU**
13. `test_cli_publish_dry_run_success`: Execução completa de ponta a ponta da CLI em modo `--dry-run`. **PASSOU**

### Validação da Suíte Completa do Strategy Lab:
- **Total coletado:** 292 testes
- **Total aprovado:** 289 testes
- **Ignorados (staging remoto dependente de URL de ambiente):** 3 testes
- **Tempo:** 67.00s

### Checagens Estáticas:
- `python -m ruff check packages tools tests`: 0 erros
- `python -m ruff format --check packages tools tests`: 106 arquivos conformes
- `python -m mypy`: 75 arquivos verificados em modo estrito, 0 erros
- `python -m compileall packages tools tests`: 100% compilado com sucesso
