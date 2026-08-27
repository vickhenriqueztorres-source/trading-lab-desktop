# Pós-validação de liveness e build — Trading Lab Desktop v1.9.11

Data da execução: 2026-08-26
Ambiente: Windows, perfil de testes isolado
Veredito: **LOCAL_FIX_VALIDATED**

## 1. Build

Pipeline autoritativo confirmado:

- `build_scripts/TradingLab.spec`;
- `build_scripts/compile_trading_lab.py`;
- `build_scripts/version_info.txt`;
- `build_scripts/TradingLab_Setup.iss`.

O `DualTrade.spec` histórico não foi usado. O build permaneceu PyInstaller `onedir`,
`windowed`, `console=False`, PySide6 e `TradingLab.exe`.

Comando principal:

```text
python build_scripts/compile_trading_lab.py --version 1.9.11 --output-dir dist_post_liveness_v1_9_11_final
```

Resultado do pipeline:

- PyInstaller: **PASS**;
- scanner de segredos: **PASS**, zero achados;
- manifesto: **PASS**, 342 arquivos;
- verificação integral do manifesto: **PASS**;
- health check do launcher compilado: **PASS**;
- versão Windows do binário: `1.9.11.0`;
- DLLs ICU estrangeiras do Poppler: ausentes do pacote final.

Artefatos e hashes:

| Artefato | Tamanho | SHA-256 |
|---|---:|---|
| `TradingLab/TradingLab.exe` | 3.804.256 bytes | `563e00f8e7a8394903b291441bb2129a3a9fbbe936a580ce3f925cdace0ef2ab` |
| `TradingLab/release_manifest.json` | — | arquivo: `4444ff36478ed441e82a92d4d850b6dc31597877c8dc4f3f7c4e3be7e92c1a14` |
| hash lógico declarado no manifesto | — | `469ed7990cc93aefa99fa193338e3f28605cd8c2f11bd5725c6591972c414fb3` |
| `TradingLab.payload.zip` | 46.252.377 bytes | `c53fa8ac01835bd7ca943546fbbed57f286b1fba78f6228af11b2814c9645aea` |
| `TradingLab-Desktop-v1.9.11-PORTABLE.exe` | 46.260.736 bytes | `a39ef7ed72cb183dc5c5c66a9560cb6d31aa5a50946682a87c3bdd6552596863` |

O pacote não contém `state.db`, `strategy_data.db`, vault, pasta de credenciais,
perfil de operador, token ou OTP.

O portable passou health check e execução completa, ambos com código 0. A extração
temporária criada por essas execuções foi removida pelo próprio launcher.

### Installer

**NOT EXECUTED / BLOCKED**. O script Inno Setup está presente, mas `ISCC.exe` não
está instalado nos caminhos suportados desta máquina. Nenhum instalador foi declarado
como gerado. O portable é o artefato único executável entregue nesta validação.

## 2. Validação local

Comandos executados:

```text
python -m pytest
python -m ruff check apps packages tests
python -m ruff format --check apps packages tests
python -m mypy apps packages
python -m compileall apps packages
git diff --check
```

Resultado final:

- pytest: **616 passed, 4 skipped, 0 failed**;
- Ruff check: **PASS**;
- Ruff format: **PASS**, 335 arquivos conformes;
- mypy: **PASS**, 211 arquivos-fonte;
- compileall: **PASS**;
- `git diff --check`: **PASS**;
- scanner da distribuição: **PASS**, zero achados.

Transparência da regressão: uma primeira execução longa teve uma leitura transitória
`DEGRADED` imediatamente após `start_all()` no teste de árvore real. O mesmo cenário
passou cinco vezes seguidas, e duas regressões completas posteriores terminaram com
616 aprovados e zero falhas. Não houve aumento de timeout para esconder o evento.

Quatro arquivos foram normalizados pelo formatador antes da execução final. A cobertura
de Martingale foi ampliada e a regressão integral foi repetida depois dessa alteração.

## 3. Smoke do aplicativo compilado

### Startup, restart e interface

**EXECUTED / PASS**.

Com um único perfil descartável, o executável compilado foi iniciado três vezes:

1. headless: código 0, 15,328 s;
2. headless, mesmo perfil: código 0, 15,016 s;
3. interface gráfica: código 0, 14,937 s.

Fluxo observado: Launcher → Core → Auth Agent → worker simulado → Deriv público → UI.
O perfil iniciou em Safe Stop, produziu eventos `trading_disarmed` e não anexou rota
financeira externa.

### Instância única

**EXECUTED / PASS**.

A primeira instância alcançou a árvore completa de seis processos. Uma segunda abertura
do mesmo perfil foi recusada em 1,797 s com código 2; a primeira permaneceu viva e
encerrou normalmente com código 0. Contagem final do executável: zero processos.

### Banco e migrações

**EXECUTED / PASS**.

- `PRAGMA quick_check`: `ok`;
- migrations 0001 a 0005 presentes;
- `0005_digit_risk_runtime` aplicada;
- singleton de `digit_risk_runtime` presente;
- nenhum lock fantasma após shutdown ou restart.

### Queda abrupta e restart

**EXECUTED / PASS**.

Após `worker_ready`, o processo raiz foi encerrado de forma abrupta em perfil isolado.
Resultado:

- raiz retornou código 1, como esperado para kill;
- zero filhos após 3 s, comprovando contenção pelo Job Object;
- o mesmo perfil reiniciou com código 0;
- `recovery_started`/`recovery_completed` ocorreram novamente;
- apenas o segundo ciclo registrou shutdown limpo;
- o sistema permaneceu desarmado;
- zero processos após o shutdown do restart.

