# Processo de Release

## 1. Estado atual

Existe um pipeline local de desenvolvimento para gerar `TradingLab.exe` em modo onedir e o
instalador Inno Setup. O artefato continua sendo **practice/demo, não assinado e não comercial**:
conta real permanece bloqueada e os gates de assinatura, CI reproduzível, SBOM e validação em VM
limpa ainda são necessários antes de qualquer canal alpha/beta.

## 2. Princípios

- release não amplia permissões silenciosamente;
- conta real nunca é padrão;
- build não contém segredo confiável;
- dependencies e migrations são versionadas;
- artefato futuro é assinado e verificável;
- atualização é transacional, possui health check e rollback;
- não atualizar com ordem ambígua;
- workers podem ser bloqueados por incompatibilidade antes de operação;
- release registra evidência e risco residual.

## 3. Versionamento

O pacote atual declara `0.0.1` em `pyproject.toml`. Antes de release formal, definir:

- política SemVer/calendário;
- compatibilidade de IPC;
- compatibilidade de banco/migration;
- compatibilidade de lease/client manifest;
- versão independente ou conjunta dos workers;
- schema dos relatórios/diagnóstico.

Mudança de protocolo incompatível exige nova versão negociada; não altere silenciosamente v1.

## 4. Dependências

- versões fixadas no `pyproject.toml`;
- revisão de origem/licença/CVE;
- lock/estratégia reproduzível a definir;
- SBOM futura;
- dependência específica de broker confinada ao worker;
- nenhum download/execução de código de estratégia arbitrário.

## 5. Gate de código

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

Também:

- scanner de segredos;
- revisão de migrations/checksums;
- teste de upgrade/recovery/backup;
- contract tests dos workers;
- caos/soak proporcionais;
- documentação/worklog atualizados;
- nenhum teste real obrigatório.

## 6. Pipeline de Compilação Windows (TradingLab.exe) e Instalador (Fase 3)

Pipeline implementado em `build_scripts/compile_trading_lab.py`, `build_scripts/TradingLab.spec`, `build_scripts/version_info.txt` e `build_scripts/TradingLab_Setup.iss`:

- **Estrutura do Pacote Compilado (`dist/TradingLab/`):**
  - `TradingLab.exe` (binário nativo compilado com PyInstaller em modo `--onedir` e `--windowed`, sem janela CMD de console).
  - Metadados de versão do Windows (`version_info.txt` com Versão "1.0.0.0", Produto "Trading Lab Desktop", Copyright e Descrição).
  - Inclusão dos módulos `apps/`, `packages/`, PySide6 (Qt 6), estilos QSS e traduções i18n.
  - `release_manifest.json` (manifesto canônico de integridade gerado com `ReleaseManifestBuilder`).
- **Higienização Obrigatória:** exclusão estrita de `.env`, `.db`, `.vault`, `.log`, testes e caches.
- **Varredura Fail-Closed:** execução mandatória de `SecretScanner.scan_directory()` no pacote antes de finalizar o build; qualquer match aborta o build imediatamente.
- **Verificação de Integridade no Startup:** no executável congelado, o Launcher deriva automaticamente a raiz da distribuição a partir de `sys.executable` e valida o `release_manifest.json` adjacente via `ReleaseIntegrityVerifier.verify_distribution()` antes de iniciar subprocessos. Manifesto ausente, arquivo divergente, extra ou ausente falha fechado antes do primeiro subprocesso.
- **Perfil gravável por usuário:** o build instalado usa `%LOCALAPPDATA%\TradingLab\profiles\default`; nenhum banco, vault ou estado mutável é gravado em `{app}`.
- **Smoke do artefato:** após gerar e auto-verificar o manifesto, `compile_trading_lab.py` executa o binário empacotado com `--post-update-health-check`. Falha, timeout ou impossibilidade de execução aborta o build.
- **Gerador de Instalador Inno Setup (`TradingLab_Setup.iss`):**
  - Gera o instalador executável `dist/TradingLab_Setup_v1.0.0.exe` para Windows 10/11 64 bits.
  - Cria atalhos no Menu Iniciar e na Área de Trabalho com suporte a desinstalação limpa.
  - Executa `TradingLab.exe --post-update-health-check` no fim da instalação e usa `GetCustomSetupExitCode` para devolver código não zero se o pacote instalado não passar na verificação, inclusive em modo silencioso.
  - Mantém `unins*.exe/.dat` em `%LOCALAPPDATA%\TradingLab\uninstall`, fora da raiz imutável `{app}`, para que o desinstalador do Inno não seja confundido com arquivo executável não rastreado pelo manifesto.

