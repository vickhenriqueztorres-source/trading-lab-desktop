# Desenvolvimento, testes e build — v1.9.11

## 1. Ambiente

- Windows 10/11 64 bits;
- Python 3.13+;
- PowerShell;
- PyInstaller para build do executável;
- Inno Setup 6 para instalador opcional;
- compilador C#/.NET Framework para o lançador portátil opcional.

Dependências de runtime e desenvolvimento estão fixadas em `pyproject.toml`.

## 2. Preparar o ambiente

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Para gerar o executável, instale também PyInstaller na versão homologada pelo ambiente de build.

## 3. Executar pelo código-fonte

Perfil local explícito:

```powershell
python -m apps.launcher --profile-dir .\data\profiles\local --workers simulated deriv_read_only
```

Modo headless e encerramento limitado:

```powershell
python -m apps.launcher --profile-dir .\data\profiles\test --workers simulated deriv_read_only --headless-ui --auto-shutdown-after 2
```

Transportes Deriv aceitos pela CLI:

```text
fake-public
fake-demo
live-public
live-demo
live-real
```

O padrão é `fake-public`. Modos autenticados dependem de credenciais já protegidas no perfil e não
devem receber token pela linha de comando.

## 4. Comandos de qualidade

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

Subconjuntos:

```powershell
python -m pytest tests/unit
python -m pytest tests/contract
python -m pytest tests/integration
python -m pytest tests/replay
python -m pytest tests/chaos
```

Somente coleta:

```powershell
python -m pytest --collect-only -q
```

Na v1.9.11, a coleta documentada contém 613 testes.

## 5. Testes externos

Testes comuns não dependem de internet. O smoke público Deriv é opt-in:

```powershell
$env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC = "1"
python -m pytest tests/external/test_deriv_public_external.py -m external_deriv_public
Remove-Item Env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC
```

Testes autenticados externos usam Demo e exigem opt-in/configuração específicos do harness. Nunca
grave token em teste, fixture, comando versionado ou saída. Teste Real financeiro é proibido.

## 6. Testes por área

### Estratégias e ticks

- aquecimento e contexto inválido;
- três janelas e desempates determinísticos;
- Wilson e thresholds;
- sinal/abstenção;
- frequência e escala decimal;
- latência do ring buffer;
- radar com estado isolado por ativo.

### Risco e martingale

- limites da configuração;
- progressão e reset por settlement;
- teto de stake/passos;
- perda máxima projetada;
- mudança bloqueada durante sequência;
- Stop Loss, Take Profit e cooldown;
- concorrência de exposição global/por símbolo.

### Deriv

- validação de endpoint e account type;
- token/vault sem plaintext;
- Demo/Real separados;
- proposal/buy/settlement;
- todos os contratos de dígitos;
- timeout ambíguo e reconciliação;
- reconexão autenticada;
- execução de cada estratégia e próximo passo de martingale.

### Persistência

- migrações/checksum;
- commit atômico;
- I/O failure;
- integridade/marker;
- backup e restore isolado;
- kill antes e depois do commit;
- duplicidade e evento fora de ordem.

### Launcher/UI

- árvore real sem órfãos;
- segunda instância;
- Job Object;
- safe shutdown;
- perda da UI/Core/worker;
- projeção/reconexão do polling;
- renderização headless e i18n.

## 7. Soak local

O soak é sintético, local, read-only e exige opt-in:

```powershell
python -m apps.core.soak_cli --run-soak-matrix --duration-seconds 5 --max-cycles 100 --max-reports 10
python -m apps.core.soak_cli --run-soak-matrix --profile fast --fault-preset heavy_load
```

Perfis:

```text
fast, standard, extended, chaos
```

Fault presets:

```text
none, intermittent_crash, sleep_resume_gap, heavy_load
```

Exit codes:

- `0`: matriz aprovada;
- `1`: matriz reprovada ou falha operacional;
- `2`: opt-in ausente ou argumentos inválidos.

## 8. Build onedir

```powershell
python build_scripts\compile_trading_lab.py --version 1.9.11 --output-dir .\dist
```

Etapas:

1. PyInstaller usa `build_scripts/TradingLab.spec`;
2. confirma `dist/TradingLab/TradingLab.exe`;
3. executa o SecretScanner;
4. gera `release_manifest.json` com SHA-256 e tamanho;
5. verifica o pacote completo;
6. executa `TradingLab.exe --post-update-health-check`.

O pacote exclui bancos, vaults, logs, `.env`, testes e caches. Build contaminado falha fechado.

`--skip-pyinstaller` existe apenas para testes de staging; ele cria um stub quando o binário não
existe e não deve ser entregue como aplicação.

## 9. Manifesto e health check

No executável congelado, o Launcher procura `release_manifest.json` ao lado do binário e verifica:

- hash do próprio manifesto;
- presença de todos os arquivos;
- tamanho;
- SHA-256;
- arquivos extras não permitidos.

Health check manual:

```powershell
.\dist\TradingLab\TradingLab.exe --post-update-health-check
```

Exit code diferente de zero bloqueia a distribuição.

## 10. Instalador Inno Setup

Depois do onedir:

```powershell
ISCC.exe /Q build_scripts\TradingLab_Setup.iss
```

Saída esperada:

```text
dist\TradingLab_Setup_v1.9.11.exe
```

O instalador usa privilégio de usuário, cria atalhos e executa o health check pós-instalação. O
desinstalador fica em `%LOCALAPPDATA%\TradingLab\uninstall` para não contaminar o manifesto da
aplicação.

## 11. Executável portátil de arquivo único

O arquivo único usado nesta entrega é um invólucro C# que incorpora um ZIP da pasta onedir como
recurso `TradingLab.payload.zip`.

Fluxo de montagem:

1. gerar e verificar a pasta onedir;
2. compactar a pasta `TradingLab` preservando sua raiz;
3. compilar `PortableLauncher.cs` com o ZIP como recurso incorporado de nome exato
   `TradingLab.payload.zip`;
4. verificar `ProductVersion`, tamanho e SHA-256 do resultado;
5. abrir e testar startup/conexão com bot pausado.

O repositório ainda não contém um único script canônico que automatize as quatro etapas do
invólucro portátil. A pasta onedir e o instalador são os pipelines formalizados; a geração portátil
deve ser tratada como etapa de release controlada.

## 12. Versionamento

Ao mudar a versão, mantenha coerentes:

- `pyproject.toml`;
- `apps/ui/app.py`;
- versão da estratégia no auto trader;
- defaults dos scripts de build;
- `build_scripts/version_info.txt`;
- `build_scripts/TradingLab_Setup.iss`;
- `build_scripts/PortableLauncher.cs`;
- expectativas de smoke de distribuição;
- documentação e WORKLOG.

## 13. Mudança financeira segura

Antes de alterar ordem, risco, worker ou reconciliação:

1. identifique o estado dono;
2. modele timeout antes/depois do possível envio;
3. preserve persist-before-act;
4. nunca adicione retry cego de submissão;
5. mantenha `UNKNOWN` como exposição;
6. cubra crash/restart/duplicidade;
7. teste safe stop com ordem aberta;
8. execute scanner de segredos;
9. atualize documentação e WORKLOG.

## 14. Arquivos gerados que não devem ser versionados

- `dist/` e diretórios de build;
- caches `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`;
- perfis locais;
- `*.db`, `*.db-wal`, `*.db-shm`;
- `*.vault`;
- relatórios/diagnósticos reais;
- tokens ou respostas de autenticação;
- executáveis temporários de release, salvo política explícita de artefato.
