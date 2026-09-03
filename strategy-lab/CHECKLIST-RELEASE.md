# Checklist de Release e Bump de Primitivos (R-BOT-4, R-ISO-2)

**Baseline:** v1.9.11  
**Atualizado em:** 2026-09-03  
**Aplica-se a:** Mudanças de indicadores, famílias de estratégias ou fórmulas de cálculo

---

## 1. Princípio de Isolamento e Paridade

O Strategy Lab e o Bot Desktop mantêm implementações 100% isoladas de código:
- O Strategy Lab realiza a pesquisa quantitativa e publica manifestos assinados no Hub.
- O Bot Desktop executa sua própria implementação local em `apps/core/families/` para emitir ordens.
- A equivalência matemática absoluta entre os dois ecossistemas é garantida pelo hash `primitives_parity_sha256`, calculado deterministicamente sobre o vetor canônico público de 10.000 velas (`contracts/fixtures/parity_candles_10000.json`).

Toda alteração de indicadores exige um **bump de versão de primitivos** (`primitives_version`) e sincronização estrita de releases.

---

## 2. Passo a Passo Obrigatório para Bump de Primitivos

### Passo 1: Implementação e Novo Hash no Strategy Lab
1. Modifique ou adicione o indicador em `packages/primitives/primitives/`.
2. Garanta a invariante **I-1**: toda a matemática financeira e estatística deve usar exclusivamente `Decimal` com precisão de 28 dígitos; uso de `float` é estritamente proibido (`test_no_float_in_primitives.py`).
3. Execute o teste de paridade para gerar e conferir o novo hash SHA-256:
   ```powershell
   cd strategy-lab
   .\.venv\Scripts\python.exe -m pytest packages/primitives/tests/parity/test_primitives_parity_hash.py -v
   ```
4. Atualize a constante `PRIMITIVES_PARITY_SHA256` em `packages/primitives/primitives/parity.py` e `packages/manifest_schema/manifest_schema/schema.py`.
5. Incremente a versão em `packages/primitives/pyproject.toml` (e.g. `1.0.0` $\to$ `1.1.0`).

### Passo 2: Implementação Local no Bot Desktop
1. No repositório principal `trading-lab-desktop`, atualize a implementação local correspondente em `apps/core/families/` ou `packages/strategies/`.
2. **Nunca importe código de `strategy-lab/`** (invariante R-ISO-2/I-14; o teste de segurança em AST bloqueia qualquer import cruzado).
3. Atualize a constante `EXPECTED_PRIMITIVES_VERSION` e `EXPECTED_PARITY_SHA256` no bot.
4. Execute os testes locais de paridade do bot:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/unit/test_families_parity.py -v
   ```
5. Confirme que o hash gerado pelo bot coincide exatamente, caractere por caractere, com o hash do Strategy Lab.

### Passo 3: Build e Release do Aplicativo Desktop
1. Realize o build do novo executável do aplicativo Desktop:
   ```powershell
   python -m PyInstaller build_scripts/TradingLab.spec
   ```
2. Execute a validação de segurança pós-build:
   ```powershell
   python -m pytest tests/security/test_strategy_lab_isolation.py -v
   ```
3. Publique e distribua a nova versão do executável (e.g. `TradingLab-v1.9.12.exe`) para a base de usuários.

### Passo 4: Período de Transição e Tolerância de Versões (Buffer)
> [!IMPORTANT]
> **Nunca publique um novo manifesto com `primitives_version` bumpada imediatamente após soltar o build do bot!**

#### Comportamento dos Bots Antigos:
- Quando um bot antigo (rodando `primitives_version: 1.0.0`) recebe um manifesto novo com `primitives_version: 1.1.0` ou hash de paridade diferente:
  - O `manifest_client` rejeita o novo manifesto fail-closed com `PRIMITIVES_VERSION_UNSUPPORTED` ou `PARITY_HASH_MISMATCH`.
  - **Segurança Operacional**: O bot antigo **não quebra** e **não encerra ordens abertas**.
  - O bot mantém o último manifesto válido em cache local (válido por até 45 dias) e continua operando as estratégias anteriores com total segurança.
  - A interface exibe um banner orientando o cliente a atualizar o aplicativo para desbloquear o novo catálogo.

#### Janela de Tolerância Recomendada:
- **Janela de Tolerância Mínima**: **7 dias** (para 80% da base atualizar).
- **Janela de Tolerância Recomendada**: **14 dias**.
- Durante esse período, continue publicando revisões ou correções na versão anterior dos primitivos, se necessário.

### Passo 5: Publicação do Novo Manifesto no Hub
Após o encerramento da janela de transição:
1. Execute a pesquisa quantitativa com a nova versão:
   ```powershell
   strategy-lab research --seed <seed>
   ```
2. O manifesto gerado em `candidates.json` conterá a nova `primitives_version` e o novo `primitives_parity_sha256`.
3. Execute o `strategy-lab publish` com a Chave A:
   ```powershell
   strategy-lab publish --run-id <run_id> --key-id A
   ```
4. Verifique o recebimento e ativação nos bots atualizados.

---

## 3. Checklist Pré-Voo de Publicação (Tick List)

Antes de rodar qualquer `strategy-lab publish` com nova versão de primitivos, certifique-se:

- [ ] **1. Teste da Moeda**: `test_coin_flip_approves_zero` passou com 0 aprovações em random walk.
- [ ] **2. Paridade**: `test_primitives_parity_hash` aprovado no Lab e no Bot com hashes idênticos.
- [ ] **3. Canário**: `test_canary_fixture_matches` passou e coleta do dia foi concluída sem alertas.
- [ ] **4. Manifestos Hostis**: `test_hostile_manifests_rejected` passou com rejeição de todos os 60 vetores hostis.
- [ ] **5. Invariante Temporal**: `test_dst_and_current_candle_never_written` aprovado.
- [ ] **6. Isolamento e Build**: `test_strategy_lab_isolation.py` aprovou 4/4 verificações sem vazamento.
- [ ] **7. Varredura de Segredos**: `python scripts/scrub_secrets.py --all` retornou código 0.
- [ ] **8. Preflight do Manifesto**: `strategy-lab publish --dry-run` executou todos os 60 vetores sem erro.
- [ ] **9. Confirmação Manual**: Diff inspecionado e contagem de estratégias digitada manualmente.