## 4. Journal operacional

**EXECUTED / PASS**, com uma limitação registrada.

O arquivo `core/operational-journal.jsonl` foi criado no perfil correto, persistiu três
reinícios e registrou startup, recovery, worker start/ready/restart/crash, Safe Stop,
DISARM e shutdown. Um journal de smoke acumulou 56 linhas/8.160 bytes; o cenário de
circuit breaker acumulou 59 linhas/8.900 bytes.

O scanner aplicado aos journals encontrou zero segredos. O teste específico de
persistência, redação e rotação passou. Capacidade e rotação são bounded.

ARM não foi gerado no artefato compilado porque esta validação sem credencial manteve o
bot deliberadamente desarmado; sua persistência é coberta pela suíte local.

## 5. Martingale

**EXECUTED / PASS** com os transports/fakes oficiais.

Cenário provado:

```text
LOSS step 0
→ step 1 / stake 200 / R_100 pinado
→ restart completo do Core
→ step 1 restaurado
→ LOSS step 1
→ step 2 / stake 400 / perda acumulada 300
→ segundo restart completo
→ step 2 e pin restaurados
→ WIN
→ step 0 / stake base 100 / pin removido / sequência zerada
```

O mesmo teste confirma que settlement duplicado e evidência tardia de reconciliação não
duplicam P&L nem progressão do Martingale.

## 6. Circuit breaker no build

**EXECUTED / PASS**.

Depois de aguardar a árvore compilada completa, três processos de worker simulado foram
encerrados dentro da janela da política. Evidência observada:

- três agendamentos de restart;
- `worker_circuit_opened`: 1;
- blocker `HG_WORKER_CIRCUIT_OPEN` ativado;
- probe automático após a janela OPEN;
- quarto `worker_ready` confirmou estabilidade;
- blockers de worker foram limpos;
- processo raiz terminou com código 0;
- zero processos residuais.

A transição de estado exata `OPEN → HALF_OPEN → CLOSED` também passou no teste de contrato
com relógio controlado. O circuito não permaneceu OPEN indefinidamente e não foi fechado
apenas pela existência de um PID.

## 7. Generation fencing

**EXECUTED / PASS**.

O teste direcionado confirmou que telemetria de geração aposentada não acessa o cliente,
não executa probe e não pode reabrir nem limpar os blockers da geração atual. A regressão
completa também cobre replacement de worker e recovery generation-aware.

## 8. Health Gate e readiness

**EXECUTED / PASS** no escopo local/compilado.

Os únicos blockers de startup observados no perfil sem broker foram os esperados:
`HG_SAFE_STOP` e prova de clock/market ainda indisponível para a fonte pública. Durante
as falhas injetadas, `HG_WORKER_DISCONNECTED` e `HG_WORKER_CIRCUIT_OPEN` apareceram e
foram limpos após recovery. Não foi encontrado Health Gate órfão de worker.

Sem credencial, `ready_to_trade` permaneceu falso. Recovery e reconnect geraram DISARM;
nenhum deles gerou ARM automático.

## 9. Proteção absoluta da conta Real

**PASS por inspeção e testes locais**.

O build mantém `allow_real_financial_submission=False`. Em `live-real`, o lifecycle
executa somente reconciliação/leitura e retorna antes de criar `DerivDigitAutoTrader`.
Nenhum guard de Real foi reduzido. Nenhuma ordem Real foi enviada.

## 10. Deriv Demo externa

Não havia uma credencial DEMO atual instalada de forma segura no perfil isolado desta
execução. Nenhum token foi inventado, recuperado de arquivos aleatórios ou copiado para
logs. Como `account_type == demo` não pôde ser provado oficialmente, toda a parte externa
financeira foi abortada conforme a regra absoluta.

| Cenário | Execução | Resultado |
|---|---|---|
| precheck oficial de conta/OTP/WebSocket Demo | NOT EXECUTED | BLOCKED — sem credencial DEMO no perfil isolado |
| soak externo somente conexão | NOT EXECUTED | BLOCKED |
| primeiras ordens financeiras Demo | NOT EXECUTED | BLOCKED |
| disconnect com ordem Demo aberta | NOT EXECUTED | BLOCKED |
| restart com ordem Demo pendente | NOT EXECUTED | BLOCKED |
| Martingale externo Demo | NOT EXECUTED | BLOCKED |
| timeout financeiro/UNKNOWN externo | NOT EXECUTED | BLOCKED |
| sleep/resume com Deriv Demo | NOT EXECUTED | BLOCKED |
| soak externo prolongado | NOT EXECUTED | BLOCKED |

Nenhum cenário não executado foi marcado como PASS.

## 11. Limitações externas e de instalação

- sem credencial DEMO atual e comprovável no perfil isolado;
- nenhum acesso financeiro externo foi realizado;
- Inno Setup ausente, portanto installer e smoke de uninstall não foram executados;
- sleep/resume físico do Windows não foi realizado;
- soak externo prolongado não foi realizado;
- o journal do build não registrou ARM porque o operador não armou uma sessão Demo.

## 12. Veredito

**LOCAL_FIX_VALIDATED**

O código corrigido gerou um artefato Windows íntegro; EXE e portable abriram e fecharam
corretamente; restart e kill não deixaram órfãos; circuit breaker recuperou; geração antiga
não contaminou o estado; Martingale e settlement permaneceram duráveis/idempotentes; e
nenhuma recuperação rearmou trading. A classificação não é externa porque os cenários
Deriv Demo não foram executados nesta etapa.
