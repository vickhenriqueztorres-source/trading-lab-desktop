# CAT-00 — Baseline e auditoria do catálogo

**Data:** 2026-09-05 BRT
**Commit inspecionado:** `18d10bbf43615644a4ed57c50f9811403002135f`
**Branch:** `feat/enterprise-foundation`
**Classificação:** diagnóstico/documentação; nenhuma alteração operacional

## 1. Escopo e estado inicial

O CAT-00 foi executado conforme o plano de catálogo incremental. Antes desta fase, o working tree
já continha duas mudanças entendidas e preservadas:

- `docs/PLANO_CATALOGO_INCREMENTAL_SUPABASE_PROMPTS.md` — plano solicitado pelo operador;
- `WORKLOG.md` — entrada append-only da elaboração do plano.

Nenhum arquivo operacional estava modificado. A fase não abriu UI, corretora, conta, ordem,
Supabase remoto, build ou instalador.

Versões verificadas:

| Item | Estado observado |
|---|---|
| Aplicativo/pipeline Windows | v1.9.11 |
| Spec autoritativo | `build_scripts/TradingLab.spec`, onedir, `console=False` |
| Executável | `TradingLab.exe` |
| Compilador | `build_scripts/compile_trading_lab.py`, default 1.9.11 |
| Installer | `TradingLab_Setup.iss`, v1.9.11 |
| BOT `pyproject.toml` | requer Python >=3.13; mypy/ruff miram 3.13 |
| Ambiente BOT disponível | Python 3.12.14 |
| LAB `pyproject.toml` | requer Python >=3.12,<3.13 |
| Ambiente LAB disponível | Python 3.12.14, compatível |
| LAB | v0.1.0, `requirements.lock` próprio |
| Hub | `deno.lock` próprio; executável Deno não disponível neste host |

**Divergência:** a validação do BOT foi executada com Python 3.12.14, embora seu contrato de
projeto exija 3.13. Isso impede chamar a execução de certificação do ambiente canônico 3.13.
Não foi alterado `requires-python` nem instalado runtime durante esta fase.

## 2. Validação executada

Comandos reproduzíveis usados, cada produto em seu próprio diretório/ambiente:

```powershell
# BOT
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest tests\contract\test_deriv_worker_contract.py -q
.\.venv\Scripts\python.exe -m ruff check apps packages tests
.\.venv\Scripts\python.exe -m ruff format --check apps packages tests
.\.venv\Scripts\python.exe -m mypy apps packages
.\.venv\Scripts\python.exe -m compileall apps packages
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\scrub_secrets.py --all
git diff --check

# LAB, após Set-Location strategy-lab
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy --strict
.\.venv\Scripts\python.exe -m pip check
```

### Trading Lab Desktop

| Verificação | Resultado |
|---|---|
| `python -m pytest` — primeira execução concorrente com LAB | 1.222 passed, 5 failed, 6 errors, 4 skipped; FAIL |
| Worker Deriv focado depois da pressão concorrente | 14 passed; PASS |
| Três falhas funcionais repetidas isoladamente | 3/3 reproduzidas; FAIL |
| `python -m pytest` — segunda execução integral isolada | 1.230 passed, 3 failed, 4 skipped; FAIL |
| `ruff check apps packages tests` | 24 diagnósticos; FAIL |
| `ruff format --check apps packages tests` | 7 arquivos; FAIL |
| `ruff check .` | inválido como gate atual: inclui subprojeto/vendor e retornou 13.610 diagnósticos |
| `ruff format --check .` | FAIL; inclui arquivo `.py` documental não parseável e escopo do subprojeto |
| `mypy apps packages` | 304 arquivos, zero issues; PASS |
| `compileall apps packages` | PASS |
| `pip check` | PASS |
| `scripts/scrub_secrets.py --all` | zero segredo detectado; PASS |
| `git diff --check` | PASS no momento da verificação |

Os erros de handshake da primeira suíte não se reproduziram quando
`tests/contract/test_deriv_worker_contract.py` foi executado sozinho: 14/14 passaram. Eles ficam
classificados como interferência/pressão de processos na execução concorrente, não como prova de
correção. A suíte integral isolada terminou em 7m58s e é a evidência final desta fase.

Falhas funcionais determinísticas reproduzidas isoladamente:

1. `test_iqoption_auto_trader_persists_before_worker_submission`: zero ordem recebida pelo fake
   quando o teste espera uma; o contrato E2E e o fluxo atual de candidatura/admissão divergem.
