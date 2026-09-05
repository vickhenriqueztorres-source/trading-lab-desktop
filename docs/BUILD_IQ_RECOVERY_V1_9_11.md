# Build Windows — IQ recovery v1.9.11

Data: 2026-09-03. Código do working tree, incluindo a correção da Causa 5.
Não é uma aprovação de produção nem uma homologação externa.

## Artefato

`dist/iq-scoped-recovery-20260903/TradingLab-v1.9.11-IQ-RECOVERY.exe`

- Portátil C# com payload onedir; windowed, PySide6, versão 1.9.11.
- Tamanho: 47.734.272 bytes.
- SHA-256: `277F37BD69A34A78D7DD3DC807C4173139B5D9C0CC56B86DD0FC25B2EE8C7F56`.
- Pasta onedir: `dist/iq-scoped-recovery-20260903/TradingLab/`.
- Payload: `dist/iq-scoped-recovery-20260903/TradingLab.payload.zip`.
- Manifesto: 434 arquivos rastreados; hash
  `cb65d6bde89b03cf3c8577f058f3c9a986c2c99f4382a617b889a092c2c865f6`.

## Pipeline e verificações

`python -u build_scripts/compile_trading_lab.py --version 1.9.11 --output-dir dist/iq-scoped-recovery-20260903`

Usado TradingLab.spec atual, sem spec legado e sem stub/skip-pyinstaller.
Diretório novo: distribuições anteriores e dados do operador foram preservados.
PyInstaller 6.22.2 no ambiente existente. Scanner da distribuição: zero achados.
Integridade e `--post-update-health-check`: aprovados.

ZIP criado por System.IO.Compression.ZipFile, preservando a raiz TradingLab.
PortableLauncher.cs compilado pelo csc x64 como winexe, com Windows.Forms,
IO.Compression e IO.Compression.FileSystem; recurso incorporado TradingLab.payload.zip.
Uma tentativa do csc com caminho relativo falhou; a execução com caminhos absolutos
gerou o artefato acima. Nenhuma mudança no código do launcher foi necessária.

ZIP com 838 entradas: nenhum state.db, strategy_data.db, vault, broker_credentials
ou subprojeto strategy-lab. O fonte embarcado iqoption_failures.py é idêntico ao
working tree (SHA-256 a90e38af8e0ffd28fc14336cf52d791064fc7a643fde7f899a03a06ea99e1de7).

## Smoke do compilado

Perfis exclusivos em artifacts/iq-scoped-release, transporte fake-public,
bot inicialmente desligado e fechamento automático pelo lifecycle em 15 s.

1. Primeiro onedir: falha de inicialização. O harness viu o diálogo de erro e
   encerrou somente o processo de teste após 100 s; nenhum descendente permaneceu.
   Ocorreu enquanto o empacotamento portátil estava ativo, mas a causa exata
   **não foi comprovada**. Não foi aumentado timeout nem alterado guard.
2. Diagnóstico no Python congelado: CoreLifecycleService iniciou e encerrou
   corretamente, com simulação de identidade ligada e desligada.
3. Segundo onedir, novo perfil: janela real Qt observada, exit 0, seis processos
   observados, nenhum remanescente.
4. Portátil, perfil novo: extração/execução e janela real Qt observadas, exit 0,
   sete processos observados, nenhum remanescente.
5. Portátil, reabertura no mesmo perfil: janela real Qt, exit 0, sete processos
   observados, nenhum remanescente.

Nos bancos temporários onedir e portátil: schema 9, zero ordens e zero reservas
ativas. Journal confirmou Safe Stop, trading_disarmed, recovery_completed e
core_shutdown_completed. Não foi usada credencial nem enviada ordem externa.

Teste adicional de launcher/supervisor/integridade: 14 passed.
Regressão funcional anterior: 191 passed, conforme relatório da Causa 5.
Pendências globais já registradas (contrato de abas da UI e Ruff) não foram
transformadas em aprovação. A primeira falha de startup permanece registrada
como limitação, apesar dos três smokes completos posteriores aprovados.

Sem instalador Inno, assinatura Authenticode, commit/push ou alteração do perfil
do operador nesta entrega.