Build local reproduzível no host Windows com as dependências de desenvolvimento e Inno Setup 6:

```powershell
python build_scripts/compile_trading_lab.py --version 1.0.0 --output-dir dist
ISCC.exe /Q build_scripts/TradingLab_Setup.iss
dist\TradingLab\TradingLab.exe --post-update-health-check
```

O smoke de instalação deve usar um diretório temporário explícito, confirmar o `InstallLocation`
registrado antes de desinstalar e validar: exit code do instalador, arquivos instalados, health-check,
startup/shutdown bounded, exit code do desinstalador e ausência de processos órfãos.

## 7. Migrações e rollback

- migration publicada é imutável;
- upgrade roda transacionalmente quando suportado;
- checksum divergente bloqueia;
- rollback de aplicativo só é suportado quando schema/compatibilidade permitirem;
- backup consistente antes de migration destrutiva futura;
- banco nunca é apagado no rollback;
- ordem ambígua impede update.

## 8. Atualizador Seguro e Rollback Transacional (Fase 3 — Fatia 3.4)

Mecanismo implementado em `packages/security/updater.py` e `apps/launcher/updater_service.py`:

1. **Manifesto Assinado Ed25519:** `SignedUpdateManifest` contendo versão alvo, versão mínima de origem, timestamp UTC, hash SHA-256 do pacote `.zip` e assinatura digital Ed25519 validada via `UpdateSignatureVerifier`.
2. **Guarda de Exposição Ativa:** `UpdateSafetyGuard.can_apply_update()` valida se o Core está livre de ordens em aberto (`PENDING`, `ACCEPTED`, `OPEN`, `UNKNOWN`, `SETTLEMENT_UNKNOWN`) e reservas de risco ativas antes de permitir qualquer atualização.
3. **Backup e Staging Isolados:** `UpdateApplier` extrai o pacote em `updates/staging/{version}/`, valida a integridade e cria snapshot do release funcional em `updates/backup/{current_version}/`, ignorando estritamente arquivos de banco (`*.db*`), vaults (`*.vault`) e logs.
4. **Post-Update Health Check:** verificação pós-atualização via flag `--post-update-health-check` validando integridade do manifesto e dry-run do banco de dados.
5. **Rollback Automático Fail-Closed:** se o health check falhar, o `UpdateManager` dispara automaticamente `UpdateApplier.rollback()`, restaurando a versão anterior a partir do snapshot de backup, limpando o staging e preservando intactos todos os bancos de dados financeiros.

## 9. Canais

Proposta futura:

- internal/dev: somente simuladores;
- alpha: demo/practice controlado;
- beta: practice com distribuição assinada;
- stable: depende de critérios legais/produto;
- real: gate separado, nunca consequência automática do canal.

## 10. Critérios por modo

### Desenvolvimento practice/demo

- UI e instalador locais disponíveis, porém sem assinatura de produção;
- sem conta real;
- worker financeiro simulado;
- Deriv read-only fake padrão.

### Alpha/Beta practice futuros

- instalador assinado;
- vault Windows validado no artefato instalado e sob outro SID;
- suporte/diagnóstico;
- health/reconciliation/soak aprovados;
- demo/practice inequívocos;
- externas opt-in.

### Real futuro

Somente decisão formal após todos os critérios do PRD: entitlement/lease curta, confirmação
explícita, risco, suporte, compliance, atualização segura, reconciliação e estratégia aprovada.

## 11. Notas de release

Devem listar:

- versão e data;
- escopo implementado;
- migrations/protocol compatibility;
- brokers/modes permitidos;
- validações executadas;
- limitações/riscos;
- instruções de rollback;
- known issues;
- nenhuma alegação de lucro.

## 12. Bloqueadores atuais

- CI/build reproduzível;
- lock/SBOM/scanner de dependências;
- code signing Authenticode e cadeia de confiança do instalador;
- CI de smoke do instalador em VM Windows limpa;
- validação cross-SID/installer do vault Windows;
- identity backend;
- pacote de diagnóstico;
- update/rollback;
- suporte/security contact;
- políticas legais/regionais.