2. `test_lifecycle_rejects_unsigned_cache`: um cache local sem assinatura é rejeitado, mas o
   lifecycle continua procurando e carrega o manifesto assinado empacotado; o teste espera
   catálogo vazio. É preciso decidir se o fallback assinado é permitido após cache hostil.
3. `test_ui_card_toggle_updates_controller_for_iqoption_and_deriv`: o painel contém entradas F1
   IQ, mas não contém `tail-probability-edge`; implementação/UI e contrato de catálogo conjunto
   divergem.

Qualidade local pendente: 24 diagnósticos Ruff incluem linhas longas/SIM em launcher e painel,
imports não usados/ordenação em testes e estilo. Sete arquivos precisam de formatação. Não foram
corrigidos porque CAT-00 é diagnóstico e as regras do Lab mandam registrar bugs fora do escopo.

### Strategy Lab

| Verificação | Resultado |
|---|---|
| `python -m pytest` | 312 passed, 3 skipped; PASS local |
| Testes `@staging` | 3 NOT EXECUTED/BLOCKED: `SUPABASE_STAGING_DB_URL` ausente |
| `ruff check .` | PASS |
| `ruff format --check .` | 1 arquivo não formatado; FAIL |
| `mypy --strict` | 79 arquivos, zero issues; PASS |
| `pip check` | PASS |
| Deno check/test/fmt | NOT EXECUTED/BLOCKED: Deno não instalado/disponível |
| Supabase staging/RLS | NOT EXECUTED/BLOCKED: URL de staging ausente |
| Scanner de segredo | coberto pelo scanner raiz `--all`; PASS |

Após o pytest do Lab, o callback de limpeza do diretório temporário registrou `WinError 5` em
`pytest-current`; o processo retornou código 0 e 312 testes passaram. O aviso deve ser tratado
como limitação do ambiente Windows, sem reclassificar testes de staging como executados.

## 3. Inventário e proveniência do manifesto atual

Arquivos `data/manifest.json` e `cache/manifest.json` são byte a byte iguais:

- SHA-256: `B77D587A1650DA1A33E6DAE528663EE609E1647B3D743771F7B53FC3EF81EEED`;
- schema 1, manifest_version 1, primitives 1.0.0;
- `research_run_id=run_complete_catalog`;
- 16 entradas, 16 ativos, somente F1/M1;
- 16 `approved`;
- um único conjunto de parâmetros e um único bloco de estatísticas replicados;
- assinatura aceita por `evaluate_manifest_bytes` com o trust store de produção e hash de
  paridade esperado.

Classificação por dimensão:

| Dimensão | Veredito | Evidência |
|---|---|---|
| Integridade criptográfica do arquivo | VERIFIED | assinatura, schema, versão e paridade aceitos localmente |
| Diversidade de estratégia | INCONSISTENT com “16 estratégias” | são 16 ativos da mesma família/params/evidência |
| Run de pesquisa | UNVERIFIED | não foi localizado artefato local com `run_complete_catalog` |
| `p_hat` e `n` | DECLARED, origem não localizada | 0.578 e 1.000 repetidos em todas as entradas |
| Wilson declarado | INCONSISTENT | manifesto 0.557; função atual do Lab para 578/1000 = 0.5471483340786791358059675999 |
| `windows_passed` | UNVERIFIED | gerador atual escreve literal `8/8` |
| `holdout_passed` | UNVERIFIED | gerador atual deriva o campo de `app.approved` |
| Resultado 1.000 ops | MODELLED, não realizado | fórmula com payout hipotético, não histórico de fills |
| Compatibilidade com futura semântica v2 | NOT ESTABLISHED | manifesto não declara semântica completa/bootstrap |

Com `p_hat=0.578`, a fórmula documentada para 1.000 operações de stake 10 produz 693.00 com
payout 0.85 e 808.60 com payout 0.87. Esses valores são esperança matemática do parâmetro
declarado, não resultado observado. Não devem ser apresentados ao cliente como lucro comprovado.

Não houve alteração, retirada, reclassificação ou nova assinatura do manifesto.

## 4. Diferenças de execução confirmadas por inspeção

