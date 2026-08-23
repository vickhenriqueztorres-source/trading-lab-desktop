# Observabilidade e Diagnóstico

## 1. Objetivo

Observabilidade deve explicar estado e falha sem virar um canal de segredo ou uma segunda fonte de
verdade financeira. O Core continua autoritativo; eventos e snapshots são projeções operacionais.

## 2. Modelo implementado

`OperationalEvent` é imutável e contém:

```text
event_name
occurred_at (UTC)
reason_code opcional
fields escalares ordenados
```

Valores permitidos no sink atual: `str`, `int`, `bool` ou `None`. Não são aceitos objetos/payloads
arbitrários.

Sinks atuais:

- `NullEventSink`: descarta eventos explicitamente;
- `InMemoryEventSink`: collector thread-safe, bounded apenas pelo ciclo de vida do teste/harness.

Não existe sink remoto, arquivo de log de produção ou analytics nesta fase.

## 3. Categorias de eventos

### Core e banco

- startup/shutdown;
- single instance;
- migration/integrity/write failure;
- backup;
- recovery e Health Gate.

### Worker e IPC

- processo iniciado/morto;
- conexão/handshake/version;
- backpressure/circuit breaker;
- shutdown/restart.

### Ordem e reconciliação

- intenção/outbox/dispatch certainty;
- ordem aceita/rejeitada/unknown;
- tentativa/evidência/outcome de reconciliação;
- eventos duplicados, gap, conflito e settlement.

### Identidade/licença

Somente status reduzido e reason code. OTP, token, device private key e lease bruta são proibidos.

### Estratégia e market data

- manifesto/status/entitlement;
- sinal/arbiter/allocator/risk reduzidos;
- candle ID/stream/fechamento, sem OHLC bruto por padrão;
- backfill/gap/stale/reconnect/health;
- replay/checkpoint/divergência;
- soak/ciclos/recursos/outcome.

## 4. Reason codes

Reason code é estável e voltado a UI, suporte e testes. Mensagem humana pode mudar; o código não
deve mudar silenciosamente. Famílias e fontes estão em
[ERROR_AND_HEALTH_CODES.md](ERROR_AND_HEALTH_CODES.md).

## 5. Campos permitidos

Preferir:

- `message_id`, `correlation_id`, `causation_id` redigidos quando necessário;
- broker/mode/capability;
- state anterior/novo;
- reason code;
- contadores e duração;
- PID/health/RSS do subprocesso;
- IDs de série, estratégia/versão e config hash;
- batch size e lag.

Mesmo IDs podem ser dados sensíveis em suporte. O pacote futuro deve avaliar hashing/escopo antes de
exportar.

## 6. Campos proibidos

- senha, cookie, OTP, token e Authorization header;
- private key e lease bruta;
- credencial/sessão de broker;
- payload bruto de autenticação ou corretora;
- saldo/histórico completo;
- candle completo por padrão;
- banco SQLite;
- PII desnecessária;
- exception string que possa conter resposta externa;
- `SecretValue.reveal_*()`.

## 7. Snapshots operacionais

Snapshots de shadow/soak incluem estado, health, subscriptions, ciclos, falhas, recoveries, lag e
recursos. Eles são imutáveis e bounded. Relatórios temporais/matriz serializam somente essas
projeções redigidas; nunca comandos financeiros.

### 7.1 Publicação e retenção de relatórios de soak

`atomic_write_json` serializa JSON UTF-8 determinístico, rejeita `NaN`/infinito e publica por
arquivo temporário único no mesmo diretório, `flush`, `fsync` e `os.replace`. Falha de
serialização, escrita ou substituição remove o temporário possível e retorna
`ATOMIC_JSON_WRITE_FAILED`, sem expor a exceção bruta.

`ReportRetentionPolicy` limita simultaneamente quantidade e bytes. O manager atua apenas sobre
arquivos regulares que correspondam a `soak_matrix_*.json` dentro do diretório resolvido e remove
os mais antigos por `mtime` e nome até cumprir ambos os limites. Symlink, escape de escopo, falha
de `stat` ou de remoção falham fechado; JSONs não relacionados não são tocados. A saída da CLI
expõe somente outcome, contadores, nome do relatório e quantidade removida, nunca caminho absoluto
ou exceção bruta.

Perfis e fault presets acrescentam somente `execution_profile`, `fault_preset`, ciclos programados,
tipo da falha, estado `INJECTED/OBSERVED/RECOVERED` e reason code estável. Não há PID externo,
exception string ou payload de market data na agenda. O payload completo passa por `SecretScanner`
em memória antes da escrita atômica; qualquer match cancela a publicação.

