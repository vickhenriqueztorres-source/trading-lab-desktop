# Roadmap — DualTrade Desktop

**Política:** avanço por evidência, sem datas prometidas  
**Fase atual:** Fase 1 executável, local e sem submissão real

## 1. Como ler este roadmap

O roadmap organiza dependências e critérios de saída. Ele não autoriza conta real, não substitui o
PRD e não transforma itens futuros em compromisso de data. O estado factual mais recente permanece
no [WORKLOG.md](WORKLOG.md).

Uma fatia só avança quando possui:

- modelos/fronteiras explícitos;
- testes reproduzíveis;
- falhas fechadas e reason codes estáveis;
- documentação e rastreabilidade;
- scanner de segredos;
- riscos residuais registrados.

## 2. Fase 0 — Fundação resiliente local

### 2.1 Concluído

- domínio financeiro imutável e dinheiro sem `float`;
- Single Core Instance e Single Database Writer;
- SQLite crítico com migrações/checksums/integridade/backup;
- TradeIntent + RiskReservation + Outbox em uma transação;
- worker financeiro simulado em subprocesso e IPC v1;
- delivery certainty, `UNKNOWN`, exposição conservadora e reconciliação;
- lifecycle de ordem até settlement com eventos idempotentes;
- Auth Agent/PKCE/device/lease simulados;
- Strategy Catalog/Runtime/Arbiter/Allocator/Risk local;
- candle fechado, journal, checkpoint e replay determinístico;
- Deriv Worker read-only com transporte fake padrão;
- scheduler/backfill/Health Gate/shadow/reconnect;
- stream compartilhado, broker session, host e soak bounded;
- soak temporal e matriz comparativa local;
- CLI opt-in com publicação JSON atômica e retenção FIFO bounded;
- perfis `fast/standard/extended/chaos` e fault presets determinísticos;
- scanner automatizado de código, fixtures e relatórios de soak;
- restore drill do backup SQLite em perfil isolado com `quick_check` e `integrity_check`.

### 2.2 Encerramento formal

A Fase 0 foi encerrada formalmente em 2026-08-21 por decisão explícita do responsável, com suíte
local de 336 testes aprovada, lint, tipagem, compileall, scanner e documentação validados. O
encerramento não liberou conta real, dispatch externo ou estratégia comercial.

A decisão preservou como riscos transferidos as execuções prolongadas em outros hosts Windows,
flakes de subprocesso sob carga e os demais bloqueadores operacionais do alpha.

### 2.3 Riscos transferidos para a Fase 1

- pacote de diagnóstico local redigido;
- redução/controle dos flakes de subprocesso Windows sob carga;
- política de retenção de market data e pacote de diagnóstico;
- budget de CPU/timeout por runtime de estratégia;
- UI/launcher mínimos ainda não entram até a base operacional estar pronta.

### 2.4 Critério de saída da Fase 0 — atendido

- suíte local integral reproduzível;
- crash/restart/duplicidade/out-of-order/timeout cobertos;
- nenhum caminho externo de ordem;
- observabilidade redigida e bounded;
- documentação operacional completa;
- riscos e bloqueadores do alpha practice aprovados.

## 3. Fase 1 — Alpha practice/read-only controlado

Objetivo: provar operação prolongada e experiência operacional sem conta real.

Fatia inicial e estado:

1. launcher/supervisor Windows mínimo — implementado com lock por perfil, Job Object, IPC lifecycle
   autenticado, health polling e safe shutdown escalonado;
2. UI de health/projeção sem acesso a broker ou SQLite;
3. Auth Agent e vault Windows — subprocesso isolado, DPAPI CurrentUser, IPC autenticado por token
   efêmero/HMAC, decisão reduzida, heartbeat e restart bounded implementados;
4. Deriv demo explicitamente opt-in e read-only;
5. soak prolongado com suspensão/rede instável e pacote de diagnóstico;
6. shadow strategy em `DECISION_ONLY` com replay equivalente;
7. política de atualização ainda desabilitada até assinatura/rollback.

Gates:

- nenhuma conta real;
- nenhuma submissão Deriv externa antes de contract/recovery gates específicos;
- fechamento seguro da UI não interrompe Core/ordem;
- perda do worker exige backfill/reconciliação antes de `READY`;
- segredo permanece no processo/vault correto.

## 4. Fase 2 — Execução demo/practice externa

Somente após decisão explícita e critérios da Fase 1:

- submissão Deriv exclusivamente demo;
- IQ Option exclusivamente practice e em worker separado;
- capacidades por broker/produto;
- cotação/deadline/reconciliação reais em ambiente practice;
- circuit breaker e limites conservadores;
- testes externos sempre opt-in e fora da suíte comum;
- UX inequívoca de broker, conta, moeda e modo.

Cada broker avança de forma independente. A instabilidade IQ não pode contaminar Deriv/Core.

## 5. Fase 3 — Beta practice

- instalador onedir assinado;
- atualização assinada com health check e rollback;
- suporte/diagnóstico com consentimento e redação;
- identidade/licenciamento remoto com rotação/revogação;
- catálogo/entitlements distribuídos sem código arbitrário;
- telemetria remota somente opt-in e sem dado financeiro identificável;
- validação estatística e practice por versão de estratégia;
- piloto restrito a practice/demo.

## 6. Gate separado para qualquer modo real futuro

Modo real não é uma fase automática. Exige decisão formal e todos os critérios do PRD, incluindo:

- requisitos legais/regulatórios e regiões definidos;
- autorização explícita e UX não confundível;
- feature flag/entitlement real e lease curta;
- autenticação reforçada conforme política;
- limites de risco conservadores e kill switch de novas entradas;
- reconciliação e Health Gate comprovados em practice;
- atualização/rollback seguros;
- estratégia/versionamento com evidência aprovada;
- auditoria, suporte e resposta a incidente;
- nenhuma ambiguidade pendente durante atualização/encerramento.

Nenhuma variável de ambiente, build de desenvolvimento ou flag escondida pode contornar esse gate.

## 7. Trilhas transversais

### Segurança

Vault Windows, IPC autenticado, code signing, SBOM, scanner de dependências e threat review.

### Confiabilidade

Soak, suspensão do Windows, crash loops, disco cheio, corrupção, clock skew, jitter e recovery.

### Estratégias

Manifesto/hash/status/entitlement, replay, backtest, walk-forward e practice por versão. Nenhuma
candidata é considerada rentável por definição.

### Produto

UI operacional, acessibilidade, onboarding, suporte, retenção e políticas regionais.

### Corretoras

Deriv e IQ Option avançam com adapters/workers/testes independentes.

## 8. Dependências de decisão

- nome comercial e regiões;
- modelo de negócio;
- provedor de identidade;
- política de dispositivos/recovery de conta;
- retenção de market data e diagnóstico;
- parâmetros de risco padrão;
- política da integração IQ;
- critérios quantitativos de promoção de estratégia;
- canal de atualização e suporte;
- security contact.