| Área | LAB atual | BOT atual | Risco |
|---|---|---|---|
| Estado dos indicadores | persistente por toda a lista | família reiniciada a cada avaliação | indicadores recursivos divergem |
| Histórico | lista fornecida ao replay | `min(120, warmup + 3)` | warmup acima de 120 é truncado |
| Composição de regime | valor positivo/genérico | gates específicos, como ADX e largura | mesmo primitivo pode gerar decisão diferente |
| Timeframe | `candidate.tf` não agrega a lista | history solicitado no TF da receita | replay pode avaliar granularidade errada |
| Horário | replay não usa `candidate.hours` | família verifica `close_time` | frequência/resultado do Lab podem incluir hora proibida |
| Próxima vela | próxima linha da lista | broker liquida por evento | gap pode ser tratado como t+1 no replay |
| Payout | lookup por ativo/epoch | quote atual antes da entrada | lookup agregado pode conter informação posterior |
| Família desconhecida | tradução cai para F1 | catálogo valida família | relatório incorreto em vez de rejeição |
| Aprovação de holdout | opcional no runner | gate do manifesto confia no campo | aprovação pode não representar holdout mínimo |

Arquivos de referência e hashes SHA-256 da inspeção:

- `apps/core/families/base.py`: `92298C33FA541EF2A152DC4675D32B7D6F540D6328A5E7FB2270BBF20148338F`;
- `apps/core/iqoption_auto_trader.py`: `F8852B9B878A9639824F39A2B44FA66F1EF7BE18D2EEC4764F98A4752898B034`;
- `strategy-lab/tools/strategy_lab/research/replay_simulator.py`:
  `501FFFAADDDE78D6402C41BEB31E3F63F79ED8327D366FEC25707EBE203FD8DC`;
- `strategy-lab/tools/strategy_lab/research/runner.py`:
  `E828D8103FC2DC43D4F708D28A42D4E79494478DD616442CF890594FC9419496`.

## 5. Decisão temporal e de bootstrap

A decisão completa está em `docs/ADR_EXECUTION_SEMANTICS_AND_BOOTSTRAP.md`. Resumo:

- `Candle.ts` do Lab é abertura; decisão usa fechamento;
- I-3 permanece estrita, inclusive seu candle adicional de margem;
- M1→M5/M15 exige todos os filhos e volume desconhecido permanece `None`;
- horários usam fechamento UTC `[start,end)`; aquecimento continua fora da janela;
- gaps não pulam para a próxima linha; operação fica excluída;
- bootstrap tem fingerprint e identidade completa; correção/generation reconstrói;
- `legacy.bot.window-replay.v1` e `legacy.lab.incremental.v1` não são equivalentes;
- `tl.candle-close.v2` é contrato-alvo, ainda não executável;
- rollout começa por leitores/vetores/shadow sem submissão.

## 6. Matriz de risco e bloqueios para as próximas fases

| Risco | Severidade | Estado/controle |
|---|---|---|
| Catálogo assinado com evidência numérica não reproduzível | crítica | não promover/resignar; reconstruir no CAT-03..06 |
| Replay e bot gerarem decisões distintas | crítica | contrato v2 + vetores completos antes do incremental |
| Cache hostil cair para manifesto empacotado sem política explícita | alta | resolver contrato/teste antes do consumo remoto |
| Fluxo E2E IQ não submeter no teste autoritativo | alta | baseline falho; correção separada antes de execução nova |
| Catálogo UI omitir Deriv | média/alta | preservar isolamento e definir união/filtro de fontes |
| Runtime BOT não canônico (3.12 versus 3.13) | alta para release | provisionar/validar 3.13 ou formalizar política em tarefa própria |
| Testes Hub/RLS não executados | alta | staging real obrigatório antes de migrations/publicação |
| Deno ausente | média/alta | instalar/usar runtime pinado em tarefa autorizada; executar contratos Hub |
| Formatação/lint falhos | média | corrigir em mudança coesa antes de PR de implementação |
| Flake de subprocesso sob carga concorrente | média | execuções isoladas e teste de estabilidade; não aumentar timeout sem causa |

## 7. Gate de saída do CAT-00

CAT-00 entrega baseline, inventário, ADR e bloqueios. Ele **não entrega baseline verde**.
Portanto:

- pode começar CAT-01, que é inventário Supabase somente leitura;
- CAT-02 pode modelar contrato, mas não publicar;
- nenhum novo engine deve ser habilitado para ordem;
- nenhuma estratégia do catálogo atual deve receber nova alegação de validação;
- as três falhas funcionais, lint/format, Python 3.13 e testes staging/Deno precisam de tarefas
  próprias antes de rollout/build final.

## 8. Ações não executadas

- nenhuma ordem Demo ou Real;
- nenhuma conexão/login em corretora;
- nenhuma alteração em risco, strategy math ou guards Real;
- nenhuma migração, chamada ou limpeza Supabase;
- nenhum arquivo de usuário removido;
- nenhum build/installer/portable;
- nenhum commit, push, PR ou release.
