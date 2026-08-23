# Desenvolvimento Local

## 1. Escopo

Este guia prepara e valida o ambiente da Fase 1. O repositório ainda não possui UI, instalador ou
conta externa executável, mas já possui Launcher local de produto e CLI interna de soak.

## 2. Pré-requisitos

- Windows 10/11 64 bits;
- Python 3.13 disponível como `py -3.13` ou `python`;
- PowerShell;
- permissão para criar subprocessos e arquivos em diretórios temporários;
- nenhuma credencial de broker.

Dependências runtime:

- `cryptography==46.0.5`;
- `websockets==15.0.1`.

Dependências dev:

- `pytest==8.4.1`;
- `ruff==0.15.22`;
- `mypy==1.17.1`.

O `pyproject.toml` é a fonte de verdade das versões.

## 3. Ambiente virtual

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Verificação:

```powershell
.\.venv\Scripts\python --version
.\.venv\Scripts\python -m pytest --version
```

## 4. Validação

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

Para iteração, rode primeiro o arquivo afetado. Antes de concluir uma fatia, execute a suíte
integral. O plano completo está em [../TEST_PLAN.md](../TEST_PLAN.md).

## 5. Arquitetura de desenvolvimento

### Core

`apps/core/` coordena startup, single instance, SQLite, recovery, Risk Ledger, worker supervisor,
reconciliação, eventos, estratégia e shadow. Ele não importa SDK de corretora e não guarda senha.

### Launcher

`apps/launcher/` possui somente lifecycle: lock `profile.lock`, Windows Job Object, snapshot de
saúde e controle autenticado do host do Core. Não abre `state.db`, não recebe credencial de broker,
não processa market data e não decide risco/stake.

Smoke local seguro:

```powershell
python -m apps.launcher --profile-dir .\data\profiles\local --workers simulated deriv_read_only --auto-shutdown-after 5
```

O Simulated Worker é obrigatório nesta composição. `deriv_read_only` usa transporte fake por
padrão. O argumento não possui opção de conta real ou credencial.

### Workers

`apps/simulated_worker/` é o único worker financeiro e trabalha com estado sintético local.
`apps/deriv_worker/` expõe somente market data read-only. Não existe `apps/iqoption_worker/` porque
nenhuma integração executável IQ foi justificada ainda.

### Packages

`packages/` contém domínio e componentes reutilizáveis sem dependência de UI. Integração Deriv de
candle fica em `packages/brokers/deriv/`; semântica financeira compartilhada não deve receber
condicionais específicos de corretora.

### Testes

Testes usam `tmp_path`, SQLite temporário, clocks fake e subprocessos locais. A suíte comum não usa
rede.

## 6. Fluxo de uma mudança

1. leia guardrails/regras/PRD/arquitetura/worklog;
2. identifique requisitos e proprietário do estado;
3. declare risco e cenários de falha;
4. implemente a menor fatia;
5. use modelo imutável na fronteira;
6. valide payload externo antes do domínio;
7. escreva teste junto;
8. execute validação direcionada e integral;
9. faça scanner de segredo;
10. atualize docs e `WORKLOG.md`.

## 7. Convenções

- linha máxima: 100;
- target Python: 3.13;
- mypy strict em `apps` e `packages`;
- `StrEnum` para estados/reasons;
- `dataclass(frozen=True, slots=True)` nas fronteiras;
- UTC aware para data/hora persistida;
- clock monotônico injetável para duração/backoff;
- dinheiro em minor units/`Decimal` com moeda;
- JSON canônico para hashes;
- sem `pickle`, `eval` ou código remoto arbitrário;
- IDs explícitos para mensagem/correlação/causação;
- listas/filas/batches/relatórios sempre bounded.

## 8. Banco local em testes

`CoreRuntime(profile_directory)` cria:

- `state.db`;
- `state.db.expected`;
- `simulated_broker_state.db` quando usa supervisor simulado.

O pipeline de estratégia usa `strategy_data.db` em seu diretório próprio. Nunca aponte teste para o
perfil real do usuário. Não copie apenas `state.db` enquanto WAL está ativo; use
`DatabaseBackupService`.

## 9. Worker Deriv read-only

O worker tem argumentos internos de host/porta/version e é iniciado pelo supervisor/harness. O
transporte fake é padrão. `--external-public` só pode ser usado em teste explicitamente opt-in e não
aceita credencial.

O smoke suportado é:

```powershell
$env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC = "1"
python -m pytest tests/external/test_deriv_public_external.py -m external_deriv_public
Remove-Item Env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC
```

Não crie environment variable para modo real ou para credencial de broker.

### 9.1 CLI interna de soak

Use somente diretório de diagnóstico isolado e transporte sintético/read-only:

```powershell
python -m apps.core.soak_cli --run-soak-matrix --output-dir .\reports\soak
```

Os limites de ciclos, duração e retenção são validados antes da execução. A CLI permanece em
`DECISION_ONLY`, não aceita credencial, não abre conta de broker e não possui dispatch financeiro.
Relatórios são publicados atomicamente e a retenção atua somente sobre `soak_matrix_*.json`.

## 10. Diagnóstico de testes de subprocesso

Se um teste falhar por startup/handshake:

1. capture o traceback e o teste exato;
2. confirme que não há outra suíte concorrente;
3. verifique processo Python órfão sem matar processos alheios;
4. rode o caso isolado para comparar;
5. inspecione timeout monotônico, porta e cleanup;
6. registre flake no worklog;
7. não aumente o deadline global sem causa comprovada.

## 11. Formatação e edição

Use Ruff para formatação mecânica:

```powershell
python -m ruff format caminho\do\arquivo.py
python -m ruff check --fix caminho\do\arquivo.py
```

Revise qualquer fix automático em área financeira. Migrações publicadas são imutáveis.

## 12. Limitações atuais

- o Launcher existe, mas ainda não há UI nem redirecionamento visual para a instância existente;
- não há configuração persistida de usuário;
- não há CI descrita no repositório;
- não há coverage tool configurada;
- não há build/installer;
- o vault Windows existe localmente, mas falta validação cross-SID/installer em matriz suportada;
- não há IQ worker;
- não há rota externa de ordem.
