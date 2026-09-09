# Terminal de logs operacionais na UI

## Resultado

A aba **Atividade** possui duas visões independentes: **Ordens** e **Logs en vivo**. O terminal
permite acompanhar a sessão corrente do Core sem abrir terminal externo e sem entregar à UI acesso
ao SQLite, ao journal ou aos workers.

Cada linha mostra:

```text
hora-local nível [origem] evento reason=CÓDIGO campo=valor
```

Os filtros cobrem nível (`INFO`, `WARNING`, `ERROR`), origem (`CORE`, `DERIV`, `IQOPTION`,
`WORKER`, outros) e busca textual. **Pausar** congela apenas a tela e aplica os eventos pendentes ao
retomar. **Limpar vista** oculta as linhas atuais, mas não apaga evidência persistida. **Copiar
visíveis** copia somente o conteúdo já sanitizado.

## Fronteira e limites

- O Core é o único produtor da projeção.
- `PersistentJsonlEventSink` retém 256 eventos da sessão em um anel thread-safe.
- O snapshot IPC transporta no máximo 160 eventos e continua limitado a 1 MiB.
- Nome, origem e reason code são normalizados.
- Somente dez campos escalares de uma allowlist fechada podem aparecer por evento.
- Senha, token, cookie, autorização, sessão, e-mail, payload bruto e exception string não fazem
  parte da allowlist.
- A severidade é uma classificação visual; o estado financeiro autoritativo permanece nos Health
  Gates, ordens e máquinas de estado do Core.

## Uso no diagnóstico

1. Abra **Atividade > Logs en vivo**.
2. Filtre por **IQ Option** para conexão, clock, catálogo, payout e submissão.
3. Filtre por **Alertas** ou **Erros** para encontrar o primeiro `reason_code` da falha.
4. Use a busca com o símbolo ou order ID interno quando disponível.
5. Exporte o diagnóstico caso seja necessário preservar contexto além das 160 linhas visíveis.

O terminal não oferece comando, edição, desativação de proteção ou repetição de ordem.

## Verificação e artefato

- testes focados Core/IPC/Qt/diagnóstico: 24 aprovados;
- suíte integral: 1.376 aprovados e 4 pulados por opt-in externo/plataforma;
- Ruff check/format, mypy (311 fontes), compileall, diff-check e scanner: aprovados;
- onedir: 548 arquivos no manifesto, scanner limpo, integridade e health-check aprovados;
- portátil: 963 entradas, recurso único `TradingLab.payload.zip`, ProductVersion 1.9.11;
- health-check do portátil: exit 0 e zero processos residuais.

Artefato:
`dist/ui-log-terminal-r2-20260909/TradingLab-Desktop-v1.9.11-UI-LOG-TERMINAL.exe`.

SHA-256: `10BC4BFCD3559235A8F4983FB7A89A99EE98DA0E8C2C95A75A30AF7B98238E87`.