## 8. Métricas candidatas

Métricas locais úteis:

- startup/recovery duration;
- DB health failures;
- outbox pending/ambiguous;
- ordens unknown/settlement unknown;
- worker restarts e circuit state;
- reconciliation outcomes;
- event queue backpressure;
- market gaps/stale/reconnect;
- backfill requests/retries/failures;
- candle duplicates/conflicts;
- replay divergence;
- strategy evaluation/block reason;
- soak cycles/failures/recoveries/RSS/CPU/lag.

Cardinalidade deve ser controlada. Não use payload, e-mail, order ID irrestrito ou símbolo arbitrário
como label remoto futuro sem política.

## 9. Pacote de diagnóstico redigido e bundle de suporte local

Implementado na Fase 3 (Fatia 3.1) via `DiagnosticBundleBuilder` (`packages/observability/diagnostic.py`) e `CoreDiagnosticService` (`apps/core/diagnostic_service.py`):

- **Disparo Seguro**: Acionável via botão "📦 Gerar Diagnóstico" na interface Tkinter (`DualTradeDesktopApp`), trafegando pelo protocolo IPC v1 (`UI_GENERATE_DIAGNOSTIC_COMMAND` / `UI_GENERATE_DIAGNOSTIC_RESPONSE`).
- **Arquivos incluídos no ZIP** (`diagnostic_bundle_{timestamp}_{short_hash}.zip`):
  - `manifest.json`: Versão da aplicação, timestamp UTC de compilação e mapeamento SHA-256 / tamanho dos arquivos do pacote.
  - `environment.json`: Sistema operacional, versão do Python, uptime do processo e árvore de processos (Launcher/Worker).
  - `health_gates.json`: Snapshot estruturado dos Health Gates (global e por corretora/conta).
  - `risk_summary.json`: Métricas consolidadas de risco global (exposição, stop loss diário, perdas consecutivas, estado de risco).
  - `recent_events.json`: Lista delimitada (bounded, padrão 1000) de `OperationalEvent` recentes emitidos para `InMemoryEventSink`.
- **Exclusões Estritas e Invariantes de Segurança**:
  - Proibição absoluta de inclusão de bancos SQLite (`state.db`, `strategy_data.db`, `*.db-wal`), arquivos de vault (`.vault`), chaves Ed25519/RSA/PEM, tokens de sessão ou cookies.
  - **Fail-Closed Security Scan**: Antes da compactação final em `.zip`, todo o diretório temporário passa por varredura com `SecretScanner`. Se qualquer padrão sensível for detectado (`report.is_clean == False`), a geração é imediatamente abortada, o diretório temporário é destruído (`shutil.rmtree`) e uma exceção `DiagnosticSecurityViolationError` é lançada.
- **Retenção Bounded**:
  - O diretório `reports/diagnostics/` aplica `ReportRetentionPolicy` gerenciado por `ReportRetentionManager` (padrão: máx. 5 arquivos ZIP, limite de 50 MB total), descartando os bundles mais antigos automaticamente.

## 10. Resposta operacional

Ao diagnosticar:

1. registre estado/reason code e tempo UTC;
2. preserve correlação sem copiar payload bruto;
3. confirme owner do estado;
4. verifique banco/worker/clock/license/market health;
5. pare novas entradas se segurança não puder ser comprovada;
6. continue acompanhamento/reconciliação de ordens abertas;
7. não reclassifique `UNKNOWN` por log incompleto.

Consulte [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md).

## 11. Testes

- eventos estruturados em startup/backup/recovery;
- `SecretValue` redigido;
- relatórios JSON sem termos/payloads financeiros proibidos;
- exceções de matriz reduzidas a reason code;
- queues/backpressure e sample retention bounded;
- scanner automatizado do workspace/fixtures/relatório e scanner manual do diff após mudança
  material;
- geração de pacotes de diagnóstico redigidos (`tests/unit/test_diagnostic_bundle.py`);
- exportação e validação ponta a ponta via IPC/UI (`tests/integration/test_diagnostic_ui_export.py`).

## 12. Limitações

- sink in-memory não possui persistência em disco de longo prazo;
- não há envio automático de telemetria remota por design de privacidade e isolamento;
- o pacote de diagnóstico é estritamente local (`reports/diagnostics/`), cabendo ao usuário o compartilhamento voluntário com o suporte.
