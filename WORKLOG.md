# WORKLOG — Registro de Trabalho e Decisões

**Projeto:** DualTrade Desktop — Deriv + IQ Option  
**Política:** append-only; novas informações entram como novas entradas

## 1. Como usar

Este arquivo registra o que foi decidido, produzido, validado e deixado pendente. Ele não substitui issues, commits ou documentação técnica; funciona como linha do tempo de engenharia e produto.

Regras:

- adicione entradas em ordem cronológica;
- use um identificador único `WL-AAAA-MM-DD-NN`;
- relacione requisitos do PRD quando aplicável;
- liste validações realmente executadas;
- não marque como concluído algo não testado;
- não apague decisões antigas; registre sua substituição;
- nunca inclua senha, token, cookie ou dados pessoais desnecessários.

## 2. Estado atual

| Campo | Estado |
|---|---|
| Fase | Fase 1 executável — Launcher/Job Object, UI reativa, Auth Agent isolado, Core resiliente e workers simulados/read-only |
| Produto | DualTrade Desktop para Deriv + IQ Option |
| Plataforma | Windows 10/11 64 bits |
| Execução atual | Core financeiro local, pipeline de estratégia simulado, worker financeiro sintético e Deriv read-only; nenhuma submissão real |
| Corretoras | Deriv market data pública/demo read-only em simulador por padrão; IQ ainda não implementada |
| Modo real | não autorizado / fora do MVP |
| Próximo marco | Telemetria operacional/diagnóstico bounded da UI e hardening do peer IPC; nenhuma rota real |

## 3. Decisões vigentes

| ID | Decisão | Razão |
|---|---|---|
| DEC-001 | Compartilhar Trading Core e separar workers por corretora | impedir que diferenças e falhas contaminem todo o sistema |
| DEC-002 | Core como único escritor financeiro | consistência, auditoria e recuperação |
| DEC-003 | Persistir intenção, reserva e outbox antes do envio | evitar operação sem evidência local |
| DEC-004 | Timeout de submissão gera `UNKNOWN` | exatamente uma vez não pode ser presumido |
| DEC-005 | `UNKNOWN` mantém exposição e bloqueia novas entradas | evitar duplicidade e excesso de risco |
| DEC-006 | MVP somente demo/practice | validar confiabilidade antes de dinheiro real |
| DEC-007 | Sem martingale no MVP | não mascarar qualidade da estratégia nem ampliar risco exponencial |
| DEC-008 | UI, Core e workers em processos independentes | isolamento e recuperação parcial |
| DEC-009 | Banco crítico separado de dados volumosos de mercado | proteger latência e integridade financeira |
| DEC-010 | Distribuição onedir e atualização assinada | facilitar workers independentes, diagnóstico e rollback |
| DEC-011 | Login único por e-mail e código | reduzir fricção sem expor tokens/IDs ao cliente |
| DEC-012 | `user_id`, tokens, dispositivo e lease são internos | separar identidade, sessão e licenciamento |
| DEC-013 | Broker credentials nunca vão ao serviço de identidade | limitar impacto de incidente remoto |
| DEC-014 | Catálogo versionado e assinado de estratégias | controlar compatibilidade, integridade e entitlement |
| DEC-015 | Signal Arbiter cancela conflitos no MVP | impedir entradas contraditórias ou stake duplicada |
| DEC-016 | Estratégia só é liberada após gates de validação | trocar quantidade por evidência reproduzível |
| DEC-017 | UX de autenticação do produto será somente e-mail + código de seis dígitos | reduzir fricção sem expor IDs, tokens ou chaves ao cliente |
| DEC-018 | Desktop é cliente público com PKCE, tokens rotativos e dispositivo criptográfico aleatório | evitar segredo embutido e hardware fingerprint como autenticação |
| DEC-019 | Lease assinada permite offline controlado: até 7 dias practice e até 24 horas real | manter disponibilidade sem transformar identidade em servidor de trading |
| DEC-020 | Expiração/revogação bloqueia novas entradas, nunca acompanhamento de ordens abertas | preservar segurança financeira e reconciliação |
| DEC-021 | Deriv comercial prefere OAuth; credencial/sessão IQ permanece local no IQ Worker | separar contas externas da identidade DualTrade |
| DEC-022 | Strategy Platform usa manifesto, ciclo de vida, Arbiter e Allocator antes do Risk Ledger | impedir incompatibilidade, conflitos e exposição duplicada |
| DEC-023 | Três arquétipos iniciais são candidatas, não estratégias comprovadas | validar tendência, reversão lateral e expansão de volatilidade sem promessa de resultado |
| DEC-024 | Evidência de estratégia usa `strategy_data.db` separado e append-only | isolar candles/replay/checkpoints do estado financeiro e permitir restore auditável |
| DEC-025 | Decisões e checkpoint de um candle formam uma única unidade SQLite | impedir decisão parcial e tornar o restart determinístico nos dois lados do commit |
| DEC-026 | Ingresso Deriv começa por histórico síncrono limitado via IPC | obter candle fechado auditável sem nova fila, retry oculto ou assinatura externa obrigatória |
| DEC-027 | Market data só alcança estratégia após scheduler monotônico, recovery com overlap e Health Gate por série; o modo permanece `DECISION_ONLY` | impedir decisão sobre gap/backpressure/reconnect incompleto e tornar shadow/replay/crash equivalentes sem abrir execução financeira |
| DEC-028 | Histórico e stream live convergem no mesmo ingress; subscription só volta após backfill da geração corrente e divergência contra replay fecha o gate | preservar determinismo, impedir entrega durante reconnect incompleto e detectar desvio live sem criar capacidade financeira |
| DEC-029 | Lifecycle shadow no Core usa o supervisor IPC existente e recovery explícito; novo cliente só assina após overlap da geração corrente | evitar restart oculto, reutilizar isolamento já provado e tornar a ordem kill → block → backfill → restore auditável |
| DEC-030 | Host shadow é caller-driven, limita ações/timeout, usa fairness e circuit breaker por série e encerra delivery ao exceder budgets CPU/RSS/lag | impedir loops e filas sem limite, isolar falhas de série e tornar consumo de recursos observável/fail-closed sem abrir execução financeira |
| DEC-031 | Um único cliente Deriv read-only pode alimentar várias séries por roteador bounded no Core | impedir competição pela fila IPC, preservar isolamento por `MarketSeriesId` e falhar fechado em backpressure/escopo desconhecido |
| DEC-032 | Sessão shadow broker-level compartilha um supervisor/cliente read-only e reinicia uma vez para restaurar todas as séries | evitar um processo por série, preservar recovery explícito e manter subscription restore dependente de health/backfill por série |
| DEC-033 | Soak broker-level bounded agrega recursos do Core e subprocesso filho, com recovery explícito limitado | observar saúde operacional read-only sem thread infinita, segredo, rota financeira ou retry de ordem |
| DEC-034 | Soak temporal usa janela monotônica, ciclos máximos, amostras bounded e relatório JSON redigido | tornar execuções prolongadas reproduzíveis e persistíveis sem reter candle bruto, credencial ou estado financeiro |
| DEC-035 | Matriz temporal executa cenários locais bounded até o fim e só passa quando todos passam | comparar cadências/falhas com evidência redigida, sem fail-fast apagar resultados nem abrir rota financeira |
| DEC-036 | Documentação usa hierarquia normativa → operacional → navegação/status, com README como índice | impedir duplicação contraditória, separar contrato implementado de plano futuro e tornar segurança/testes/runbooks encontráveis |
| DEC-037 | Relatórios de soak são publicados por temporário único no mesmo diretório, `fsync` e `os.replace`, com retenção FIFO limitada por quantidade e bytes | impedir relatório parcial, crescimento indefinido e expurgo fora do escopo `soak_matrix_*.json` |
| DEC-038 | A CLI de soak exige opt-in, usa somente cenários locais sintéticos/read-only e retorna códigos estáveis `0/1/2` | tornar execução operacional explícita e auditável sem conta, credencial, rede, ordem ou capacidade financeira |
| DEC-039 | Perfis de soak definem somente limites bounded; fault presets viram agenda determinística e eventos redigidos por ciclo | tornar falhas reproduzíveis/comparáveis sem exception bruta, relógio de parede como autoridade ou caminho financeiro |
| DEC-040 | Scanner local retorna categoria/localização/metadados, nunca o trecho nem hash derivado do segredo, e bloqueia relatório antes da publicação | reduzir vazamento secundário, inclusive para OTP de baixa entropia, e falhar fechado em artefato sensível |
| DEC-041 | Restore permanece ensaio isolado sobre cópia do backup publicado, com marker e checks SQLite, sem comando automático sobre o perfil original | provar recuperabilidade sem ampliar autoridade de escrita nem arriscar a única evidência financeira |
| DEC-042 | A Fase 0 foi encerrada formalmente em 2026-08-21 e seus riscos operacionais foram transferidos, sem autorizar conta real ou dispatch externo | iniciar a Fase 1 por decisão explícita preservando os mesmos guardrails financeiros |
| DEC-043 | Segredos do Auth Agent no Windows usam DPAPI CurrentUser, entropia por chave, integridade interna/externa, DACL protegida por SID e replace atômico | cumprir FR-095/R-AUTH-005 sem criar segredo mestre, arquivo plaintext ou escopo de máquina |
| DEC-044 | Simulação do vault é somente explícita ou não Windows; falha de DPAPI, ACL, integridade ou I/O no Windows propaga reason code e nunca seleciona fallback | impedir continuidade aparente sem comprovação de proteção local |
| DEC-045 | Auth Agent executa em subprocesso e autentica o IPC por token efêmero entregue via stdin + prova HMAC sobre nonces | isolar sessão/device/lease e impedir conexão loopback sem posse da capability de spawn |
| DEC-046 | Core financeiro recebe apenas allow/block, reason code e expiração; indisponibilidade do Auth Agent vira `HG_AUTH_AGENT_UNAVAILABLE` somente para novas entradas | preservar AG-INV-011 e impedir que falha de identidade interrompa evento, reconciliação ou settlement |
| DEC-047 | O simulador persiste somente seu conjunto bounded de chaves públicas de verificação no vault; signing key fake é efêmera e confinada ao subprocesso | permitir restore offline da lease após kill sem confiar chave privada de assinatura ao Core ou executável |
| DEC-048 | Launcher possui apenas lock, Job Object e lifecycle; Core continua dono do banco e dos supervisores IPC dos workers | impedir autoridade financeira duplicada e preservar recovery/reconciliação no único processo correto |
| DEC-049 | Shutdown segue safe stop → drain bounded → workers → Auth → Core, com ACK → terminate → kill | persistir eventos já aceitos, não esperar settlement futuro e eliminar órfãos sem inferir estado financeiro |
| DEC-050 | Restart do Launcher é permitido somente para Auth Agent e Deriv read-only; kill do Simulated Worker exige novo Core/recovery | impedir troca de uma porta financeira ativa sem reconstruir coordenadores e reconciliar a geração anterior |

## 4. Artefatos existentes

| Arquivo | Finalidade | Estado |
|---|---|---|
| `PRD_Trading_Desktop_Deriv_IQOption.md` | requisitos e escopo do produto | v1 criado |
| `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md` | desenho técnico resiliente | v1 criado |
| `AIGUARD.md` | limites para IA e automações | v1 criado |
| `RULES.md` | normas obrigatórias de arquitetura/código | v1 criado |
| `AGENTS.md` | instruções operacionais para agentes | v1 criado |
| `WORKLOG.md` | registro cumulativo | v1 criado |
| `BRIEFING.md` | visão executiva e decisões centrais | v1.1 criado |
| `AUTHENTICATION_AND_LICENSING.md` | identidade, dispositivos, tokens e leases | v1 criado |
| `STRATEGY_PLATFORM.md` | catálogo, runtime, arbitragem e validação | v1 criado |
| `SECURITY.md` | modelo de ameaças e controles | v1 criado |
| `ROADMAP.md` | fases, marcos e critérios de saída | v1 criado |
| `TEST_PLAN.md` | estratégia de validação e caos | v1 criado |
| `README.md` | índice mestre do pacote | v1 criado |

## 5. Backlog imediato

| Prioridade | Item | Saída esperada |
|---:|---|---|
| P0 | Criar `pyproject.toml` e estrutura mínima | projeto instalável, lint e testes locais |
| P0 | Implementar modelos de identidade/licença simulados | `user_id`, device key, sessão, entitlement e lease verificável sem segredo real |
| P0 | Implementar Auth Agent e servidor fake | fluxo e-mail/código + PKCE simulado, token vault e renovação/expiração |
| P0 | Implementar Strategy Catalog/manifesto | versão, hash, compatibilidade, lifecycle e Validation Registry |
| P0 | Implementar Signal Arbiter/Portfolio Allocator | conflitos, deduplicação de stake e orçamento antes do Risk Ledger |
| P0 | Implementar modelos do domínio | estados, mensagens, dinheiro e identificadores |
| P0 | Implementar máquinas de estado | sessão e ordem com transições testadas |
| P0 | Implementar Risk Ledger em memória | reservas atômicas e testes de concorrência |
| P0 | Definir protocolo IPC v1 | envelope, framing, handshake e erros |
| P0 | Criar worker simulado | cenários de aceite, rejeição, timeout e crash |
| P0 | Implementar persistência inicial | schema, single writer, outbox e migrações |
| P1 | Criar UI mínima de saúde | projeções do Core sem acesso direto ao banco |
| P1 | Integrar Deriv demo | contract tests e reconciliação |
| P1 | Integrar IQ Option practice | worker isolado e circuit breaker |

## 6. Bloqueadores e questões abertas

- nome comercial definitivo;
- regiões de distribuição;
- modelo de negócio;
- política de armazenamento de credenciais IQ;
- parâmetros/presets e critérios quantitativos finais das três candidatas iniciais;
- limites de risco padrão;
- período mínimo de practice antes de piloto real;
- política de suporte para quebra da integração IQ;
- retenção de dados de mercado;
- canal de atualização.
- provedor de identidade gerenciado definitivo;
- limites de dispositivos por plano;
- parâmetros operacionais finais das leases dentro dos tetos definidos (practice até 7 dias; real até 24 horas);
- política estatística de promoção de estratégias.

## 7. Entradas

### WL-2026-08-19-01 — Análise inicial da proposta

**Objetivo:** avaliar viabilidade de um bot desktop local para Deriv e IQ Option.  
**Resultado:** decidiu-se manter execução no computador do cliente e tratar as integrações separadamente.  
**Decisões relacionadas:** DEC-001, DEC-006.  
**Riscos identificados:** API não oficial IQ, credenciais locais, reconexão, ordens ambíguas e distribuição.  
**Validação:** análise conceitual; nenhum código executado.  
**Próximo passo:** desenhar arquitetura resiliente.

### WL-2026-08-19-02 — Arquitetura resiliente v1

**Objetivo:** criar uma arquitetura capaz de falhar de forma controlada.  
**Arquivos:** `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`.  
**Resultado:** definidos Core único, workers isolados, journal, outbox, Risk Ledger, Health Gate, reconciliação e estados `UNKNOWN`.  
**Decisões relacionadas:** DEC-001 a DEC-010.  
**Validação:** estrutura Markdown e consistência documental verificadas.  
**Limitação:** arquitetura ainda não possui implementação executável.  
**Próximo passo:** transformar arquitetura em PRD.

### WL-2026-08-19-03 — PRD v1

**Objetivo:** definir produto, escopo, requisitos e critérios de liberação.  
**Arquivos:** `PRD_Trading_Desktop_Deriv_IQOption.md`.  
**Resultado:** MVP definido para Deriv demo e IQ practice, uma estratégia inicial, risco conservador e sem martingale.  
**Validação:** requisitos, regras, rastreabilidade e Markdown verificados.  
**Limitação:** questões comerciais, jurídicas e parâmetros finais permanecem abertas.  
**Próximo passo:** estabelecer governança do repositório.

### WL-2026-08-20-01 — Governança para desenvolvimento assistido

**Objetivo:** criar guardrails, regras e instruções operacionais antes do código.  
**Arquivos:** `AIGUARD.md`, `RULES.md`, `AGENTS.md`, `WORKLOG.md`.  
**Resultado:** invariantes financeiros protegidos, responsabilidades de processos fixadas e fluxo de trabalho documentado.  
**Requisitos relacionados:** FR-045, FR-052, FR-054, FR-061; NFR-001 a NFR-005; NFR-030 a NFR-043.  
**Validação:** arquivos presentes e não vazios; blocos Markdown balanceados; referências cruzadas ao PRD e à arquitetura verificadas.  
**Próximo passo:** criar scaffolding da Fase 0.

### WL-2026-08-20-02 — Identidade do cliente e plataforma multi-estratégias

**Objetivo:** incorporar autenticação simples para o cliente e governança completa de estratégias.  
**Requisitos relacionados:** FR-090 a FR-111.  
**Arquivos alterados:** PRD, arquitetura, AIGUARD, RULES, AGENTS, WORKLOG e novos documentos especializados.  
**Decisões:** login único por e-mail/código; device key e lease assinada internos; credenciais de broker separadas; catálogo versionado; arbitragem antes do risco.  
**Validação executada:** revisão documental cruzada e verificação estrutural do pacote.  
**Resultado:** documentação v1.1 preparada para orientar implementação.  
**Riscos/limitações:** provedor de identidade, parâmetros de lease e gates estatísticos finais ainda precisam de decisão.  
**Próximo passo:** implementar scaffolding de domínio, auth simulado e strategy catalog simulado.

### WL-2026-08-20-03 — Consolidação da nova implementação de identidade e estratégias

**Objetivo:** alinhar os documentos centrais à implementação definida para autenticação simples do cliente, dispositivo/licença offline e plataforma multi-estratégias.  
**Requisitos relacionados:** FR-090 a FR-111; NFR-035 a NFR-038; NFR-044.  
**Arquivos alterados:** `PRD_Trading_Desktop_Deriv_IQOption.md`, `RULES.md`, `AIGUARD.md`, `AGENTS.md`, `WORKLOG.md`.  
**Implementação documental:** login visível por e-mail + código de seis dígitos; `user_id` interno; cliente público/PKCE; tokens rotativos; `device_id` aleatório com chave própria; proteção no escopo do usuário do Windows; lease assinada com offline controlado; separação completa entre identidade DualTrade e credenciais de broker; Strategy Catalog, Runtime isolado, Signal Arbiter, Portfolio Allocator, Validation Registry e lifecycle de release.  
**Decisões:** DEC-017 a DEC-023; sinais opostos cancelam a entrada no MVP; sinais iguais não somam stake; três arquétipos são apenas candidatas até validação.  
**Validação executada:** revisão cruzada de IDs/regras, checagem estrutural Markdown, unicidade dos requisitos adicionados e verificação de que o estado do projeto continua declarando ausência de código de trading implementado.  
**Resultado:** documentação central alinhada à nova implementação e rastreabilidade `FR-090` a `FR-111` materializada no PRD.  
**Riscos/limitações:** não foram fornecidos nesta atualização os documentos especializados `AUTHENTICATION_AND_LICENSING.md`, `STRATEGY_PLATFORM.md`, `SECURITY.md`, `TEST_PLAN.md` nem a arquitetura relacionada; eles devem ser sincronizados separadamente antes de a documentação ser tratada como pacote integralmente fechado. Nenhum código executável foi validado.  
**Próximo passo:** criar scaffolding da Fase 0 com auth/licensing e strategy platform simulados, seguido dos testes de contrato, expiração/revogação e arbitragem.

### WL-2026-08-20-04 — Persistência SQLite, writer único e outbox durável

**Objetivo:** implementar a primeira fatia executável da Fase 0 para persistência financeira local, dispatch simulado e recuperação conservadora após falha.  
**Requisitos relacionados:** FR-044 a FR-046, FR-052 a FR-054, FR-061, FR-080; NFR-001 a NFR-005, NFR-042; BR-003, BR-004 e BR-009; R-STATE-001 a R-STATE-006, R-ORD-001 a R-ORD-005, R-RISK-001 e R-RISK-002, R-DB-001 a R-DB-005; AG-INV-001 a AG-INV-005.  
**Arquivos alterados:** `pyproject.toml`; contratos em `packages/domain/`; banco, migração, reader, writer e unit of work em `packages/persistence/`; Health Gate, Risk Ledger mínimo, coordenador, dispatcher e recovery em `apps/core/`; worker exclusivamente simulado em `apps/simulated_worker/`; testes em `tests/unit/` e `tests/integration/`; `WORKLOG.md`.  
**Implementação:** modelos imutáveis e enums explícitos; dinheiro em minor units inteiros com moeda; SQLite com `foreign_keys=ON`, WAL, `synchronous=FULL` e `busy_timeout=5000`; migração imutável `0001_initial_state` com checksum; transação única para `TradeIntent`, `RiskReservation`, `OutboxMessage` e projeção inicial de ordem; índice parcial único para uma reserva ativa por broker/conta; claim transacional da outbox; estados `PENDING`, `DISPATCHING`, `DISPATCHED`, `AMBIGUOUS` e `CANCELLED`; timeout/exceção após possível envio convertidos em `UNKNOWN` sem retry; reserva conservada; reconciliação explícita exige evidência e estado terminal; eventos idempotentes e máquina de estados sem regressão terminal; recuperação converte claim interrompido em ambiguidade e fecha o Health Gate.  
**Decisões:** o Trading Core é o único dono da escrita; `StateReader` usa conexão `query_only`; a serialização por conta combina lock por chave `(broker, account_id)` com constraint durável no banco; `dispatched_at` permanece nulo sem confirmação; qualquer exceção após invocar a fronteira do worker é tratada conservadoramente como possível envio. Nenhuma integração real, rede, credencial ou modo real foi adicionada.  
**Validação executada:** `python -m pytest` — 17 testes aprovados; dois testes concorrentes repetidos 10 vezes — 20 execuções aprovadas; `python -m compileall apps packages` — aprovado; `python -m ruff check .` — aprovado; `python -m ruff format --check .` — aprovado. `mypy` não foi executado porque o módulo não está instalado. Scanner manual de segredos e inspeção de uso de `float` executados sobre código/configuração, sem achados.  
**Resultado:** a suíte demonstra que nenhuma ordem simulada chega ao worker antes do commit local, falhas antes do commit não deixam registros parciais, mensagens persistidas sobrevivem a restart e ambiguidade nunca retorna automaticamente a `PENDING` nem libera exposição.  
**Riscos/limitações:** o repositório não possui controle Git nem o arquivo `PROMPT_MESTRE_DESENVOLVIMENTO.md`; o writer único é uma fronteira lógica no processo e ainda não possui exclusão entre dois processos Core; testes de corrupção, disco cheio real, interrupção abrupta de processo/WAL e backup consistente permanecem pendentes; o Recovery Coordinator classifica estado local, mas ainda não reconcilia com contrato de broker; tipagem estrita aguarda instalação do `mypy`.  
**Próximo passo:** implementar a fatia de robustez do armazenamento com exclusão de segunda instância do Core, verificação de integridade/backup SQLite e testes de crash em subprocesso, corrupção e disco cheio simulado.

### WL-2026-08-20-05 — Storage resiliente, Core único e crash recovery real

**Objetivo:** endurecer a persistência da Fase 0 contra concorrência entre processos, corrupção, falha de escrita e morte abrupta do Core, preservando commits comprovados e mantendo ambiguidade sem retry.  
**Requisitos relacionados:** FR-002, FR-044 a FR-046, FR-052 a FR-054, FR-061 a FR-063, FR-080; NFR-001 a NFR-005, NFR-021, NFR-023, NFR-042; BR-003, BR-004, BR-009; R-ARCH-002, R-STATE-003 a R-STATE-006, R-ORD-001 a R-ORD-005, R-DB-001 a R-DB-008, R-TEST-001, R-TEST-004 e R-TEST-006.  
**Arquivos alterados:** `pyproject.toml`, `AGENTS.md`, `WORKLOG.md`; `apps/core/health.py`, `apps/core/instance.py`, `apps/core/runtime.py`, `apps/core/recovery.py`, `apps/core/risk.py`, `apps/core/coordinator.py` e exports; `packages/persistence/database.py`, `health.py`, `backup.py`, `migrations.py`, `writer.py` e exports; `packages/observability/`; `packages/domain/models.py`; helpers e testes em `tests/chaos/`, `tests/helpers/`, `tests/integration/` e `tests/unit/`.  
**Implementação:** `CoreInstanceGuard` com lock de arquivo mantido pelo sistema operacional e liberado após morte do processo; `CoreRuntime` como dono do guard, Database Health, writer, recovery, exposição restaurada, dispatcher e shutdown; estados `HEALTHY/DEGRADED/FAILED` e códigos estáveis de storage; marker `state.db.expected` para detectar banco esperado ausente; `quick_check` no startup e `integrity_check` em diagnóstico/backup; migration imutável `0002_outbox_state_reason`; checksum e rollback transacional de migration; cancelamento de `PENDING` expirado sem reclassificar `DISPATCHING`; Backup API do SQLite com snapshot temporário, full integrity check e publicação atômica; eventos operacionais estruturados sem payload financeiro bruto; dependências de desenvolvimento fixadas e `mypy` estrito.  
**Decisões:** segunda instância é recusada antes de abrir/migrar o banco; arquivo de lock não representa ownership por existência, somente pelo lock do SO; startup usa `quick_check` de baixo custo e reserva `integrity_check` completo para diagnóstico/backup; backup nunca substitui automaticamente o banco principal; `DISPATCHING` encontrado após crash vira `AMBIGUOUS/UNKNOWN`, nunca `PENDING`; dispatcher permanece acionado explicitamente pelo Core nesta fase, sem thread automática; banco existente sem marker é aceito como migração de instalação anterior e passa a receber o marker após validação.  
**Validação executada:** `python -m pytest` — 41 testes aprovados; suíte de seis testes de subprocesso/kill repetida três vezes — 18 execuções aprovadas; `python -m ruff check .` — aprovado; `python -m ruff format --check .` — aprovado; `python -m mypy apps packages` — sucesso sem issues em 22 arquivos; `python -m compileall apps packages` — aprovado. Scanner de código executável sem achados para `float`, credenciais, conta real, martingale, `pickle`, retry genérico financeiro ou SDK de broker no Core.  
**Resultado:** morte abrupta antes do commit não produz estado financeiro; commit no WAL sobrevive ao kill; claim interrompido é recuperado como ambíguo com reserva ativa e Health Gate bloqueado; aceite persistido não regride; segunda instância não alcança banco/dispatcher; corrupção, ausência inesperada, checksum divergente, migration failure e write failure falham fechados; backups ativos preservam migrations, intenção, ordem, outbox e exposição.  
**Riscos/limitações:** testes exercitam `TerminateProcess` no Windows, mas não simulam perda física de energia/cache de hardware; se banco e marker forem removidos juntos, não há metadata externa suficiente para distinguir perda total de uma primeira execução; backup é ferramenta de proteção/diagnóstico e não existe restore automático; exclusão depende da semântica de lock do filesystem local e deve ser revalidada no instalador/perfil definitivo; o dispatcher ainda não é um loop assíncrono e a reconciliação externa permanece simulada.  
**Próximo passo:** implementar o protocolo IPC v1 e mover o worker simulado para subprocesso isolado, com handshake de versão, framing limitado, deadlines e contract tests sem integração real de corretora.

### WL-2026-08-20-06 — IPC v1 e worker simulado isolado

**Objetivo:** substituir a chamada direta usada pelo runtime por um contrato IPC versionado com
worker simulado em subprocesso real, preservando outbox, autoridade financeira do Core e
classificação conservadora de falhas.
**Requisitos relacionados:** FR-012, FR-022, FR-024, FR-051 a FR-054, FR-056, FR-060 a FR-062 e
FR-080; NFR-001 a NFR-004, NFR-012, NFR-020, NFR-040 e NFR-041; R-ARCH-001 a R-ARCH-003,
R-ARCH-007, R-ARCH-008, R-STATE-001 a R-STATE-008, R-ORD-001 a R-ORD-005, R-BRK-002,
R-BRK-007, R-BRK-008, R-DATA-002, R-DATA-007, R-SEC-003, R-TEST-002, R-TEST-004 e R-TEST-005.
**Arquivos alterados:** contratos em `packages/protocol/`; `OrderCommand` e estados explícitos em
`packages/domain/`; integração e lifecycle em `apps/core/`; servidor/cenários em
`apps/simulated_worker/`; compatibilidade da outbox em `packages/persistence/`; testes em
`tests/unit/`, `tests/contract/`, `tests/integration/` e `tests/chaos/`; `docs/IPC_PROTOCOL_V1.md`,
`AGENTS.md` e `WORKLOG.md`.
**Implementação:** TCP somente em `127.0.0.1`, porta dinâmica escolhida pelo SO, frame com comprimento
big-endian de quatro bytes e JSON UTF-8 limitado a 64 KiB; envelope v1 imutável; handshake e
capabilities practice; mensagens de ordem, heartbeat, health e shutdown; validação em camadas;
roteamento por correlation/causation; replay cache limitado; fila de eventos limitada e fail closed;
supervisor com subprocesso, monitor, backoff, circuit breaker e shutdown escalonado; dispatcher
contra `WorkerPort`; classificação `NOT_SENT`, `POSSIBLY_SENT` e `RESPONSE_RECEIVED`.
**Decisões:** `NOT_SENT` fica bloqueado sem retry em `BLOCKED_NOT_SENT/SEND_BLOCKED` e mantém reserva;
qualquer falha durante/depois de `sendall` é potencialmente parcial e vira `AMBIGUOUS/UNKNOWN`;
heartbeat não resolve ordem; restart exige novo handshake e nunca reenvia item ambíguo; o Core
continua único escritor financeiro. Payload legado anterior ao IPC v1 recupera `order_id` pela
projeção durável de ordem.
**Validação executada:** `python -m pytest` — 73 testes aprovados; testes críticos de handshake,
crash após possível envio, kill abrupto e restart repetidos três vezes; `python -m ruff check .`,
`python -m ruff format --check .`, `python -m mypy apps packages` e
`python -m compileall apps packages` aprovados. Scanner manual de código executável sem achados
proibidos.
**Resultado:** Core e worker executam em processos distintos; kill do worker não mata o Core;
aceite/rejeição comprovados são persistidos; timeout/crash em região incerta preserva exposição;
mensagens malformadas não chegam ao domínio; segunda instância de worker não compartilha transporte
nem estado; o subprocesso não recebe caminho do `state.db`.
**Riscos/limitações:** TCP loopback ainda não autentica o peer por identidade de usuário Windows; a
seleção de porta tem posse segura pelo Core, mas o protocolo não possui criptografia por ser local;
replay cache do transporte é limitado e em memória, enquanto idempotência financeira durável
continua no writer; reconciliação pós-restart é simulada; não há market data nem integração externa;
os testes não simulam perda física de energia durante tráfego IPC.
**Próximo passo:** implementar reconciliação simulada por consulta de status após restart, sem
reenvio, e usar essa evidência para resolver `UNKNOWN` apenas quando comprovado.

### WL-2026-08-20-07 — Reconciliação por evidência sem reenvio

**Objetivo:** resolver ordens `UNKNOWN` e `SETTLEMENT_UNKNOWN` somente por consulta de status
read-only ao worker simulado, com evidência durável, matching estrito e atualização financeira
atômica no Core.
**Requisitos relacionados:** FR-046, FR-054, FR-055, FR-060 a FR-063 e FR-080; NFR-001,
NFR-003, NFR-004, NFR-020 e NFR-021; R-STATE-003 a R-STATE-008, R-ORD-001 a R-ORD-005,
R-RISK-001, R-RISK-002, R-DB-001 a R-DB-008, R-BRK-002, R-BRK-007, R-BRK-008,
R-DATA-002, R-DATA-007, R-TEST-002, R-TEST-004 e R-TEST-006.
**Arquivos alterados:** modelos e exports em `packages/domain/`; mensagens, capability e erros em
`packages/protocol/`; migration `0003_reconciliation`, reader, writer e exports em
`packages/persistence/`; coordinator de reconciliação, runtime, Health Gate, portas do worker e
supervisor em `apps/core/`; store externo sintético, cenários, servidor e entry point em
`apps/simulated_worker/`; testes unitários, de contrato, integração e chaos em `tests/`;
`docs/IPC_PROTOCOL_V1.md` e `WORKLOG.md`.
**Implementação:** `ORDER_STATUS_REQUEST/RESPONSE` e `supports_order_status_query`; store SQLite
separado e durável sob autoridade exclusiva do worker simulado; cenários de aceite, rejeição,
liquidação e liquidação desconhecida com perda de resposta; `ReconciliationCoordinator` entre
Recovery e `OrderStatusPort`; retries limitados somente para consultas; tabelas duráveis de
tentativas/evidências e proveniência na ordem; matching de referência, broker, conta, produto,
símbolo, direção, minor units, moeda e broker order ID quando conhecido; commit único para
evidência, tentativa, ordem, outbox, reserva e P&L; outbox reconciliada em estado terminal
`RECONCILED`, sem regressão para `PENDING`.
**Decisões:** `NOT_FOUND`, timeout, indisponibilidade, payload inválido e passagem do tempo não
resolvem ambiguidade; evidência conflitante exige revisão manual e preserva exposição; evidência
idêntica é idempotente; `REJECTED` e `SETTLED` liberam a reserva uma vez, enquanto `ACCEPTED`,
`OPEN` e `SETTLEMENT_UNKNOWN` mantêm exposição conforme o estado; o Core nunca abre o store externo
e o coordinator de reconciliação não possui API de submissão.
**Validação executada:** `python -m pytest` — 96 testes aprovados; quatro cenários críticos
(aceite, rejeição, liquidação e liquidação desconhecida após perda de resposta) repetidos três
vezes — 12 execuções aprovadas; prova de zero reenvio repetida três vezes com
`submit_count=1`, `status_query_count=3`, `attempt_count=1` e nenhuma outbox `PENDING`;
`python -m compileall apps packages`, `python -m ruff check apps packages tests`,
`python -m ruff format --check apps packages tests` e `python -m mypy apps packages` aprovados.
Scanner manual não encontrou segredo atribuído, credencial, acesso do Core ao
`SimulatedBrokerStore`, chamada de submit pelo coordinator de reconciliação nem `float` nas
fronteiras financeiras inspecionadas.
**Resultado:** perda de resposta deixa a ordem `UNKNOWN` com exposição ativa; restart do worker,
do Core ou de ambos consulta o estado externo sem reenviar; evidência suficiente resolve de forma
atômica; kill antes do commit mantém toda a ambiguidade e kill depois do commit preserva toda a
resolução.
**Riscos/limitações:** o store representa apenas um broker sintético; não há semântica real de
consulta Deriv/IQ Option, autenticação do peer IPC ou UI operacional para revisão manual; testes de
kill não reproduzem perda física de energia/cache; `SETTLEMENT_UNKNOWN` continua exigindo nova
evidência futura e bloqueia novas entradas.
**Próximo passo:** implementar o acompanhamento simulado de ordens aceitas até liquidação por
eventos normalizados, com replay idempotente e fallback para a consulta read-only já criada.

### WL-2026-08-20-08 — Event stream durável e lifecycle simulado até liquidação

**Objetivo:** acompanhar ordens aceitas por eventos assíncronos normalizados até `OPEN`,
`SETTLED` ou `SETTLEMENT_UNKNOWN`, com inbox durável, efeitos financeiros atômicos e fallback
read-only para reconciliação quando a entrega não é comprovada.
**Requisitos relacionados:** FR-055, FR-056, FR-060 a FR-062 e FR-080; NFR-001 a NFR-004,
NFR-010, NFR-012, NFR-020, NFR-021 e NFR-042; R-STATE-001 a R-STATE-008, R-ORD-004 a
R-ORD-008, R-RISK-002, R-RISK-004, R-DATA-001, R-DATA-002, R-DATA-007, R-DB-001 a
R-DB-008, R-TEST-001, R-TEST-002, R-TEST-004 e R-TEST-005.
**Arquivos alterados:** modelos e exports em `packages/domain/`; `ORDER_EVENT`, capability,
parser e erros estáveis em `packages/protocol/`; migration `0004_broker_order_events`, reader,
writer e exports em `packages/persistence/`; processor/pump de eventos, Health Gate por conta,
runtime, reconciliação, supervisor e cliente IPC em `apps/core/`; store externo, cenários e servidor
em `apps/simulated_worker/`; testes em `tests/integration/`, `tests/chaos/` e `tests/helpers/`;
`docs/IPC_PROTOCOL_V1.md` e `WORKLOG.md`.
**Implementação:** `BrokerOrderEvent` imutável com evidence hash canônico; entrega IPC unsolicited
por fila limitada separada das respostas; tabela/inbox durável com `event_id` único; matching
estrito de escopo e identidade; sequência externa e detecção de gap; transação única para inbox,
ordem, proveniência, P&L e liberação; contadores duráveis de aplicação/liberação; lifecycle externo
sintético separado de seu estado de entrega; cenários de duplicidade, reordenação, perda,
settlement desconhecido e crash; reconciliação de `ACCEPTED`/`OPEN` no startup e fallback por
consulta de status sem submit; bloqueios de evento isolados por broker/conta.
**Decisões:** o Core e seu single writer permanecem a única autoridade financeira; worker apenas
traduz e mantém a verdade externa simulada; heartbeat não consome a fila financeira nem infere
resultado; saturação fecha o Health Gate; replay idêntico não repete efeitos e replay conflitante
é persistido e bloqueia a conta; evento tardio não regride estado terminal; `UNKNOWN`, `OPEN` e
`SETTLEMENT_UNKNOWN` conservam exposição até evidência suficiente; nenhum retry de ordem foi
adicionado.
**Validação executada:** `python -m pytest -q` — 120 testes aprovados; casos EVT-01 a EVT-25,
incluindo três repetições parametrizadas do lifecycle normal e do fallback por gap; worker caindo
com ordem `OPEN`, tempestade de 100 settlements duplicados e crash real do Core antes/depois do
commit repetidos três vezes — 12 execuções aprovadas; settlement perdido com restart conjunto e
reconciliação repetido três vezes; `python -m compileall apps packages`,
`python -m ruff check apps packages tests`, `python -m ruff format --check apps packages tests` e
`python -m mypy apps packages` aprovados. Scanner manual não encontrou atribuição de segredo,
integração real, SDK de broker, modo real, martingale, serialização insegura, submit/status store no
processor ou `float` em valor financeiro; usos de `float` encontrados limitam-se a durações e
backoff.
**Resultado:** a prova normal termina com `submit_count=1`, outbox `attempt_count=1`, três eventos
externos entregues, `pnl_application_count=1` e `release_count=1`; a prova de fallback termina com
os mesmos efeitos únicos, `status_query_count>0` e nenhum novo submit. Frame truncado não altera o
domínio, duplicidade massiva não duplica P&L, kill antes do commit não deixa inbox/efeito parcial e
kill depois do commit preserva um único efeito completo; crash durante a escrita envia apenas um
prefixo do frame de settlement e não cria inbox nem efeito financeiro parcial.
**Riscos/limitações:** o broker continua integralmente sintético e practice-only; TCP loopback não
autentica o peer por identidade Windows; detecção de gap depende de sequência quando o provedor a
oferece; fila cheia exige reconciliação após reconexão e não preserva em memória o frame não
enfileirado; testes de kill não equivalem à perda física de energia; não há UI de revisão manual,
market data, Deriv, IQ Option, conta real ou modo real.
**Próximo passo:** implementar Auth Agent e signed Offline Lease simulados da Fase 0, fazendo
expiração/revogação bloquear apenas novas entradas e preservando acompanhamento/reconciliação de
ordens abertas.

### WL-2026-08-20-09 — Deriv Worker read-only para market data e demo opt-in

**Objetivo:** implementar a fatia Deriv read-only da Fase 0 para dados públicos de mercado,
catálogo/contratos/relógio, assinatura simulada, reconexão e arquitetura de sessão demo opt-in,
sem qualquer submissão real.
**Requisitos relacionados:** FR-010 parcialmente para arquitetura demo read-only, FR-012, FR-020 a
FR-023, FR-060 e FR-080; NFR-001 a NFR-004, NFR-012, NFR-020, NFR-040 e NFR-041; R-BRK-001,
R-BRK-002, R-BRK-004, R-BRK-005, R-BRK-007, R-BRK-008, R-DATA-001, R-DATA-002, R-DATA-007,
R-SEC-003, R-TEST-002, R-TEST-004 e R-TEST-005.
**Arquivos alterados:** `pyproject.toml`; modelos em `packages/domain/market.py`; protocolo em
`packages/protocol/envelope.py`, `packages/protocol/errors.py`, `packages/protocol/messages.py` e
exports; cliente/supervisores em `apps/core/worker_client.py`,
`apps/core/read_only_worker_supervisor.py` e `apps/core/worker_supervisor.py`; worker Deriv em
`apps/deriv_worker/`; testes em
`tests/unit/test_deriv_market_data.py`, `tests/contract/test_deriv_worker_contract.py` e
`tests/integration/test_reconciliation_protocol.py`,
`tests/external/test_deriv_public_external.py`; `docs/DERIV_WORKER.md`,
`docs/IPC_PROTOCOL_V1.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** transporte Deriv com URL/host/path TLS estritos; allowlist de operações
read-only e denylist de opcodes de trading antes da rede; parser JSON sem números não finitos;
mappers para active symbols, contracts, ticks, tick history/candles e server time; modelos
imutáveis com `Decimal`; health de market data `HEALTHY/WARMING_UP/STALE/GAPPED/DISCONNECTED/
INCOMPATIBLE`; fila limitada de ticks, detecção de duplicidade, atraso, gap, sobrecarga e stale;
reconnect com backoff/jitter injetável e restauração de assinatura lógica; subprocesso Deriv IPC v1
com `can_submit_orders=false`; sessão demo REST/OTP somente read-only, seleção explícita de conta
demo e bloqueio de conta/URL real antes do connect; timeout de handshake do supervisor simulado
ajustado para tolerar startup de subprocesso no Windows sem alterar classificação financeira.
**Decisões:** o Deriv Worker não tem API pública de `ORDER_SUBMIT`; o Core genérico recusa workers
read-only que anunciem submissão; credenciais Deriv permanecem no worker/transport e não passam
pelo serviço de identidade; CLI usa fake transport por padrão e exige `--external-public` para rede
pública; teste externo Deriv é opt-in por variável de ambiente e não há marker/caminho para real.
**Validação executada:** `python -m pytest -q` — 158 testes aprovados e 1 externo skipado por
opt-in; `python -m compileall apps packages` — aprovado; `python -m ruff check apps packages
tests` — aprovado; `python -m ruff format --check apps packages tests` — aprovado;
`python -m mypy apps packages` — sucesso em 51 arquivos; scanners manuais de segredo,
Core/imports, denylist/trading e uso de `float` executados, com ocorrências permitidas apenas em
redação/fixtures de teste, denylist, validação de URL real proibida, durações/backoff e parser
`parse_float=Decimal`.
**Resultado:** market data Deriv fica disponível ao Core por IPC normalizado e read-only; falha de
schema fecha saúde; timeout/rate limit só repetem leituras com limite; reconnect restaura
assinaturas sem decisão financeira; submissão real não foi implementada nem autorizada.
**Riscos/limitações:** o teste externo público não roda sem opt-in e rede; demo auth ainda não está
conectada à UI/Auth Agent; não há streaming assíncrono contínuo por thread dedicada no cliente real,
apenas contrato/read path e primeiro tick normalizado; suspensão real do Windows ainda não possui
detector dedicado nesta fatia.
**Próximo passo:** implementar Auth Agent e signed Offline Lease simulados, ou completar o pump
contínuo de market data Deriv antes de ligar dados ao Strategy Runtime.

### WL-2026-08-20-10 — Pump contínuo Deriv e suspensão de market data

**Objetivo:** completar o pump contínuo read-only de market data Deriv antes de qualquer ligação ao
Strategy Runtime, preservando filas limitadas, correlação de assinatura e invalidação após suspensão
local.
**Requisitos relacionados:** FR-020, FR-022, FR-023, FR-060, FR-064 e FR-080; NFR-001 a NFR-004,
NFR-012, NFR-020, NFR-040 e NFR-041; R-ARCH-001 a R-ARCH-003, R-ARCH-007, R-ARCH-008,
R-BRK-001, R-BRK-002, R-BRK-007, R-BRK-008, R-DATA-001, R-DATA-002, R-DATA-004, R-DATA-005,
R-DATA-007, R-SEC-003, R-TEST-002, R-TEST-004 e R-TEST-005.
**Arquivos alterados:** `apps/deriv_worker/websocket_client.py`,
`apps/deriv_worker/public_session.py`, `apps/deriv_worker/subscriptions.py`,
`apps/deriv_worker/fake_transport.py`, `apps/deriv_worker/server.py`,
`tests/unit/test_deriv_market_data.py`, `tests/contract/test_deriv_worker_contract.py`,
`docs/DERIV_WORKER.md`, `docs/IPC_PROTOCOL_V1.md` e `WORKLOG.md`.
**Implementação:** reader thread no transporte WSS Deriv com roteamento por `req_id`; fila limitada
para respostas pendentes e fila limitada para eventos de stream; ingestão contínua por
`SubscriptionManager`; `MARKET_TICK_EVENT` assíncrono via IPC com `causation_id` nulo e
`correlation_id` preservado da assinatura; fake transport com eventos contínuos determinísticos;
detector de gap monotônico para suspensão/retorno do Windows, marcando market data como `STALE` e
assinaturas como `RESTORING` até reconnect/backfill.
**Decisões:** o pump é exclusivo de market data e não possui API de ordem, status financeiro,
estratégia ou Risk Ledger; todo tick aceito passa pela mesma fila limitada, inclusive o primeiro da
assinatura; falha de stream ou backpressure degrada saúde em vez de alimentar estratégia com dado
duvidoso; suspensão invalida cotações e exige ressincronização read-only.
**Validação executada:** `python -m pytest -q` — 166 testes aprovados e 1 externo skipado por
opt-in; `python -m compileall apps packages` — aprovado; `python -m ruff check apps packages
tests` — aprovado; `python -m ruff format --check apps packages tests` — aprovado;
`python -m mypy apps packages` — sucesso em 51 arquivos; scanners manuais de segredo,
Core/imports, denylist/trading e uso de `float` executados, com achados permitidos apenas em docs,
redação/fixtures, denylist, validação de URL real proibida, durações/backoff e
`parse_float=Decimal`.
**Resultado:** respostas Deriv e ticks de stream agora são multiplexados sem confundir resposta de
request com evento contínuo; IPC entrega ticks contínuos normalizados ao Core read-only; suspensão
monotônica torna dados stale e força restauração/backfill antes de saúde `HEALTHY`.
**Riscos/limitações:** a detecção de suspensão é local/monotônica e não usa ainda eventos nativos do
Windows; o teste externo público permanece opt-in e não foi executado nesta validação; market data
ainda não está conectado ao Strategy Runtime; demo auth continua sem fluxo UI/Auth Agent.
**Próximo passo:** implementar Auth Agent e signed Offline Lease simulados da Fase 0, garantindo
que expiração/revogação bloqueie apenas novas entradas e preserve acompanhamento/reconciliação de
ordens abertas.

### WL-2026-08-20-11 — Auth Agent e Offline Lease assinada simulados

**Objetivo:** implementar identidade DualTrade, dispositivo e licenciamento practice da Fase 0 com
serviço local simulado, sem integrar provedor real, conta real ou credencial de corretora.
**Requisitos relacionados:** FR-090 a FR-097 e FR-099; NFR-035 a NFR-038; BR-015 a BR-018;
R-AUTH-001 a R-AUTH-010, R-AUTH-014, R-AUTH-015, R-TEST-009 e R-TEST-010; AG-INV-011 a
AG-INV-013.
**Arquivos alterados:** dependência em `pyproject.toml`; `apps/auth_agent/`; fronteira reduzida em
`apps/core/coordinator.py` e composição opcional em `apps/core/runtime.py`; modelos e criptografia
em `packages/identity/`, `packages/licensing/` e `packages/security/`; testes em
`tests/unit/test_auth_and_licensing.py` e `tests/integration/test_auth_lease_entry_gate.py`;
`AUTHENTICATION_AND_LICENSING.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** OTP de seis dígitos gerado em runtime e PKCE S256; `user_id` estável; access
token curto e refresh token rotativo com detecção de reuso/revogação da família; `device_id`
aleatório e chave Ed25519 própria; prova de posse do dispositivo; lease v1 em JSON canônico e
assinatura Ed25519; verificador local somente com chave pública; limite practice de sete dias;
validação de assinatura, schema, usuário, dispositivo, validade, versão, broker, strategy pack e
proibição de modo real; vault `CurrentUser` simulado; `SecretValue` redigido; renovação silenciosa e
offline dentro da validade; gate reduzido injetável no Core antes de qualquer intenção/reserva.
**Decisões:** a chave de assinatura é gerada somente na instância efêmera do serviço fake e nunca é
embutida; OTP e tokens não usam valores fixos de fixture; o Core recebe apenas autorização e reason
code, sem e-mail, token, chave ou lease bruta; bloqueio de licença é consultado somente no caminho
de nova entrada, enquanto eventos financeiros e reconciliação permanecem ativos; harnesses legados
continuam compondo o Core sem auth durante a transição, mas a composição licenciada usa a factory e
falha fechado.
**Validação executada:** suíte direcionada — 10 testes aprovados; `python -m pytest -q` — 176 testes
aprovados e 1 smoke externo Deriv skipado por exigir opt-in; `python -m compileall apps packages` —
aprovado; `python -m ruff check .` — aprovado; `python -m ruff format --check .` — aprovado após
formatação mecânica; `python -m mypy apps packages` — sucesso em 63 arquivos. Scanner manual não
encontrou segredo atribuído, OTP/token fixo, credencial de broker cruzando identidade, chave privada
de assinatura embutida, modo real habilitado, `float` financeiro, fingerprint, `pickle` ou
martingale; chaves privadas encontradas são apenas a device key local e a signing key gerada em
memória pelo serviço fake. Uma execução intermediária da suíte sofreu timeout isolado no startup do
worker simulado em teste de reconciliação preexistente; o caso passou em três repetições consecutivas
e a execução completa final voltou a 176 aprovados e 1 skip.
**Resultado:** login/PKCE, rotação, reuso, restart, adulteração, expiração, indisponibilidade,
revogação de dispositivo, incompatibilidade e entitlement ausente falham de modo reproduzível. A
prova central mantém uma ordem aceita liquidável e libera sua reserva exatamente uma vez após
expiração/revogação, enquanto uma nova entrada é bloqueada antes de criar `TradeIntent`.
**Riscos/limitações:** o vault é memória isolada por usuário simulado, não DPAPI/Credential Locker;
Auth Agent ainda não é subprocesso com IPC autenticado; serviço, e-mail, antifraude, limite de
dispositivos, revogação push e rotação/distribuição de chaves públicas não existem; estado do
serviço e chave de assinatura são efêmeros; a factory de autorização permanece opcional para
compatibilidade dos harnesses anteriores; modo real continua proibido.
**Próximo passo:** implementar Strategy Catalog/Manifest v1, Runtime mínimo, Signal Arbiter e
Portfolio Allocator simulados, preservando entitlement e a ordem Arbiter → Allocator → Risk Ledger.

### WL-2026-08-20-12 — Strategy Platform simulada e pipeline obrigatório antes do risco

**Objetivo:** implementar os contratos P0 de catálogo, manifesto, validação, runtime isolado,
arbitragem e alocação, provando a ordem Strategy Runtime → Signal Arbiter → Portfolio Allocator →
Risk Ledger antes de qualquer persistência/dispatch financeiro.
**Requisitos relacionados:** FR-100 a FR-108 e FR-110; FR-109 somente como gate futuro; NFR-044;
BR-019 a BR-021; R-STR-001 a R-STR-008; R-CAT-001 a R-CAT-015; R-TEST-011; AG-INV-009,
AG-INV-014 e AG-INV-015.
**Arquivos alterados:** `packages/strategy_catalog/`, `packages/strategies/`,
`packages/signal_arbitration/`, `packages/portfolio_allocation/`;
`apps/core/strategy_pipeline.py`, `apps/core/coordinator.py`, `apps/core/__init__.py` e
`apps/auth_agent/core_gate.py`; helpers e testes em `tests/helpers/strategy_fixtures.py`,
`tests/unit/test_strategy_platform.py` e `tests/integration/test_strategy_pipeline.py`;
`STRATEGY_PLATFORM.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** Manifest v1 imutável com SHA-256, brokers/produtos/timeframes/dados, warm-up,
schema de parâmetros, classe de risco, validation report, status e strategy pack; verificação entre
artefato empacotado declarado pela implementação e hash do manifesto; lifecycle ordenado; registry
com evidências separadas de backtest, walk-forward, replay e practice usando métricas `Decimal`;
runtime limitado e isolado pelo contexto completo, configuração e parâmetros; candle fechado,
duplicidade, ordem temporal e warm-up; signal IDs determinísticos; Arbiter com audit deque limitada,
rechecagem de status/validade, cancelamento de opostos e consenso sem soma; Allocator puro com
orçamentos em minor units por estratégia/conta/global; composição Core que só cria `OrderRequest`
após runtime, arbitragem e alocação.
**Decisões:** nenhuma estratégia comercial é registrada por padrão; evidências aprovadas existem
somente nas fixtures sintéticas; código remoto, plugin, `eval` e download são proibidos; uma stake
solicitada acima de qualquer orçamento é bloqueada em vez de reduzida silenciosamente; sinais
duplicados da mesma estratégia/configurações diferentes podem formar uma única intenção, mas nunca
duplicam orçamento; o Risk Ledger permanece a autoridade final e o allocator não mantém exposição
financeira própria; suspensão bloqueia avaliação nova, sem participar do lifecycle de ordem.
**Validação executada:** suíte direcionada — 18 testes aprovados; `python -m pytest -q` — 191 testes
aprovados e 1 smoke externo Deriv skipado por exigir opt-in; `python -m compileall apps packages` —
aprovado; `python -m ruff check .` e `python -m ruff format --check .` — aprovados;
`python -m mypy apps packages` — sucesso em 77 arquivos. Scanner manual não encontrou segredo,
execução dinâmica, código remoto, rede, import de worker/broker, acesso a `state.db`,
`SingleDatabaseWriter`, Risk Ledger ou submissão dentro dos pacotes de estratégia; orçamento e
stake usam somente `Money` e minor units, sem `float` financeiro.
**Resultado:** manifesto adulterado/incompatível, hash divergente, evidência incompleta, entitlement
ausente, candle aberto/duplicado/fora de ordem, sinal expirado, estratégia suspensa e orçamento
excedido falham fechado. Sinais opostos não produzem intenção; sinais iguais geram exatamente uma
intenção com a stake configurada, e a prova integrada registra a sequência Runtime → Arbiter →
Allocator → Risk Ledger. Suspender após aceite bloqueia nova entrada e a ordem anterior ainda
liquida/libera reserva uma única vez.
**Riscos/limitações:** catálogo, validation registry, auditoria e warm-up permanecem em memória;
restart exige reconstrução/reaquecimento; decisões de arbitragem ainda não são duráveis no
`state.db`; market data Deriv read-only não alimenta automaticamente o pipeline; runtime executa no
processo Core sem budget de CPU/timeout; allocator depende de snapshot produzido pelo Core e a
proteção concorrente final continua no Risk Ledger/constraints; não existe estratégia comercial
`RELEASED`; assinatura de pacote remoto de FR-109 não foi implementada.
**Próximo passo:** implementar ingresso normalizado de candles fechados e replay determinístico com
persistência auditável de manifesto, decisão do Arbiter e Allocation, antes de qualquer nova
integração financeira externa.

### WL-2026-08-20-13 — Closed Candle Ingress e replay determinístico

**Objetivo:** fechar a primeira fatia determinística de market data/replay com candle canônico,
deduplicação antes do Strategy Runtime, relógio virtual e trilha de decisão hash-chain, sem conectar
broker ou execução financeira externa.
**Requisitos relacionados:** FR-023, FR-031, FR-032, FR-034, FR-080, FR-100 a FR-102, FR-104,
FR-106, FR-107 e FR-110; NFR-012 e NFR-044; BR-005 e BR-019 a BR-020; R-DATA-001, R-DATA-005,
R-DATA-007, R-STR-001, R-STR-003, R-STR-004, R-STR-006, R-STR-007, R-CAT-005, R-CAT-006,
R-CAT-010 e R-CAT-014.
**Arquivos alterados:** novos pacotes `packages/market_data/`, `packages/replay/` e
`packages/audit/`; `apps/core/candle_pipeline.py`, `apps/core/strategy_pipeline.py` e exports;
testes em `tests/unit/test_closed_candle_ingress.py`, `tests/unit/test_decision_journal.py` e
`tests/replay/test_deterministic_replay.py`; `docs/CLOSED_CANDLE_REPLAY.md`,
`STRATEGY_PLATFORM.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `ClosedCandle` imutável com tempos e OHLC inteiros escalados, origem e SHA-256
canônico independente da redelivery; parser externo estrito; `CandleIngress` com estados
`ACCEPTED/DUPLICATE/OUT_OF_ORDER/INVALID`; `InMemoryCandleStore` limitado e decisão atômica de
deduplicação/ordenação/gap; fonte fake limitada; ponte Core que somente converte candle aceito para
`Decimal`; `OrderIntentPort` para reutilizar exatamente Runtime → Arbiter → Allocator; replay com
ordenamento por fechamento, clock virtual monotônico, catálogo/runtime/arbiter/allocator/ledger
recriados em cada run e sink de risco sem banco/worker/dispatch; journal limitado com tempo lógico,
hash de payload e cadeia `previous hash + evento canônico`; IDs de sinais, intents simulados, run e
hash final determinísticos.
**Decisões:** o Core permanece dono do estado financeiro; market data e auditoria não gravam
`state.db`; o sink de replay cria somente evidência sintética e recusa `dispatch=True`; manifesto e
configuração são verificados/registrados; suspensão falha fechado em cada avaliação; duplicata não
produz segundo sinal, risco ou intent; gap não é reinterpretado como candle válido; nenhuma regra
Deriv, rede, credencial, conta real ou modo real foi adicionada. A persistência durável do journal
foi deliberadamente deixada para a próxima fatia.
**Validação executada:** `python -m pytest` — 202 testes aprovados e 1 smoke Deriv externo skipado
por exigir opt-in; replay de 500 candles idêntico em duas execuções e após recriação completa do
engine/runtime; `python -m compileall apps packages` — aprovado; `python -m ruff check .` —
aprovado; `python -m ruff format --check .` — 116 arquivos conformes; `python -m mypy apps
packages` — sucesso em 92 arquivos. Scanner manual nos arquivos afetados não encontrou segredo
atribuído, `float` em domínio/valor financeiro, relógio de parede no engine, UUID aleatório,
rede/SDK de broker, persistência crítica, `dispatch=True`, `ORDER_SUBMIT` ou `client_secret`; o
único literal `float` novo é um payload externo deliberadamente inválido em teste negativo.
**Resultado:** candle aberto e payload inválido não alcançam o pipeline; redelivery com outro ID de
origem chega ao pipeline uma vez; fechamento fora de ordem e gap são detectados; a mesma entrada
produz os mesmos sinais, razões de arbitragem/alocação, decisões de risco, IDs e `final_hash` após
recriação; adulterar evento invalida a cadeia; suspensão impede novos intents. O teste integrado
preexistente de suspensão continua provando que ordem aceita anteriormente liquida e libera reserva
uma única vez.
**Riscos/limitações:** `InMemoryCandleStore`, journal, catálogo e warm-up ainda não sobrevivem a
crash; o replay não modela fill, payout ou P&L; não há detector de gap baseado em calendário de
sessão nem adapter Deriv candle end-to-end; `SECURITY.md` e `TEST_PLAN.md` citados pelo `AGENTS.md`
não existem neste workspace; nenhum teste externo foi executado; recriação foi provada no nível do
engine/runtime, não por kill de subprocesso.
**Próximo passo:** implementar persistência auditável separada do `state.db`, checkpoint/warm-up
determinístico e contract tests do `DerivCandleAdapter` read-only antes de conectar o pump ao
Strategy Runtime.

### WL-2026-08-20-14 — Persistência auditável e warm-up recuperável após restart

**Objetivo:** transformar o Closed Candle Ingress/replay da Fase 1 em base local durável e
auditável, restaurar warm-up determinístico após reabertura do processo e validar um adapter Deriv
estritamente read-only, sem abrir caminho financeiro ou usar conta externa.
**Requisitos relacionados:** FR-023, FR-031, FR-032, FR-034, FR-080, FR-100 a FR-102, FR-107 e
FR-110; NFR-012, NFR-023, NFR-042 e NFR-044; R-DATA-001, R-DATA-005 a R-DATA-008; R-DB-003 a
R-DB-005, R-DB-007 e R-DB-008; R-STR-001 a R-STR-003, R-STR-006 e R-STR-007; R-CAT-005,
R-CAT-010 e R-CAT-014; R-TEST-001, R-TEST-004 e R-TEST-005.
**Arquivos alterados:** canonicalização em `packages/domain/canonical.py` e consumidores em
`packages/market_data/`, `packages/audit/`, `packages/replay/`, `packages/strategies/` e
`packages/strategy_catalog/`; banco e repositórios em `packages/persistence/strategy_data.py`,
`candle_repository.py`, `journal_repository.py`, `replay_repository.py` e
`warmup_repository.py`; adapter em `packages/brokers/deriv/`; restore do runtime e replay em
`packages/strategies/checkpoint.py`, `packages/strategies/runtime.py`,
`packages/replay/engine.py`, `packages/replay/persistent_journal.py`, models/exports e
`apps/core/candle_pipeline.py`; testes em `tests/contract/test_deriv_candle_adapter.py`,
`tests/integration/test_strategy_data_persistence.py` e
`tests/replay/test_recoverable_replay.py`; `docs/CLOSED_CANDLE_REPLAY.md`,
`STRATEGY_PLATFORM.md`, `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e
`WORKLOG.md`.
**Implementação:** `strategy_data.db` separado recusa o nome `state.db`, usa conexão/writer local,
WAL, `synchronous=FULL`, `quick_check`, migração imutável com checksum e tabelas separadas para
candles, decisões, replay runs e checkpoints; candles idempotentes por `candle_id` e únicos por
stream/fechamento; conflito de conteúdo falha fechado; journal append-only com sequência única e
hash-chain verificável; `ReplayRecord` imutável comprova manifest + config + candles + journal =
resultado; serialização JSON canônica única; `RuntimePhase`, `StrategyStateV1` e
`WarmupCheckpoint` explícitos, imutáveis e sem `pickle`; restore valida hash, state version,
manifest, configuração, contexto, journal e candles antes de reconstruir o runtime; replay
concluído é reidratado idempotentemente; export tardio remove ciclo de importação dependente da
ordem de startup. `DerivCandleAdapter` valida schema exato, decimais textuais, allowlist, OHLC,
fechamento e timestamps e a ponte criada após os contratos termina no `CandleIngress`.
**Decisões:** o Core permanece a única autoridade financeira e `state.db` não foi modificado; o
novo banco contém somente market data/evidência de estratégia e possui writer próprio limitado a
esse domínio; dinheiro continua em minor units/`Decimal`; o replay usa Risk Ledger sintético sem
criar `TradeIntent`, `RiskReservation`, Outbox ou dispatch; IDs de candle, sinal, correlação,
intenção sintética e hashes são preservados no restore; payload Deriv é validado antes do domínio;
nenhum transporte, WebSocket, segredo, conta real/demo ou submissão foi adicionado.
**Validação executada:** suíte direcionada — 16 testes aprovados; `python -m pytest` — 218 testes
aprovados e 1 smoke Deriv externo skipado por exigir opt-in; `python -m compileall apps packages` —
aprovado; `python -m ruff check .` — aprovado; `python -m ruff format --check .` — 132 arquivos
conformes; `python -m mypy apps packages` — sucesso em 105 arquivos. O scanner manual não encontrou
segredo atribuído, chave/token/credencial de broker, rede/SDK no adapter, dependência de estratégia,
allocator, Risk Ledger ou ordem no adapter, `float` financeiro, `pickle`, execução dinâmica,
`dispatch=True`, submissão ou acesso ao `state.db`; as ocorrências de termos sensíveis ficaram em
regras/documentação e no bloqueio explícito ao nome `state.db`.
**Resultado:** fechar após 300 candles, reabrir `strategy_data.db`, restaurar, reenviar o candle de
fronteira e continuar até 500 produz exatamente o mesmo estado, sinais, arbitragem, alocação,
decisões de risco e hash final do journal que a execução limpa 1–500. Rerun concluído não duplica
decisões. Checkpoint adulterado, manifest/config/versão divergentes, state version não suportada,
candle ausente, journal adulterado, migração incompatível, banco corrompido e candle conflitante
falham fechado. O adapter rejeita parcial, OHLC inválido, fechamento não confirmado e símbolo fora
da allowlist e não possui capacidade financeira.
**Riscos/limitações:** cada evento do journal é persistido em transação própria; crash no meio da
sequência derivada de um candle é detectado como divergência, mas ainda não há commit atômico ou
reparo automático por candle. O teste de restart fecha/reabre banco e objetos, sem matar um
subprocesso. O adapter não está ligado ao transporte/WebSocket Deriv. O catálogo/Validation
Registry continuam em memória; replay não modela fill, payout ou P&L. `SECURITY.md` e
`TEST_PLAN.md` citados pelo `AGENTS.md` continuam ausentes no workspace. Nenhum teste externo foi
executado e modo real permanece proibido.
**Próximo passo:** tornar a gravação das decisões de um candle uma unidade transacional recuperável
e provar crash por kill de subprocesso; somente depois ligar o transporte Deriv read-only ao
ingress persistente, mantendo o pipeline financeiro ausente.

### WL-2026-08-20-15 — Commit atômico por candle e crash recovery por kill

**Objetivo:** eliminar a janela em que apenas parte das decisões derivadas de um candle poderia ser
persistida e provar recuperação determinística com morte real do processo imediatamente antes e
depois do commit do candle 300, sem tocar estado financeiro ou transporte externo.
**Requisitos relacionados:** FR-023, FR-031, FR-032, FR-034, FR-080, FR-100 a FR-102, FR-107 e
FR-110; NFR-012, NFR-023, NFR-042 e NFR-044; R-DATA-001, R-DATA-005 a R-DATA-008; R-DB-003 a
R-DB-005 e R-DB-008; R-STR-001 a R-STR-003, R-STR-006 e R-STR-007; R-TEST-001 e R-TEST-004;
AG-INV-004, AG-INV-005, AG-INV-009 e AG-INV-015.
**Arquivos alterados:** transação/fault hook em `packages/persistence/strategy_data.py`; batch
append em `journal_repository.py`; append transacional em `warmup_repository.py`; novo
`strategy_commit_repository.py`; staging em `packages/replay/persistent_journal.py`; unit of work
e checkpoint automático em `packages/replay/engine.py`; testes/harness em
`tests/replay/test_recoverable_replay.py`, `tests/helpers/strategy_crash_actor.py` e
`tests/chaos/test_strategy_replay_crash_recovery.py`; `docs/CLOSED_CANDLE_REPLAY.md`,
`STRATEGY_PLATFORM.md`, `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e
`WORKLOG.md`.
**Implementação:** `PersistentDecisionJournal` exige um batch ativo, mantém os novos eventos apenas
em memória e falha fechado após erro; o coordenador valida um único candle/run e confirma todos os
`DecisionRecord` contíguos mais o `WarmupCheckpoint` em um `BEGIN IMMEDIATE`; qualquer exceção
pré-commit faz rollback total. O checkpoint passa a acompanhar automaticamente cada candle aceito.
O fault hook rotulado não altera chamadas normais e permite pausar o writer exatamente antes ou
depois do `COMMIT`. Checkpoint idêntico pode comprovar runs distintos; sobreposição de sequência,
conteúdo ou posição incompatível continua bloqueada.
**Decisões:** o candle bruto permanece em transação anterior e idempotente; se houver crash antes
das decisões, ele pode existir sozinho e será reprocessado desde o último checkpoint confirmado.
Isso não representa decisão nem exposição. `strategy_data.db` continua limitado a evidência de
estratégia e não contém `TradeIntent`, `RiskReservation`, Outbox ou ordem. O Core continua único
dono financeiro; Runtime → Arbiter → Allocator → Risk Ledger não foi reordenado; o Risk Ledger do
replay permanece sintético e incapaz de dispatch. Licença, workers e ordens abertas não foram
alterados.
**Validação executada:** suíte direcionada final — 15 testes aprovados; `python -m pytest` — 222
testes aprovados e 1 smoke Deriv externo skipado por exigir opt-in; kill antes do commit 300
restaurou o checkpoint 299 e kill depois restaurou o checkpoint 300, ambos terminando iguais ao
replay limpo 1–500; `python -m compileall apps packages` — aprovado; `python -m ruff check .` —
aprovado; `python -m ruff format --check .` — 135 arquivos conformes; `python -m mypy apps
packages` — sucesso em 106 arquivos. Scanner manual não encontrou segredo, credencial/token,
rede/SDK, `float` financeiro, `pickle`, execução dinâmica, submissão, `dispatch=True`,
`TradeIntent`, `RiskReservation`, Outbox ou acesso financeiro; `subprocess` aparece somente no
teste de caos e `state.db` somente no bloqueio nominal preexistente.
**Resultado:** nenhuma decisão parcial do candle 300 fica visível após kill pré-commit; após kill
pós-commit, lote e checkpoint sobrevivem juntos. O candle de fronteira é redeliverado sem duplicar
decisões. Estado do runtime, sinais, arbitragem, alocação, decisões sintéticas de risco e hash final
são idênticos à execução limpa. Falha injetada pré-commit deixa zero eventos e zero checkpoint para
o candle, mantendo apenas o candle bruto reprocessável. Runs diferentes compartilham somente um
checkpoint exatamente igual e mantêm journals isolados por `run_id`.
**Riscos/limitações:** a unidade não inclui o candle bruto por decisão deliberada; segurança depende
do reprocessamento idempotente já testado. O hook pós-commit é exclusivo de teste e uma exceção
lançada nele seria ambígua para o chamador, portanto produção não injeta callback. Catálogo e
Validation Registry continuam em memória; replay não modela fill, payout ou P&L. `SECURITY.md` e
`TEST_PLAN.md` seguem ausentes. Nenhum teste externo foi executado e modo real permanece proibido.
**Próximo passo:** conectar o pump/transporte Deriv read-only existente ao `DerivCandleAdapter` e
ao ingress persistente primeiro com fake transport e contract tests de backpressure, duplicidade,
reconnect e candle parcial; manter testes externos explicitamente opt-in e nenhuma rota financeira.

### WL-2026-08-20-16 — Deriv read-only via IPC até o ingress persistente

**Objetivo:** ligar o worker Deriv read-only existente ao `DerivCandleAdapter` e ao
`strategy_data.db` por uma fronteira limitada, reproduzível e auditável, sem acionar estratégia,
risco, ordem ou rede externa obrigatória.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032 e FR-034; NFR-012, NFR-013 e NFR-044;
R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008; R-STATE-003 e R-STATE-007; R-BRK-001,
R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001 e R-DATA-005 a R-DATA-008; R-TEST-001,
R-TEST-004 e R-TEST-005; AG-INV-004 a AG-INV-009 e AG-INV-015.
**Arquivos alterados:** `packages/brokers/deriv/candle_pump.py`, `contracts.py` e exports;
`MarketHistoryBatch` em `packages/domain/market.py` e exports; compatibilidade do cliente IPC em
`apps/core/worker_client.py`; contratos em
`tests/contract/test_deriv_candle_ingress_pump.py`; integração em
`tests/integration/test_deriv_candle_ingress_transport.py`; `docs/CLOSED_CANDLE_REPLAY.md`,
`docs/DERIV_WORKER.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `DerivCandleHistoryPump` síncrono recebe uma porta mínima de histórico, exige
símbolo/timeframe e `count` dentro do limite, recusa ticks misturados e resposta maior que o pedido,
valida escopo/timestamps e converte decimais textuais exclusivamente pelo adapter já testado. O
relatório imutável conta aceitos, duplicados, parciais e falhas de qualidade. `MarketHistoryBatch`
preserva `response_message_id`, `correlation_id` e `causation_id` sem quebrar o método legado
`market_history`; esses IDs e o hash do item formam a proveniência persistida. O pump não cria fila,
thread, scheduler ou retry. O teste integrado executa subprocesso Deriv real do projeto com fake
transport, IPC v1, restart explícito e SQLite temporário.
**Decisões:** o Worker continua dono de protocolo/transporte e não recebe caminho de banco; o Core
continua dono do writer de evidência via `CandleIngress`. A conexão nesta fatia usa histórico de
candles, não agrega ticks localmente e não mascara gap/out-of-order. Candle parcial é contabilizado
e descartado. Backpressure de pedido falha antes do IPC; overflow de resposta falha depois da
validação sem persistência adicional. Disconnect é propagado e uma nova chamada só ocorre após
ação explícita do supervisor. Nenhum candle aceito chega automaticamente ao Strategy Runtime.
**Validação executada:** suíte direcionada final — 23 testes aprovados; `python -m pytest` — 227
testes aprovados e 1 smoke Deriv externo skipado por exigir opt-in; `python -m compileall apps
packages` — aprovado; `python -m ruff check .` — aprovado; `python -m ruff format --check .` — 138
arquivos conformes; `python -m mypy apps packages` — sucesso em 107 arquivos. Scanner manual do
conjunto novo não encontrou segredo, credencial/token, rede/SDK direto, `float` financeiro,
`pickle`, execução dinâmica, `TradeIntent`, `RiskReservation`, Outbox, Risk Ledger, allocator,
Strategy Runtime, submissão ou `dispatch=True`; referências financeiras e `float` encontradas
ficam em caminhos genéricos preexistentes do cliente IPC e no clock não monetário.
**Resultado:** fake transport → Deriv Worker subprocesso → IPC → batch correlacionado → adapter →
ingress → `strategy_data.db` persiste exatamente um candle fechado. Repetição e restart do worker
resultam em `DUPLICATE`; parcial não é persistido; gap permanece falha de qualidade; lote acima do
limite não chama a fonte; falha da fonte não é repetida pelo pump; escopo divergente e overflow
falham fechado. `state.db` não é criado e a primeira proveniência IPC fica preservada.
**Riscos/limitações:** trata-se de backfill sob chamada, não assinatura contínua ou scheduler. O
Health Gate do supervisor cobre conexão IPC, mas ainda não recebe o relatório de qualidade do pump.
O batch usa horário de recebimento do Core; sincronização de clock permanece requisito para o gate
seguinte. Candles não acionam Strategy Runtime. Testes externos não foram executados;
`SECURITY.md` e `TEST_PLAN.md` continuam ausentes; modo real permanece proibido.
**Próximo passo:** criar um scheduler monotônico e limitado que execute backfill somente com worker,
clock e dados saudáveis, projete gap/backpressure no Health Gate e recupere após reconnect; depois,
e somente com gates verdes, entregar candles aceitos ao pipeline com `dispatch=False`.

### WL-2026-08-20-17 — Scheduler monotônico, Market Health Gate e pipeline shadow

**Objetivo:** implementar a cadeia bounded Scheduler → Backfill Planner → Candle Pump → ingress
durável → continuidade → Health Gate por série → dispatcher de candle aceito, ligando o pipeline
real de decisão somente em `DECISION_ONLY` e `dispatch=False`.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034, FR-080, FR-100 a FR-102,
FR-107 e FR-110; NFR-012, NFR-013, NFR-020, NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002,
R-ARCH-003 e R-ARCH-005 a R-ARCH-008; R-STATE-003 e R-STATE-007; R-BRK-005, R-BRK-007 e
R-BRK-008; R-DATA-001 a R-DATA-007; R-STR-001, R-STR-003 e R-STR-006; R-TEST-001,
R-TEST-004 e R-TEST-005; AG-INV-004, AG-INV-005, AG-INV-009, AG-INV-014 e AG-INV-015;
DEC-027.
**Arquivos alterados:** novo `packages/market_pipeline/` com models, clock, planner, health,
scheduler, coordinator e dispatcher; paginação read-only em `packages/brokers/deriv/contracts.py`,
`candle_pump.py`, `apps/core/worker_client.py`, `apps/deriv_worker/server.py`,
`public_session.py` e fake transport; defesa shadow em `packages/replay/engine.py`; testes em
`tests/unit/test_market_backfill_scheduler.py`, `test_market_health_gate.py`,
`tests/integration/test_market_backfill_scheduler.py`, `test_shadow_strategy_pipeline.py`,
`tests/helpers/shadow_crash_actor.py`, `tests/chaos/test_shadow_pipeline_crash_recovery.py` e
contrato Deriv; `docs/MARKET_DATA_PIPELINE.md`, documentos de replay/Deriv/IPC, arquitetura,
`STRATEGY_PLATFORM.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `MarketSeriesId` usa broker, broker/canonical symbol, produto, timeframe e
contexto; scheduler `tick()` usa clock monotônico injetável, agenda efêmera, coalescing, recovery
lock, limite global e fairness; retry exclusivo de backfill read-only usa backoff exponencial,
jitter, teto e máximo; planner puro calcula warm-up cronológico, janelas `end_epoch`, overlap e
cursor pela boundary durável. `MarketHealthGate` mantém estados/reasons e snapshots por série e
agregado por broker; gap, backpressure, clock, reconnect, suspensão, incompatibilidade e falha
bloqueiam. Gerações antigas não reabrem health e a geração atual força overlap. O dispatcher prova
persist-before-dispatch, consulta health, passa `dispatch=False` explicitamente e usa capability
default `DECISION_ONLY/can_submit_orders=false`. Proveniência permanece no candle e observabilidade,
mas não altera o hash de decisão estratégico.
**Decisões:** scheduler não executa estratégia nem persiste timer monotônico; dados já commitados
durante resposta antiga permanecem canônicos/idempotentes, porém nova geração precisa revalidá-los
com overlap; fila drenada não limpa backpressure; não há forward-fill; somente `HEALTHY` entrega
candle; warm-up usa o contrato do replay; cursor de delivery é checkpoint por run, nunca booleano
global; intents/reservas registrados pelo replay são exclusivamente sintéticos e não criam tabelas
financeiras, Outbox ou mensagem ao worker. Identidade/licença não elevam execution mode e não foram
alteradas; modo real continua proibido.
**Validação executada:** suíte completa `python -m pytest -q` — 259 aprovados e 1 smoke Deriv
externo skipado por exigir opt-in; suíte crítica de gap, backpressure, reconnect+overlap, suspensão,
kill antes/depois do commit 300 e equivalência de 500 candles passou três vezes consecutivas com 7
casos por execução; `python -m ruff check apps packages tests`, `python -m ruff format --check apps
packages tests`, `python -m mypy apps packages` e `python -m compileall apps packages` aprovados.
Scanner manual não encontrou segredo atribuído, credencial/token, execução dinâmica, import de
RiskLedger/OutboxDispatcher/OrderCoordinator pelo scheduler, Strategy Runtime no pump/ingress,
`dispatch=True` em produção nova, operação Deriv write ou `float` de preço/dinheiro; `float` novo é
restrito a clock monotônico/backoff. Teste externo não foi executado.
**Resultado:** warm-up de 500 candles é recuperado em seis batches de no máximo 100 com dez
duplicatas de overlap; gap de 220 candles bloqueia, faz sete requests/12 duplicatas e termina com
zero faltantes; overflow fica `BACKPRESSURED` e só retorna após backfill+continuidade; parcial
recebido permanece zero persistido/entregue/decisão. Shadow entrega 500 candles e produz 101
decisões sintéticas, zero estado financeiro e zero Deriv write. Replay limpo, shadow e kills
pré/pós-commit produzem o mesmo hash
`41ac9fbd0f1321ec48dfcb703759b595429b6a8945e3d57b6dd1d12872cae53b`; checkpoints de crash são
299 e 300 respectivamente.
**Riscos/limitações:** `tick()` ainda depende de acionamento pela composição; assinatura live e
subscription restore não estão ligados ao coordinator; não há calendário para intervalos
legitimamente sem candle; catálogo/Validation Registry permanecem em memória; runtime continua no
Core sem budget CPU/timeout; não há estratégia comercial, execução demo financeira, IQ Option ou
conta real; `SECURITY.md` e `TEST_PLAN.md` continuam ausentes; Deriv externo não foi executado.
**Próximo passo:** implementar Shadow Runtime contínuo com stream+history no mesmo ingress,
restauração de subscription após backfill, métricas de atraso/sinal/divergência live-vs-replay e
soak test prolongado, mantendo `DECISION_ONLY` e `dispatch=False`.

### WL-2026-08-20-18 — Continuous Shadow Runtime determinístico

**Objetivo:** unir histórico e stream live no mesmo ingresso durável, restaurar subscription apenas
depois do backfill da geração corrente e medir atraso/divergência contra replay, sem criar qualquer
capacidade financeira.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034, FR-080, FR-100 a FR-102,
FR-107 e FR-110; NFR-012, NFR-013, NFR-020, NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002,
R-ARCH-003 e R-ARCH-005 a R-ARCH-008; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001 a
R-DATA-007; R-STR-001, R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e R-TEST-005;
AG-INV-004, AG-INV-005, AG-INV-009, AG-INV-014 e AG-INV-015; DEC-028.
**Arquivos alterados:** modelos, fingerprint e exports em `packages/market_pipeline/models.py`,
`dispatcher.py`, `live.py` e `__init__.py`; testes em
`tests/unit/test_continuous_shadow_runtime.py` e
`tests/integration/test_continuous_shadow_runtime.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/DERIV_WORKER.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `ClosedCandleAggregator` converte `Decimal` para integer units na escala
configurada, fecha OHLC por timeframe, limita a memória de deduplicação, rejeita precisão excessiva
e torna duplicata, out-of-order e gap estados explícitos sem forward-fill. O
`ContinuousShadowRuntime` é poll-driven: executa scheduler/backfill antes da assinatura, consome o
primeiro tick pelo mesmo caminho dos seguintes, invalida o bucket parcial no disconnect, recusa
poll quando health não está verde e envia todo candle fechado ao mesmo `CandleIngress` usado pelo
histórico. Candle durável aceito segue pelo dispatcher existente com `dispatch=False`. Métricas
cobrem ticks/candles, duplicatas, gaps, timeout, atraso, restores e comparações/divergências. A
fingerprint imutável combina hash, sinais e decisões; divergência muda a série para
`FAILED/MD_SHADOW_DIVERGENCE`.
**Decisões:** o Core é dono da agregação, ingresso, Market Health e evidência estratégica; o worker
continua dono apenas de protocolo/transporte e publica ticks imutáveis. A fonte live não decide
stake, não executa estratégia e não grava SQLite. Timeout prolongado vira `STALE`; disconnect cria
nova geração; restart reaproveita candles/checkpoint duráveis e refaz backfill; duplicata converge
no ingress; gap e out-of-order bloqueiam até continuidade comprovada. Licença expirada/revogada não
eleva capability nem altera acompanhamento financeiro; esta fatia não toca identidade, ordem aberta
ou estado financeiro. Modo real continua proibido.
**Validação executada:** suíte direcionada — 9 testes aprovados, repetidos três vezes; soak
determinístico de 10.000 ticks fechou 166 candles e manteve dedupe em 256 identidades; integração de
400 candles históricos + 100 live terminou idêntica ao replay limpo de 500; exceção de transporte
marcou `RECONNECTING` antes de propagar e timeout stale cancelou subscription/descartou bucket.
`python -m pytest -q` na repetição final — 268 aprovados e 1 smoke Deriv externo skipado por opt-in.
A primeira execução integral teve uma corrida transitória preexistente ao ler o arquivo-sinal ainda vazio em
`test_committed_bundle_survives_abrupt_kill_through_wal`; o caso passou três vezes isolado e a suíte
integral seguinte passou. `python -m ruff check .`, `python -m ruff format --check .` (157 arquivos),
`python -m mypy apps packages` (116 arquivos) e `python -m compileall apps packages` — aprovados.
Scanner manual não encontrou segredo atribuído, token/credencial, execução dinâmica,
`TradeIntent`, `RiskReservation`, Outbox, `OrderCoordinator`, operação Deriv write ou
`dispatch=True`; `float` ficou restrito a timeout/clock monotônico, e `state.db` aparece somente na
prova de que não foi criado.
**Resultado:** o shadow combinado produz 101 sinais, 101 decisões sintéticas e hash
`e67f59f6fb3418d394fd92ef03b0340d83f9548af45f4675b987b45c55267a42`, igual ao replay limpo.
Reconexão não restaura assinatura antes do backfill da geração 1; divergência bloqueia o gate e o
próximo poll. Foram observados 401 ticks live, 100 candles live, 100 comparações, zero divergências,
atraso máximo zero na fixture, zero operação Deriv write e nenhum `state.db`.
**Riscos/limitações:** `poll_once()` ainda depende de composição externa e não possui supervisor de
longo prazo, kill/restart do processo live ou budget isolado de CPU. O soak prova 10.000 ticks
sintéticos, não uma sessão temporal com jitter/rede real. Não há calendário para mercados que
legitimamente deixem de formar candle. A deduplicação de ticks é efêmera; idempotência após restart
depende corretamente do candle canônico durável. Catálogo/Validation Registry continuam em memória;
não há estratégia comercial liberada, execução demo financeira, IQ Option ou conta real. O teste
externo não foi executado.
**Próximo passo:** compor o runtime shadow no supervisor/Core com o cliente IPC Deriv read-only,
shutdown controlado, restart por kill e soak temporal com telemetria de CPU/memória/lag, mantendo
fake transport por padrão, `DECISION_ONLY` e nenhuma rota financeira.

### WL-2026-08-20-19 — Lifecycle supervisionado e restart IPC do shadow

**Objetivo:** compor o Continuous Shadow Runtime no Core com o supervisor e cliente IPC Deriv
read-only existentes, tornando start/poll/recovery/shutdown explícitos e provando kill/restart real
do worker antes de restaurar o stream.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034 e FR-080; NFR-012, NFR-013,
NFR-020, NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-003 e R-STATE-007; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001 a R-DATA-007;
R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e R-TEST-005; AG-INV-004, AG-INV-005,
AG-INV-007, AG-INV-009 e AG-INV-015; DEC-029.
**Arquivos alterados:** novo `apps/core/shadow_runtime.py`; cenário read-only adicional em
`apps/deriv_worker/fake_transport.py`; testes em
`tests/unit/test_supervised_shadow_runtime.py` e
`tests/integration/test_supervised_shadow_runtime_ipc.py`; estabilização de espera concorrente em
`tests/integration/test_order_event_lifecycle.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `SupervisedShadowRuntime` recebe portas pequenas para supervisor/client market
data e uma factory do runtime. O lifecycle síncrono usa estados imutáveis
`STOPPED/STARTING/RUNNING/RECOVERING/FAILED`; `poll_once()` não faz restart oculto. Worker não
`READY` invalida a subscription/generation e bloqueia; `recover()` troca o cliente IPC, reconstrói
coordinator/scheduler pela boundary durável, executa overlap e só então restaura subscription. Falha
de start ou recovery faz cleanup do runtime e subprocesso, sem deixar worker órfão. Snapshot expõe
somente health, subscription, contadores, duração monotônica e lag máximo. O cenário fake
`SHADOW_CANDLES` emite ticks sequenciais bounded suficientes para fechar um candle pelo IPC. O teste
financeiro preexistente passou a aguardar tanto `SETTLED` quanto a métrica de fallback que pretende
provar, eliminando dependência do interleaving de commits sem mudar produção financeira.
**Decisões:** o Core permanece dono do lifecycle, geração, market health, ingress e evidência; o
Deriv Worker continua dono apenas de processo/protocolo/transporte e anuncia
`can_submit_orders=false`. Queda não causa retry financeiro nem reabre health pelo socket; recovery
é explícito e somente read-only. Dados persistidos antes do kill sobrevivem; overlap/redelivery
idêntico é `DUPLICATE`; bucket live efêmero é descartado. Shutdown para strategy delivery não cria
ou abandona ordem. Identidade/licença e worker financeiro simulado não foram acoplados ao serviço;
expiração/revogação não pode elevar `DECISION_ONLY`.
**Validação executada:** testes focados finais — 4 aprovados; integração com kill do subprocesso
Deriv repetida três vezes consecutivas; lifecycle normal + fallback de gap repetidos três vezes, 18
casos aprovados. Duas execuções integrais anteriores expuseram a mesma corrida preexistente no teste
de métrica de fallback: logs já mostravam request/response, mas a métrica era lida antes do commit;
após estabilização, `python -m pytest -q` final — 272 aprovados e 1 smoke externo Deriv skipado por
opt-in. `python -m ruff check .`, `python -m ruff format --check .` (160 arquivos),
`python -m mypy apps packages` (117 arquivos) e `python -m compileall apps packages` — aprovados.
Scanner manual não encontrou segredo, token/credencial, execução dinâmica, `TradeIntent`,
`RiskReservation`, Outbox, `OrderCoordinator`, `ORDER_SUBMIT`, operação Deriv write ou
`dispatch=True` na nova composição; `float` permanece restrito a duração/timeout monotônico.
**Resultado:** o primeiro worker é morto, o serviço fica `RECOVERING`, Market Health fica
`RECONNECTING`, um processo com PID diferente sobe e executa overlap duplicado antes da segunda
subscription. Em seguida, 60 ticks sequenciais fecham um candle live contínuo ao candle histórico;
o repositório termina com dois candles, duas restaurações de subscription, uma recuperação e uma
falha de poll esperada. O cursor impede redelivery estratégico do candle histórico. `state.db` não
é criado e nenhuma superfície de trading foi adicionada.
**Riscos/limitações:** o serviço continua caller-driven; não há loop hospedado, política automática
bounded de restart/circuit breaker no nível shadow, budget de CPU ou medição de RSS. A factory deve
reutilizar explicitamente repository, Market Health e cursor duráveis entre gerações; isso está
provado na composição, mas ainda não é uma identidade selada por tipo. O snapshot não agrega ainda
o Health Gate genérico do worker e o Market Health por série em uma única projeção UI. Não houve
soak temporal de horas, jitter/rede real, Deriv externo, estratégia comercial, IQ Option ou conta
real.
**Próximo passo:** criar um host caller-driven/bounded para múltiplas séries, com política explícita
de restart/backoff/circuit breaker, shutdown global determinístico, soak temporal e telemetria de
CPU/RSS/lag; manter fake transport, teste externo opt-in, `DECISION_ONLY` e zero rota financeira.

### WL-2026-08-20-20 — Host shadow bounded, fairness, circuit e budgets

**Objetivo:** hospedar múltiplos serviços shadow com trabalho limitado e justo por ciclo, recovery
read-only governado por backoff/circuit breaker, shutdown global e budgets fail-closed de CPU, RSS
e lag, sem criar thread automática ou capacidade financeira.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034 e FR-080; NFR-012, NFR-013,
NFR-020, NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-003 e R-STATE-007; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001, R-DATA-002,
R-DATA-004, R-DATA-005 e R-DATA-007; R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e
R-TEST-005; AG-INV-004, AG-INV-005, AG-INV-007, AG-INV-009 e AG-INV-015; DEC-030.
**Arquivos alterados:** novo `apps/core/shadow_host.py`; novos testes em
`tests/unit/test_shadow_runtime_host.py`; integração do host em
`tests/integration/test_supervised_shadow_runtime_ipc.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `ShadowRuntimeHost` registra no máximo um número configurado de identidades
`MarketSeriesId`, usa rotação justa e executa no máximo `maximum_actions_per_cycle`; timeout e
override de ações são validados contra tetos. Poll/recovery de uma série consome uma ação e não
bloqueia logicamente as demais. Cada entrada reutiliza `RestartPolicy` e `CrashCircuitBreaker`:
falha agenda backoff monotônico exponencial com jitter validado; repetição abre circuito; após
`open_seconds`, somente a tentativa `HALF_OPEN` pode fechar o circuito. Não há `sleep`, callback
acumulado ou fila nova. Snapshot imutável registra séries, circuitos, due monotônico, ciclos,
ações, falhas e recovery. `SystemResourceProbe` mede `time.process_time()` e RSS/Working Set do Core;
budgets opcionais de CPU por ciclo, RSS e lag mudam o host para `RESOURCE_EXHAUSTED` e encerram todos
os serviços. Shutdown percorre todas as séries mesmo se uma falhar.
**Decisões:** o Core é dono do scheduling, budget e projeção operacional; cada serviço continua
dono do lifecycle/Market Health da própria série e reconstrói estado efêmero pela boundary durável.
Workers não recebem agenda, estratégia, stake, SQLite ou budget. Timeout de poll sem erro não causa
recovery; perda comprovada agenda recovery read-only. Crash/restart preserva geração, backfill e
subscription restore do serviço; duplicata continua convergindo no ingress. Circuit breaker nunca
faz retry financeiro. Budget excedido interrompe somente delivery shadow, sem criar/abandonar ordem.
Licença/entitlement não eleva execution mode e não foi acoplada ao host.
**Validação executada:** suíte focada final — 10 testes aprovados; conjunto crítico de host/IPC — 7
testes aprovados por rodada, repetido três vezes. Soak determinístico executou 10.000 ciclos e 20.000
ações em três séries. `python -m pytest -q` — 278 aprovados e 1 smoke Deriv externo skipado por
opt-in. `python -m ruff check .`, `python -m ruff format --check .` (162 arquivos),
`python -m mypy apps packages` (118 arquivos) e `python -m compileall apps packages` — aprovados.
Scanner manual não encontrou segredo, token/credencial, execução dinâmica, `TradeIntent`,
`RiskReservation`, Outbox, `OrderCoordinator`, `ORDER_SUBMIT`, operação Deriv write ou
`dispatch=True`; `float` ficou restrito a clock, timeout, CPU e jitter.
**Resultado:** três séries receberam exatamente 20.000 polls bounded, com diferença máxima de um
poll entre elas e sem histórico de ciclo em memória. Uma série falha abriu circuito após duas
falhas, a série saudável continuou, a tentativa antecipada foi bloqueada e a prova `HALF_OPEN`
restaurou `RUNNING`. Exceder RSS, CPU/ciclo ou lag encerrou todas as séries com reason code estável.
Na integração real, o host observou kill do subprocesso Deriv, aguardou o backoff monotônico, criou
novo PID, executou overlap e só depois restaurou subscription/fechou candle live. Nenhum `state.db`
ou caminho financeiro foi criado.
**Riscos/limitações:** o host permanece caller-driven e o soak usa tempo monotônico simulado, não
uma sessão hospedada de horas. CPU/RSS medem somente o processo Core; filhos Deriv/IQ ainda não são
agregados. A abstração agenda múltiplos serviços isolados, mas a composição atual pode exigir um
supervisor por serviço e ainda não multiplexa várias séries numa única sessão Deriv. Budget
excedido exige intervenção/restart explícito; não há auto-clear. A factory do serviço ainda precisa
reutilizar corretamente repository, Market Health e cursor. Não houve rede externa, estratégia
comercial, IQ Option ou conta real.
**Próximo passo:** criar uma composição broker-level que compartilhe um supervisor/cliente Deriv
read-only entre várias séries, preserve recovery generation por série e agregue telemetria do Core
e processo filho; depois executar soak hospedado prolongado com fake transport e injeção de
suspensão/restart, mantendo `DECISION_ONLY` e zero rota financeira.

### WL-2026-08-20-21 — Roteamento live multi-série em um cliente Deriv

**Objetivo:** permitir que múltiplos runtimes shadow consumam uma única sessão/cliente Deriv
read-only sem competir pela fila IPC de eventos live.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034 e FR-080; NFR-012,
NFR-020, NFR-021, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-007; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001, R-DATA-002, R-DATA-005 e
R-DATA-007; R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e R-TEST-005; AG-INV-004,
AG-INV-005, AG-INV-007, AG-INV-009 e AG-INV-015; DEC-031.
**Arquivos alterados:** novo `packages/market_pipeline/live_router.py`; exports em
`packages/market_pipeline/__init__.py`; novos testes em
`tests/unit/test_shared_market_tick_router.py` e `tests/integration/test_shared_shadow_stream.py`;
`docs/MARKET_DATA_PIPELINE.md`, `docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `SharedMarketTickRouter` registra séries completas `MarketSeriesId` de um mesmo
broker e entrega uma `RoutedLiveTickSource` por série. O router lê uma única fonte live compartilhada,
valida broker/símbolo/subscription, roteia tick alheio para fila bounded da série correta e retorna
`None` ao poll atual quando só executou roteamento. Subscription e unsubscribe preservam IDs
externos; filas são drenadas ao cancelar a série; snapshot imutável expõe subscriptions ativas,
timeouts, eventos recebidos, backpressure e contadores por série. Backpressure gera
`MD_BACKPRESSURE`; subscription desconhecida ou escopo divergente gera `MD_SCOPE_MISMATCH`.
**Decisões:** o Core é dono do demultiplexador e do isolamento por série; o Deriv Worker continua
dono apenas de protocolo/transporte e capability read-only. O router não cria thread, não agenda
backfill, não executa estratégia, não grava `state.db` e não toca `TradeIntent`, `RiskReservation`
ou Outbox. Timeout de market tick permanece `None`; crash/desconexão continua propagado pela fonte
e será tratado pelo lifecycle superior. Duplicatas permanecem responsabilidade do agregador/ingress.
Licença/entitlement não eleva execution mode.
**Validação executada:** testes focados `python -m pytest tests/unit/test_shared_market_tick_router.py
tests/integration/test_shared_shadow_stream.py -q` — 6 aprovados; integração com um único
subprocesso Deriv fake e um único `SocketWorkerClient` alimentando duas séries — aprovada.
`python -m pytest -q` — 284 aprovados e 1 smoke externo Deriv skipado por opt-in. `python -m ruff
check .`, `python -m ruff format --check .` (165 arquivos), `python -m mypy apps packages` (119
arquivos) e `python -m compileall apps packages` — aprovados. O comando focado rodado com
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` também passou enquanto eu diagnosticava uma pausa transitória de
plugin externo; o pytest padrão passou depois. Scanner manual sobre arquivos alterados não encontrou
segredo atribuído, token/credencial, execução dinâmica, `TradeIntent`, `RiskReservation`, Outbox,
`OrderCoordinator`, `ORDER_SUBMIT`, operação Deriv write, `dispatch=True` ou `float`; ocorrências
restantes são documentação normativa/negações explícitas.
**Resultado:** duas séries Deriv com símbolos distintos compartilharam uma única fila IPC sem roubo
de tick: um runtime observou primeiro o tick da outra série, o router o preservou na fila correta e
ambos fecharam candles por `ContinuousShadowRuntime`. Backpressure por fila cheia e escopo
desconhecido falham fechado e são mensurados. Nenhum `state.db`, ordem, capability financeira ou
rota de dispatch foi criada.
**Riscos/limitações:** esta fatia prova o demultiplexador live e o uso de um cliente IPC compartilhado,
mas ainda não implementa um lifecycle broker-level único que reinicie a sessão uma vez e execute
backfill/recovery coordenado para todas as séries. O host ainda opera serviços isolados; telemetria
de RSS/CPU do subprocesso filho ainda não foi agregada. Não houve rede externa, soak temporal de
horas, IQ Option, estratégia comercial ou conta real.
**Próximo passo:** envolver o router em uma sessão broker-level Deriv que possua um único
supervisor/cliente, coordene start/recovery/backfill por geração para todas as séries e agregue
telemetria do processo filho antes do soak hospedado prolongado.

### WL-2026-08-20-22 — Sessão broker-level Deriv shadow compartilhada

**Objetivo:** envolver o roteador multi-série em uma sessão Core que compartilha um único
supervisor/cliente Deriv read-only, coordena polling justo e reinicia o worker uma única vez para
restaurar todas as séries.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034 e FR-080; NFR-012,
NFR-020, NFR-021, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-007; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001, R-DATA-002, R-DATA-005 e
R-DATA-007; R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e R-TEST-005; AG-INV-004,
AG-INV-005, AG-INV-007, AG-INV-009 e AG-INV-015; DEC-032.
**Arquivos alterados:** novo `apps/core/broker_shadow_session.py`; export em `apps/core/__init__.py`;
novos testes em `tests/unit/test_broker_shadow_session.py`; extensão de
`tests/integration/test_shared_shadow_stream.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `BrokerShadowSession` registra séries antes do start, valida broker e limite
bounded, inicia o supervisor read-only uma única vez, cria `SharedMarketTickRouter`, constrói um
runtime por série com `RoutedLiveTickSource` e alterna `poll_once()` por cursor justo. Perda do
worker ou falha de poll chama `on_disconnect()` em todas as subscriptions ativas e move a sessão
para `RECOVERING`; `recover()` é explícito, para a geração antiga, executa um único
`supervisor.restart()`, recria router/runtimes e chama `recover_and_restore()` em cada série.
Snapshot imutável expõe estado, health, contadores, router e lag por série.
**Decisões:** o Core é dono do lifecycle broker-level shadow; o Deriv Worker continua limitado a
processo/protocolo/transporte read-only e `can_submit_orders=false`. O recovery não ocorre dentro
do poll, não tenta ordem e não reabre série por socket; cada runtime ainda depende do scheduler e
Market Health para restaurar subscription após backfill. Timeout de market tick continua sem erro;
crash/restart é prova operacional, não autorização financeira. Identidade/licença não foram
alteradas e não elevam execution mode.
**Validação executada:** testes focados `python -m pytest tests/unit/test_broker_shadow_session.py
tests/integration/test_shared_shadow_stream.py -q` — 6 aprovados, incluindo kill de um único
subprocesso Deriv fake com duas séries e recovery para novo PID. `python -m pytest -q` — 288
aprovados e 1 smoke externo Deriv skipado por opt-in. `python -m ruff check .`,
`python -m ruff format --check .` (167 arquivos), `python -m mypy apps packages` (120 arquivos) e
`python -m compileall apps packages` — aprovados. Scanner manual sobre arquivos alterados não
encontrou segredo atribuído, token/credencial, execução dinâmica, `TradeIntent`,
`RiskReservation`, Outbox, `OrderCoordinator`, `ORDER_SUBMIT`, operação Deriv write,
`dispatch=True` ou `float`; ocorrências restantes são documentação normativa/negações explícitas ou
exports preexistentes.
**Resultado:** duas séries Deriv compartilham uma sessão Core com um único supervisor/cliente. Após
kill do worker, ambas entram em `RECONNECTING`; `recover()` reinicia o worker uma vez, recria o
router e restaura duas subscriptions. Polling justo distribui chamadas entre séries e o snapshot
mostra router/subscriptions sem expor candle bruto, segredo ou estado financeiro.
**Riscos/limitações:** o scheduler/backfill da integração broker-level ainda é simulado no teste de
recovery; falta soak hospedado prolongado com tempo real, jitter e suspensão. O host bounded ainda
não agenda diretamente a sessão broker-level como unidade composta. RSS/CPU do processo filho ainda
não são agregados à telemetria. Não houve rede externa, IQ Option, estratégia comercial ou conta
real.
**Próximo passo:** executar soak temporal hospedado com `BrokerShadowSession`, fake transport,
injeção de suspensão/restart e telemetria agregada do Core e subprocesso, mantendo `DECISION_ONLY`
e zero rota financeira.

### WL-2026-08-21-01 — Soak broker-level bounded com telemetria do subprocesso

**Objetivo:** executar a próxima fatia de soak hospedado/caller-driven sobre `BrokerShadowSession`,
agregando telemetria do Core e do subprocesso Deriv fake, com injeção local de suspensão/restart e
limites explícitos de ciclo, recovery e recursos.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034 e FR-080; NFR-012,
NFR-020, NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-007; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001, R-DATA-002, R-DATA-004,
R-DATA-005 e R-DATA-007; R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e R-TEST-005;
AG-INV-004, AG-INV-005, AG-INV-007, AG-INV-009 e AG-INV-015; DEC-033.
**Arquivos alterados:** novo `apps/core/broker_shadow_soak.py`; exports em `apps/core/__init__.py`;
novos testes em `tests/unit/test_broker_shadow_soak.py`; extensão de
`tests/integration/test_shared_shadow_stream.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `BrokerShadowSoakRunner` hospeda uma sessão broker-level read-only por ciclos
finitos. `BrokerShadowSoakLimits` torna obrigatórios `max_cycles` e timeout positivo, limita
recoveries e permite budgets opcionais de RSS do Core, RSS do processo filho e lag live.
`BrokerShadowSoakSnapshot` registra estado, reason code, ciclos, polls, falhas, recoveries,
amostra de recursos e snapshot da sessão. `PopenChildProcessProbe` observa PID/alive/RSS do
subprocesso via handle Windows ou `/proc` em POSIX; `NoChildProcessProbe` mantém testes puramente
locais quando não há filho. Hooks de ciclo permitem injetar falha em teste sem abrir loop autônomo.
**Decisões:** o Core continua dono do lifecycle, budgets e telemetria operacional. O worker Deriv
continua somente read-only e não recebe estratégia, stake, SQLite, licença ou segredo. O runner só
executa `poll_once()` quando a sessão está `RUNNING` e só executa `recover()` quando a sessão está
`RECOVERING` e o limite permitir; estouro de budget/recovery chama `shutdown()` e retorna reason
code estável. Timeout de poll permanece operacional e não implica ordem rejeitada/aceita. Crash do
worker vira recovery explícito da sessão, nunca retry financeiro. Duplicatas continuam tratadas por
router/agregador/ingress. Expiração/revogação de licença não foi alterada e não eleva
`DECISION_ONLY`.
**Validação executada:** testes focados
`python -m pytest tests/unit/test_broker_shadow_soak.py tests/integration/test_shared_shadow_stream.py -q`
— 9 aprovados. `python -m pytest -q` — 294 aprovados e 1 smoke externo Deriv skipado por opt-in.
`python -m ruff check .`, `python -m ruff format --check .` (169 arquivos),
`python -m mypy apps packages` (121 arquivos) e `python -m compileall apps packages` — aprovados.
O teste IPC local usa Deriv fake em subprocesso, duas séries, kill do worker, novo PID, restauração
de subscriptions e telemetria do filho. Scanner manual no código novo não encontrou segredo, token,
credencial, `TradeIntent`, `RiskReservation`, Outbox, `OrderCoordinator`, `RiskLedger`,
`ORDER_SUBMIT`, `dispatch=True`, `can_submit_orders=True` ou `float()`; ocorrências amplas restantes
ficaram em documentação normativa/negações explícitas.
**Resultado:** o soak bounded completa ciclos com telemetria agregada, recupera uma suspensão
injetada uma vez, falha fechado em RSS/lag/recovery limit e encerra a sessão sem rota financeira.
Na integração, a sessão Deriv fake compartilhada sobrevive a kill/restart dentro do limite e mantém
duas séries subscritas no novo processo.
**Riscos/limitações:** ainda não é um daemon de horas com janela temporal real, jitter de rede
prolongado ou telemetria persistida. RSS do filho pode ser indisponível em alguns ambientes; quando
um budget de RSS do filho é configurado e o processo vivo não informa RSS, a política falha fechado.
O runtime de estratégia continua no processo Core sem isolamento de CPU por estratégia. Não houve
rede externa, IQ Option, estratégia comercial, conta demo/real ou execução financeira.
**Próximo passo:** transformar o soak bounded em uma execução temporal prolongada e sumarizada,
com janela controlada, falhas programadas, métricas persistíveis e critérios de aceitação claros,
mantendo Deriv read-only, `DECISION_ONLY` e zero rota de ordem.

### WL-2026-08-21-02 — Relatório temporal de soak com critérios de aceitação

**Objetivo:** transformar o soak broker-level bounded em uma execução temporal controlada,
sumarizada e persistível, com janela monotônica, teto de ciclos, amostras bounded e critérios de
aceitação explícitos.
**Requisitos relacionados:** FR-020 a FR-024, FR-031, FR-032, FR-034 e FR-080; NFR-012,
NFR-020, NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-007; R-BRK-005, R-BRK-007 e R-BRK-008; R-DATA-001, R-DATA-002, R-DATA-004,
R-DATA-005 e R-DATA-007; R-STR-003 e R-STR-006; R-TEST-001, R-TEST-004 e R-TEST-005;
AG-INV-004, AG-INV-005, AG-INV-007, AG-INV-009 e AG-INV-015; DEC-034.
**Arquivos alterados:** `apps/core/broker_shadow_soak.py`; exports em `apps/core/__init__.py`;
testes em `tests/unit/test_broker_shadow_soak.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `BrokerShadowTemporalSoakPlan` exige duração positiva, ciclos mínimos, ciclos
máximos, frequência de amostragem e limite de amostras retidas. `BrokerShadowTemporalSoakRunner`
executa `BrokerShadowSoakRunner.run_cycle()` até atingir a janela monotônica, bater no teto de
ciclos ou encontrar estado terminal. O relatório captura snapshot final antes do shutdown,
shutdown snapshot, outcome, reason code, amostras resumidas e critérios usados. `write_json()` grava
um payload JSON-safe sem candle bruto, payload externo, credencial, saldo, ordem ou evidência
financeira.
**Decisões:** toda execução temporal precisa ter dois freios: tempo monotônico e `maximum_cycles`.
Amostras são bounded e descartam as mais antigas quando o limite é excedido. A aceitação falha se a
duração não for alcançada, ciclos mínimos não forem atingidos, final ficar degradado por padrão,
falhas de poll excederem o limite ou recoveries excederem o limite. O runner sempre chama
`shutdown()` ao final da janela temporal; isso encerra somente a sessão shadow read-only e não
abandona ordens porque não existe superfície financeira nessa composição. Timeout/crash/restart
continuam operacionais e nunca viram retry financeiro.
**Validação executada:** `python -m pytest tests/unit/test_broker_shadow_soak.py -q` — 9 aprovados.
`python -m pytest -q` — 298 aprovados e 1 smoke externo Deriv skipado por opt-in.
`python -m ruff check .`, `python -m ruff format --check .` (169 arquivos),
`python -m mypy apps packages` (121 arquivos) e `python -m compileall apps packages` — aprovados.
Scanner manual no código novo/alterado não encontrou segredo, token, credencial, `TradeIntent`,
`RiskReservation`, Outbox, `RiskLedger`, `ORDER_SUBMIT`, `dispatch=True`,
`can_submit_orders=True` ou `float()`; as únicas ocorrências no teste novo são asserções negativas
provando que o relatório JSON não contém esses termos.
**Resultado:** a execução temporal passa quando a janela é alcançada com ciclos suficientes, grava
relatório JSON redigido, falha fechado quando o teto de ciclos impede alcançar a duração, aplica
limite de recoveries e mantém somente amostras bounded. Nenhuma rota de ordem, banco financeiro,
credencial ou dispatch foi criada.
**Riscos/limitações:** ainda não há matriz de cenários nem soak real de horas com jitter externo;
`TEST_PLAN.md` continua ausente no workspace. A camada temporal produz relatório local, mas ainda
não define retenção/compactação de múltiplos relatórios nem pacote de diagnóstico. Não houve rede
externa, IQ Option, estratégia comercial, conta demo/real ou execução financeira.
**Próximo passo:** criar uma matriz local de soak temporal com falhas programadas, variação de
intervalo/ciclos e relatório comparativo, mantendo Deriv read-only, `DECISION_ONLY` e zero rota de
ordem.

### WL-2026-08-21-03 — Matriz local de soak temporal comparativa

**Objetivo:** executar a próxima fatia local de soak como matriz bounded de cenários temporais com
cadências e falhas programadas, preservando todos os resultados em um relatório comparativo
redigido e sem abrir qualquer caminho financeiro.
**Requisitos relacionados:** FR-020 a FR-024, FR-060, FR-064, FR-080 e FR-083; NFR-012, NFR-020,
NFR-021, NFR-023, NFR-042 e NFR-044; R-ARCH-002, R-ARCH-003 e R-ARCH-005 a R-ARCH-008;
R-STATE-007; R-BRK-007 e R-BRK-008; R-DATA-001, R-DATA-002, R-DATA-004, R-DATA-005 e
R-DATA-007; R-SEC-007; R-TEST-001 a R-TEST-005; AG-INV-004, AG-INV-005, AG-INV-007,
AG-INV-008, AG-INV-009 e AG-INV-015; DEC-035.
**Arquivos alterados:** `apps/core/broker_shadow_soak.py`; exports em `apps/core/__init__.py`;
testes em `tests/unit/test_broker_shadow_soak.py`; `docs/MARKET_DATA_PIPELINE.md`,
`docs/IPC_PROTOCOL_V1.md`, `STRATEGY_PLATFORM.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md` e `WORKLOG.md`.
**Implementação:** `BrokerShadowTemporalSoakScenario` vincula ID local validado a um runner
temporal. `BrokerShadowTemporalSoakMatrixRunner` exige pelo menos um cenário, IDs únicos e teto
positivo de cenários; executa todos sequencialmente mesmo após falha e só aprova a matriz quando
todos aprovam. O relatório imutável agrega contagens, duração monotônica e resultados individuais,
reutilizando exclusivamente o payload redigido dos relatórios temporais. Exceção inesperada e falha
do shutdown recebem reason codes estáveis distintos, sem mensagem bruta, e não apagam a comparação
dos cenários seguintes. O cenário excepcional recebe tentativa explícita de shutdown read-only.
**Decisões:** a matriz não usa fail-fast porque o objetivo é evidência comparativa; o outcome
agregado continua fail-closed. Quantidade de cenários e amostras internas são bounded. O ID do
cenário aceita somente caracteres ASCII alfanuméricos, ponto, hífen e underscore e não carrega
descrição livre. Timeout permanece critério do runner temporal; crash/exceção vira falha explícita;
restart é exercitado somente pelo recovery read-only existente; IDs duplicados são rejeitados.
Expiração/revogação de licença não se aplica porque não há entrada nem ordem aberta nessa composição.
**Validação executada:** `python -m pytest tests/unit/test_broker_shadow_soak.py -q` — 14 aprovados.
`python -m pytest -q` — 303 aprovados e 1 smoke externo Deriv skipado por opt-in. Durante validações
anteriores sob carga, três casos de subprocesso não relacionados falharam isoladamente por timeout
de startup/handshake; todos passaram isolados e a execução integral final passou. `python -m ruff
check .`, `python -m ruff format --check .` (169 arquivos), `python -m mypy apps packages` (121
arquivos) e `python -m compileall apps packages` — aprovados. Scanner manual no código alterado só
encontrou `ORDER_SUBMIT`, `TradeIntent` e `RiskReservation` em asserções negativas que provam sua
ausência do JSON; não encontrou padrão positivo de segredo, credencial, dispatch ou modo real.
**Resultado:** baseline, suspensão/recovery com cadência de 500 ms, limite de duração, exceção e
falha de shutdown produzem resultados comparáveis e bounded. Falha não impede o cenário seguinte,
detalhe bruto de exceção não entra no relatório e nenhuma rota IPC/ordem, banco financeiro,
credencial ou integração externa foi criada.
**Riscos/limitações:** a matriz ainda é API local in-process e sequencial; `write_json()` não faz
publicação atômica nem define retenção. O ambiente mostrou flake de timeout em testes antigos de
subprocesso sob carga, embora a suíte final tenha passado. `TEST_PLAN.md` continua ausente. Não houve
soak real de horas, jitter/rede externa, Deriv demo, IQ Option, estratégia comercial ou conta real.
**Próximo passo:** criar um executável local explicitamente opt-in para rodar a matriz por janela
longa, publicar o artefato JSON atomicamente e aplicar retenção bounded, ainda com transportes fake,
Deriv read-only, `DECISION_ONLY` e zero dispatch financeiro.

### WL-2026-08-21-04 — Suíte documental completa do projeto

**Objetivo:** criar a camada documental ausente do DualTrade Desktop, reconciliar o inventário já
declarado no worklog com os arquivos realmente presentes e fornecer uma navegação única entre
contratos normativos, desenvolvimento, testes, segurança, operação, recovery e release.
**Requisitos relacionados:** R-DOC-001 a R-DOC-004; R-TEST-001 a R-TEST-011; R-SEC-001 a
R-SEC-008; R-REL-001 a R-REL-004; todos os AG-INV-001 a AG-INV-015 como limites documentados;
DEC-036. A mudança documenta requisitos existentes, sem afirmar implementação dos itens futuros.
**Arquivos alterados:** novos `README.md`, `BRIEFING.md`, `SECURITY.md`, `ROADMAP.md`,
`TEST_PLAN.md`, `CONTRIBUTING.md`, `docs/DEVELOPMENT.md`, `docs/OPERATIONS_RUNBOOK.md`,
`docs/PERSISTENCE_AND_RECOVERY.md`, `docs/OBSERVABILITY.md`, `docs/RELEASE_PROCESS.md`,
`docs/TRACEABILITY.md` e `docs/ERROR_AND_HEALTH_CODES.md`; atualização append-only de
`WORKLOG.md`.
**Implementação:** `README.md` tornou-se o índice mestre e quick start seguro. O briefing resume
estado, riscos e próximo marco. Segurança consolidou threat model, fronteiras, segredos e resposta a
incidente. Roadmap separou fases/gates sem prometer datas ou autorizar modo real. O plano de testes
formalizou diretórios, comandos, failure matrix, externos opt-in, política de flake e gates. Guias
operacionais documentam ambiente local, startup/safe stop, banco/migrações/backup/recovery,
observabilidade/redação, release futuro, reason codes e rastreabilidade FR/R para código/testes.
**Decisões:** `AIGUARD.md` e `RULES.md` permanecem acima de todos os documentos; PRD e arquitetura
são fontes de produto/desenho; documentos operacionais descrevem como validar/operar a Fase 0;
README/briefing/roadmap apenas navegam e resumem. Conteúdo futuro é marcado explicitamente como não
implementado. Nenhum canal de segurança, requisito legal, release ou capacidade real foi inventado.
Timeout potencialmente aceito continua `UNKNOWN`; crash/restart exigem recovery/reconciliação;
duplicidade permanece idempotente ou conflito; lease expirada/revogada bloqueia somente entradas.
**Validação executada:** verificador PowerShell percorreu todos os links Markdown relativos e
retornou `All local Markdown links resolve.`; scanner de chaves/JWT/Bearer não encontrou valor
sensível; busca de alegações proibidas encontrou somente a proibição histórica do `AIGUARD`.
`python -m pytest -q` — 303 aprovados e 1 smoke externo Deriv skipado por opt-in. `python -m ruff
check .`, `python -m ruff format --check .` (169 arquivos), `python -m mypy apps packages` (121
arquivos) e `python -m compileall apps packages` — aprovados.
**Resultado:** o repositório possui agora 25 documentos Markdown com índice, fontes de verdade,
status implementado versus futuro, procedimentos de desenvolvimento/teste/operação e links locais
válidos. A documentação preserva Fase 0, Deriv read-only, IQ não implementada, `DECISION_ONLY` e
zero submissão real.
**Riscos/limitações:** não há markdown linter/CI de links configurado, owner/security contact formal,
SBOM, pacote de diagnóstico ou pipeline de release. O workspace continua sem repositório Git, então
não foi possível validar diff/links por commit. Documentos exigem manutenção junto ao código e não
substituem testes. Referências históricas no worklog permanecem append-only, inclusive menções a
arquivos ausentes em momentos anteriores.
**Próximo passo:** continuar o marco técnico vigente: executável local opt-in para matriz de soak
longa, artefato JSON atômico e retenção bounded; ao implementá-lo, atualizar README, roadmap,
runbook, teste, observabilidade, rastreabilidade e worklog na mesma fatia.

### WL-2026-08-21-05 — CLI de soak com publicação atômica e retenção bounded

**Objetivo:** transformar a matriz temporal local em uma ferramenta executável e explicitamente
opt-in, publicar sua evidência JSON sem estado parcial e limitar o uso de disco por quantidade e
bytes.
**Requisitos relacionados:** FR-020 a FR-024, FR-060, FR-064, FR-080 e FR-083; R-STATE-007,
R-DATA-002, R-DATA-008, R-SEC-001, R-SEC-007, R-SEC-008, R-TEST-002, R-TEST-003 e R-TEST-005;
AG-INV-004, AG-INV-006, AG-INV-009 e AG-INV-015; DEC-030 a DEC-038.
**Arquivos alterados:** novos `packages/observability/retention.py`,
`apps/core/soak_cli_runtime.py`, `apps/core/soak_cli.py`, `tests/unit/test_report_retention.py`,
`tests/unit/test_soak_cli.py` e `tests/integration/test_soak_cli_execution.py`; exports em
`packages/observability/__init__.py` e `apps/core/__init__.py`; publicação de relatórios em
`apps/core/broker_shadow_soak.py`; atualizações em `README.md`, `AGENTS.md`, `ROADMAP.md`,
`STRATEGY_PLATFORM.md`, `TEST_PLAN.md`, `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`,
`docs/DEVELOPMENT.md`, `docs/MARKET_DATA_PIPELINE.md`, `docs/OBSERVABILITY.md`,
`docs/OPERATIONS_RUNBOOK.md`, `docs/TRACEABILITY.md`, `docs/ERROR_AND_HEALTH_CODES.md` e este
worklog.
**Implementação:** `atomic_write_json` serializa UTF-8 ordenado com `allow_nan=False`, grava um
temporário exclusivo no mesmo diretório, executa `flush`/`fsync`, publica com `os.replace` e limpa o
temporário em falha. `ReportRetentionPolicy` impõe defaults de 10 relatórios/20 MiB e tetos de 100
relatórios/1 GiB; o manager considera somente arquivos regulares `soak_matrix_*.json`, ordena por
`mtime`/nome e remove os mais antigos até cumprir quantidade e bytes. A CLI valida diretório e
limites, exige `--run-soak-matrix` ou `DUALTRADE_RUN_SOAK_MATRIX=1`, executa quatro cenários locais
bounded, evita sobrescrever evidência em colisão de timestamp, aplica retenção e imprime sumário
redigido. Exit code `0` significa matriz aprovada, `1` matriz reprovada/falha operacional e `2`
opt-in ausente/argumento inválido.
**Decisões:** o Core continua dono do lifecycle do soak e da telemetria; observability possui apenas
publicação e retenção do artefato. A mudança foi classificada como risco médio por remover arquivos,
por isso o padrão aceito não pode apontar para JSON arbitrário, symlink/escape de escopo falha
fechado e erro de remoção encerra a CLI sem afetar o Core. Timeout é monotônico e limitado por
duração/ciclos; crash antes de `os.replace` não publica relatório parcial; restart gera novo nome e
preserva colisões; duplicidade não sobrescreve evidência. Expiração/revogação não se aplica porque
não existe entrada, conta ou ordem nessa ferramenta.
**Validação executada:** testes direcionados de retenção, CLI, matriz e subprocesso — 35 aprovados.
`python -m pytest -q` — 324 aprovados e 1 smoke externo Deriv ignorado por exigir opt-in.
`python -m ruff check .` — aprovado; `python -m ruff format --check .` — 175 arquivos formatados;
`python -m mypy apps packages` — sem issues em 124 arquivos; `python -m compileall apps packages` —
aprovado. Verificador de links confirmou 26 documentos Markdown. Scanner manual não encontrou valor
de segredo; `ORDER_SUBMIT`, `TradeIntent` e `RiskReservation` aparecem somente nas asserções
negativas do teste que comprova sua ausência do JSON.
**Resultado:** `python -m apps.core.soak_cli` agora fornece uma matriz reproduzível, sintética,
`DECISION_ONLY` e `dispatch=false`, com publicação atômica, retenção bounded e contrato operacional
testado em subprocesso real. Nenhuma rede, credencial, conta, banco financeiro, broker real ou rota
de ordem foi adicionada.
**Riscos/limitações:** a matriz interna ainda usa sessões locais sintéticas e não é um soak de horas
com worker Deriv em subprocesso; `fsync` cobre o arquivo temporário, mas durabilidade de metadata do
diretório após perda física depende do filesystem/Windows; retenção pode remover parte dos arquivos
selecionados antes de uma falha posterior e então retorna erro, sem atomicidade entre múltiplas
remoções. Não há scheduler/daemon, scanner automático do artefato, pacote de diagnóstico, IQ Option,
estratégia comercial ou modo real.
**Próximo passo:** definir perfis opt-in de soak prolongado para Windows, com fault schedule
determinístico, worker read-only supervisionado, coleta bounded e scanner automatizado do artefato,
mantendo `DECISION_ONLY`, transportes fake por padrão e zero dispatch financeiro.

### WL-2026-08-21-06 — Perfis de soak, scanner de segredos e restore drill

**Objetivo:** fechar a fatia de robustez operacional com presets bounded, injeção determinística de
falhas, verificação automatizada de artefatos e prova isolada de recuperação de backup SQLite.
**Requisitos relacionados:** FR-020 a FR-024, FR-060, FR-064, FR-080 e FR-083; NFR-012, NFR-020,
NFR-021, NFR-023 e NFR-030; R-STATE-007, R-DATA-002, R-DATA-004, R-DATA-008, R-DB-007,
R-DB-008, R-SEC-001, R-SEC-007, R-SEC-008, R-TEST-001, R-TEST-002, R-TEST-004 e R-TEST-007;
AG-INV-004 a AG-INV-009; DEC-039 a DEC-041.
**Arquivos alterados:** novos `apps/core/soak_profiles.py`,
`packages/security/secret_scanner.py`, `tests/unit/test_soak_profiles.py`,
`tests/security/test_secret_scanner.py` e `tests/integration/test_backup_restore_drill.py`;
integração em `apps/core/soak_cli_runtime.py`, exports em `apps/core/__init__.py` e
`packages/security/__init__.py`; testes ampliados em `tests/unit/test_soak_cli.py` e
`tests/integration/test_soak_cli_execution.py`; documentação atualizada em `README.md`,
`ROADMAP.md`, `AGENTS.md`, `SECURITY.md`, `TEST_PLAN.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`,
`docs/PERSISTENCE_AND_RECOVERY.md`, `docs/MARKET_DATA_PIPELINE.md`, `docs/OBSERVABILITY.md`,
`docs/OPERATIONS_RUNBOOK.md`, `docs/TRACEABILITY.md`, `docs/ERROR_AND_HEALTH_CODES.md` e este
worklog.
**Implementação:** `SoakProfile` oferece `fast`, `standard`, `extended` e `chaos`; limites
numéricos explícitos no CLI continuam podendo reduzir/ajustar a execução dentro dos tetos.
`FaultSchedule` guarda tuplas ordenadas/únicas com no máximo 32 eventos e os presets `none`,
`intermittent_crash`, `sleep_resume_gap` e `heavy_load` calculam ciclos relativos ao horizonte. A
CLI injeta backpressure, suspensão e perda sintética da sessão nos cenários isolados, contabiliza
recovery/falha de poll e inclui somente cenário, ciclo, tipo, estado e reason code no JSON.
`SecretScanner` limita arquivo, quantidade de arquivos e matches; reconhece markers de chave
privada, JWT/Bearer/Authorization, token Deriv contextual, OTP, cookie e senha contextual. O match
não contém o valor e seu fingerprint deriva apenas de categoria/localização/comprimento. O relatório
de soak é escaneado em memória antes da publicação. O restore drill comita intenção, reserva,
outbox e ordem, cria backup pela SQLite Backup API, fecha o Core, torna o source temporariamente
indisponível em `tmp_path`, restaura para outro perfil, cria marker, executa `quick_check` e
`integrity_check`, abre outro Core e compara migrations/linhas exatamente.
**Decisões:** o Core permanece dono do lifecycle e o Single Database Writer continua único dono do
estado financeiro. Timeout/duração usam clock monotônico e ciclos máximos; crash/perda sintética do
worker entra em recovery read-only, nunca retry financeiro. Agenda duplicada/desordenada ou acima
do teto é rejeitada. Match/falha do scanner encerra a CLI com código operacional antes de criar o
artefato. O restore não despacha o item `PENDING`, não usa conta externa e não sobrescreve o source;
o hash original é comprovado após o ensaio. Expiração/revogação não se aplica à agenda/scanner e
não foi alterada no Core restaurado.
**Validação executada:** conjunto direcionado de perfis, CLI, scanner, restore e subprocesso — 25
aprovados. `python -m pytest -q` — 336 aprovados e 1 smoke externo Deriv ignorado por exigir opt-in.
`python -m ruff check .` — aprovado; `python -m ruff format --check .` — 180 arquivos formatados;
`python -m mypy apps packages` — sem issues em 126 arquivos; `python -m compileall apps packages` —
aprovado. `SecretScanner` percorreu 205 arquivos `.py/.json/.md`, sem match. Verificador confirmou
os links dos 26 documentos Markdown. Busca manual por capacidades financeiras encontrou somente
asserções negativas de ausência de `ORDER_SUBMIT`, `TradeIntent` e `RiskReservation` nos relatórios.
**Resultado:** a CLI aceita perfis e fault presets testados em subprocesso, registra 7 injeções e
7 observações/recoveries no preset `heavy_load`, bloqueia publicação de payload sensível e mantém
`DECISION_ONLY`/`dispatch=false`. O backup restaurado inicia em outro Core com migrations, intenção,
reserva, outbox e ordem preservadas, enquanto o banco original retorna ao mesmo caminho com hash
idêntico. Nenhuma rede, credencial, conta real, IQ Option ou rota externa de ordem foi adicionada.
**Riscos/limitações:** a suíte executa o perfil `fast`; `extended` e `chaos` têm configuração e
agenda testadas, mas ainda não foram rodados por minutos/horas em hosts Windows variados. O
`WORKER_KILL` da CLI é perda sintética da sessão, não término real prolongado do subprocesso. O
scanner é heurístico/contextual, limitado a extensões/tamanhos configurados e pode ter falso
positivo ou falso negativo; não substitui redação, revisão humana, rotação nem scanner de
dependências. O restore é harness de teste, não comando de produto, e não simula perda de energia ou
falha física de mídia.
**Próximo passo:** executar perfis `extended/chaos` em hosts Windows suportados, correlacionar fault
schedule com telemetria do subprocesso e realizar a revisão formal dos gates da Fase 0 antes de
qualquer decisão de Fase 1. Essa revisão não autoriza conta real nem dispatch externo.

### WL-2026-08-21-07 — Vault Windows DPAPI CurrentUser

**Objetivo:** iniciar a Fase 1 pela menor fatia de proteção local: vault persistente do Auth Agent
vinculado ao usuário atual do Windows, sem alterar autoridade financeira, broker workers ou
capabilities de ordem.
**Requisitos relacionados:** FR-095; NFR-030, NFR-031 e NFR-036; R-AUTH-005; R-SEC-001,
R-SEC-002 e R-SEC-008; R-TEST-008 e R-TEST-009; AG-INV-008, AG-INV-012 e AG-INV-013;
DEC-042 a DEC-044.
**Arquivos alterados:** novos `packages/security/dpapi.py`,
`packages/security/windows_vault.py`, `packages/security/vault.py`,
`apps/auth_agent/vault_factory.py`, `tests/unit/test_dpapi.py`,
`tests/unit/test_vault_factory.py`, `tests/unit/test_windows_vault_envelope.py` e
`tests/integration/test_windows_user_vault.py`; compatibilidade/exports em
`packages/security/secrets.py`, `packages/security/__init__.py` e
`apps/auth_agent/__init__.py`; metadado em `pyproject.toml`; documentação atualizada em
`AUTHENTICATION_AND_LICENSING.md`, `SECURITY.md`, `TEST_PLAN.md`, `README.md`, `ROADMAP.md`,
`AGENTS.md`, `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`,
`docs/DEVELOPMENT.md`, `docs/RELEASE_PROCESS.md`, `docs/TRACEABILITY.md`,
`docs/ERROR_AND_HEALTH_CODES.md` e este worklog.
**Implementação:** wrappers `ctypes` chamam `CryptProtectData`/`CryptUnprotectData` somente com
`CRYPTPROTECT_UI_FORBIDDEN`, copiam o resultado e limpam buffers nativos antes de `LocalFree`.
`WindowsUserScopedVault` deriva filename SHA-256 e entropia por chave, valida envelope externo e
pacote interno versionados, impõe limites, publica por temporário único + `fsync` + `os.replace` e
aplica DACL protegida contendo o SID do token atual ao diretório e a cada arquivo. O novo protocolo
expõe `set_secret/get_secret/delete_secret/has_secret/clear`; o vault Windows e o simulador também
preservam `store/load/delete`. A factory usa DPAPI por padrão no Windows e permite simulação somente
por `force_simulation=True` ou plataforma não Windows.
**Decisões:** o Auth Agent continua dono lógico de refresh token, device key e lease; o vault é
somente a fronteira de proteção da persistência, e o Core permanece a única autoridade financeira.
Timeout não gera aceitação implícita: falha síncrona de DPAPI/ACL/I/O propaga reason code e nenhum
valor. Crash antes do replace preserva o arquivo anterior; restart/reopen revalida envelope, binding
e DPAPI; escrita duplicada substitui atomicamente sem concatenar; corrupção/truncamento e chave
divergente falham fechados. Expiração/revogação continua bloqueando apenas novas entradas e não
interfere em ordens abertas. Nenhuma credencial de broker foi introduzida ou enviada ao serviço de
identidade.
**Validação executada:** testes direcionados de DPAPI, factory, envelopes, DACL e persistência
Windows — 15 aprovados e 1 caminho exclusivo de não Windows ignorado. A execução integral final de
`python -m pytest -q` teve 351 aprovados e 2 ignorados legítimos. Duas rodadas intermediárias
expuseram flakes já existentes e fora da área alterada: handle SQLite ainda aberto no teardown de
`HANG_AFTER_RECEIVE` e timeout de 0,2 s numa consulta de reconciliação; ambos passaram isoladamente
antes da rodada integral final limpa. `python -m ruff check .` — aprovado;
`python -m ruff format --check .` — 188
arquivos formatados; `python -m mypy apps packages` — sem issues em 130 arquivos;
`python -m compileall apps packages` — aprovado. `SecretScanner` percorreu 213 arquivos antes deste
registro, sem match; busca manual na área alterada não encontrou `CRYPTPROTECT_LOCAL_MACHINE`,
`ORDER_SUBMIT` ou `dispatch=True`.
**Resultado:** segredos do Auth Agent podem ser persistidos/reabertos no Windows sob DPAPI
CurrentUser com DACL por SID e integridade independente da resposta do DPAPI. Truncamento,
adulteração, entropy/key mismatch, falha atômica e falha de ACL possuem comportamento reproduzível e
tipado. Falha do vault Windows não degrada para armazenamento em memória. Não foi adicionada rede,
conta, segredo real, IQ Option, estratégia comercial, modo real ou rota financeira externa.
**Riscos/limitações:** a máquina de validação possui somente o SID corrente; por isso não foi
fabricada uma falsa prova cross-user. A negação sob outro SID real e o comportamento da DACL no
artefato instalado exigem harness Windows multiusuário. O runtime Python pode manter cópias
imutáveis transitórias do plaintext apesar da limpeza dos buffers nativos. `fsync` cobre o arquivo
temporário, mas a garantia de metadata após perda física depende do Windows/filesystem. `clear` é
bounded e fail-closed, porém não é uma transação entre múltiplos arquivos.
**Próximo passo:** implementar a fatia 1.2 compondo o Auth Agent em subprocesso isolado com a factory
do vault, IPC local autenticado/versionado e testes de crash/restart/rotação usando somente o
`FakeIdentityService`; em paralelo de validação, adicionar o harness cross-SID/installer, ainda sem
conta real ou dispatch externo.

### WL-2026-08-21-08 — Auth Agent isolado com IPC autenticado

**Objetivo:** concluir a Fatia 1.2 separando sessão DualTrade, device key e lease em um Auth Agent
executado como subprocesso local, com IPC versionado e autenticado, mantendo no Core apenas a
decisão reduzida necessária ao gate de novas entradas.
**Requisitos relacionados:** FR-090 a FR-099; NFR-012, NFR-030, NFR-037 e NFR-040; R-ARCH-001 e
R-ARCH-008; R-AUTH-005, R-AUTH-009, R-AUTH-010 e R-AUTH-014; R-SEC-001 e R-SEC-003; R-STATE-007;
R-TEST-005, R-TEST-008, R-TEST-009 e R-TEST-010; AG-INV-008, AG-INV-012 e AG-INV-013; DEC-045 a
DEC-047.
**Arquivos alterados:** novos `packages/protocol/auth_messages.py`,
`apps/auth_agent/server.py`, `apps/auth_agent/runner.py`, `apps/core/auth_client.py`,
`apps/core/auth_supervisor.py`, `tests/contract/test_auth_ipc_contract.py` e
`tests/integration/test_auth_agent_subprocess.py`; protocolo, domínio e fronteiras atualizados em
`packages/protocol/envelope.py`, `packages/protocol/errors.py`, `packages/protocol/__init__.py`,
`packages/licensing/models.py`, `packages/security/vault.py`, `apps/auth_agent/agent.py`,
`apps/auth_agent/fake_service.py`, `apps/auth_agent/core_gate.py`, `apps/auth_agent/__init__.py` e
`apps/core/__init__.py`; documentação atualizada em `AUTHENTICATION_AND_LICENSING.md`,
`SECURITY.md`, `TEST_PLAN.md`, `README.md`, `ROADMAP.md`, `AGENTS.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `docs/IPC_PROTOCOL_V1.md`,
`docs/TRACEABILITY.md`, `docs/ERROR_AND_HEALTH_CODES.md` e este worklog.
**Implementação:** o supervisor inicia exclusivamente `python -m apps.auth_agent.runner`, gera um
token efêmero de 256 bits e o entrega em um JSON bounded por `stdin`; argv, environment e stdout não
contêm o segredo. O servidor abre somente `127.0.0.1` em porta efêmera e usa o framing JSON v1 de 4
bytes com limite de 64 KiB. O primeiro frame valida versão, papéis, deadline e token em tempo
constante; o cliente valida prova HMAC-SHA-256 sobre nonces de ambos os lados. Modelos imutáveis e
estritos cobrem login, OTP, renovação, autorização, status e shutdown. O servidor possui cache de
replay bounded para idempotência e rejeita reutilização conflitante de `message_id`. No Windows, o
subprocesso reabre o vault DPAPI CurrentUser; o simulador mantém no vault somente seu conjunto
bounded de chaves públicas de verificação para que uma lease fake anterior possa ser revalidada
após restart. A chave privada fake permanece efêmera no serviço simulado. O Core recebe apenas
allow/block, reason code e expiração; status expõe preview hash do usuário, device ID e flag de
lease, nunca access/refresh token, device key, assinatura ou lease bruta.
**Decisões:** o Auth Agent é o único dono de sessão, device key e lease, enquanto o Core continua a
única autoridade financeira. E-mail e OTP atravessam transitoriamente o cliente de login, mas não
são persistidos nem devolvidos nas respostas. Timeout, framing inválido, falha de autenticação,
disconnect ou kill fecham somente novas entradas com `HG_AUTH_AGENT_UNAVAILABLE`; nenhum desses
eventos prova resultado financeiro. Restart é explícito, bounded, rotaciona o token de sessão e
exige novo handshake e revalidação da lease. Duplicidade idêntica recebe resposta correlacionada em
cache; conteúdo divergente sob o mesmo ID falha fechado. Expiração, revogação conhecida ou lease
adulterada bloqueiam novas `TradeIntent`, mas processamento de eventos, settlement e reconciliação
de ordens já abertas permanecem independentes. Nenhuma credencial de broker, conta real ou rota de
ordem foi introduzida.
**Validação executada:** regressão direcionada de auth/vault — 30 aprovados e 2 ignorados;
contratos e integração novos — 5 aprovados e 1 ignorado. `python -m pytest` — 356 aprovados e 3
ignorados legítimos, sem falha intermitente na rodada integral final. O teste Windows usa
subprocesso e DPAPI reais, mata o Auth Agent, comprova bloqueio de uma nova entrada sem criar outra
`TradeIntent`, liquida a ordem já aceita, reinicia com novo handshake, restaura a lease e rejeita
expiração/adulteração. `python -m ruff check .` — aprovado; `python -m ruff format --check .` — 195
arquivos formatados; `python -m mypy apps packages` — sem issues em 135 arquivos;
`python -m compileall apps packages` — aprovado. `SecretScanner` percorreu 220 arquivos antes deste
registro, sem match. A revisão manual da fatia não encontrou `ORDER_SUBMIT`, `dispatch=True`,
`CRYPTPROTECT_LOCAL_MACHINE` ou segredo literal; ocorrências de nomes como `refresh_token`,
`payload_b64` e `signature_b64` pertencem ao estado interno do Auth Agent ou à prova sintética de
adulteração e não atravessam a resposta reduzida.
**Resultado:** o Auth Agent agora possui isolamento real de processo, autenticação de posse no IPC,
persistência DPAPI e recovery comprovado sem transferir estado sensível ao Core. A indisponibilidade
de identidade falha fechada para novas entradas e não contamina o ciclo de vida financeiro já
aberto. A implementação permanece inteiramente local, simulada e sem submissão externa.
**Riscos/limitações:** a prova HMAC autentica posse do token de spawn, mas não cifra o loopback nem
vincula o peer ao SID ou ao binário assinado. Strings imutáveis do runtime Python podem manter
cópias transitórias em memória. O ensaio cross-SID e a auditoria no instalador continuam pendentes.
Identidade/OTP remotos, revogação push, TLS e política comercial ainda não existem. O restart é
deliberadamente explícito; nenhuma autorização é inferida durante a janela indisponível.
**Próximo passo:** integrar o fluxo de login ao Launcher/UI por uma fronteira dedicada, sem ampliar
o Core financeiro, e endurecer a identidade do peer IPC com vínculo ao SID/artefato assinado e
harness Windows multiusuário; continuar usando somente o serviço fake e sem dispatch real.

### WL-2026-08-21-09 — Launcher e supervisão da árvore Windows

**Objetivo:** implementar a Fatia 1.3 com Launcher executável, instância única por perfil,
orquestração ordenada do host do Core e seus descendentes, health polling, restart bounded não
financeiro e safe shutdown sem transferir autoridade financeira ao Launcher.
**Requisitos relacionados:** FR-002, FR-060, FR-072 e FR-073; NFR-004, NFR-012, NFR-014 e NFR-020;
R-ARCH-001, R-ARCH-002, R-ARCH-007 e R-ARCH-008; R-ORD-008; R-STATE-007 e R-STATE-008; R-DATA-002;
R-SEC-001 e R-SEC-003; R-TEST-002, R-TEST-004, R-TEST-005 e R-TEST-008; AG-INV-004, AG-INV-005,
AG-INV-007, AG-INV-008 e AG-INV-011; DEC-048 a DEC-050.
**Arquivos alterados:** novos `apps/launcher/__init__.py`, `apps/launcher/__main__.py`,
`apps/launcher/cli.py`, `apps/launcher/models.py`, `apps/launcher/instance.py`,
`apps/launcher/windows_job.py`, `apps/launcher/core_client.py`,
`apps/launcher/process_controller.py`, `apps/launcher/supervisor.py`,
`apps/core/lifecycle_service.py`, `apps/core/lifecycle_server.py`, `apps/core/runner.py`,
`packages/protocol/lifecycle_messages.py`, `tests/unit/test_launcher_supervisor.py`,
`tests/unit/test_broker_event_drain.py`, `tests/contract/test_core_lifecycle_ipc.py`,
`tests/integration/test_launcher_process_tree.py` e `tests/helpers/launcher_actor.py`; ciclo de vida,
drenagem e protocolo atualizados em `apps/core/runtime.py`, `apps/core/broker_events.py`,
`apps/core/worker_client.py`, `packages/protocol/envelope.py`, `packages/protocol/errors.py` e
`packages/protocol/__init__.py`; documentação atualizada em
`PRD_Trading_Desktop_Deriv_IQOption.md`,
`Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `AGENTS.md`, `README.md`, `ROADMAP.md`,
`SECURITY.md`, `TEST_PLAN.md`, `docs/DEVELOPMENT.md`, `docs/OPERATIONS_RUNBOOK.md`,
`docs/TRACEABILITY.md`, `docs/ERROR_AND_HEALTH_CODES.md`, `docs/IPC_PROTOCOL_V1.md` e este worklog.
**Implementação:** `python -m apps.launcher` aceita perfil, conjunto bounded de workers e
auto-shutdown opcional. `profile.lock` usa lock de SO e atua antes do lock independente do Core. O
Launcher inicia somente `python -m apps.core.runner`, atribui o host ao Windows Job Object com
`KILL_ON_JOB_CLOSE` antes de liberar sua configuração por `stdin` e não inclui segredo em argv,
environment ou stdout. Descendentes herdam o Job. O host compõe a sequência lógica Auth Agent →
Core/lock/SQLite/recovery → Simulated Worker/reconciliation → Deriv read-only fake. O canal
lifecycle TCP loopback usa envelope v1, framing de 64 KiB, token efêmero de 256 bits, prova
HMAC-SHA-256, deadlines, roles, correlação e replay cache bounded de 128 respostas. Snapshots
imutáveis expõem apenas role, PID, liveness, exit code, estado e contagem de restarts. Safe shutdown
executa `HG_SAFE_STOP`, drain dos eventos já enfileirados/em persistência, shutdown dos workers,
shutdown do Auth e fechamento do Core/writer/locks. O event pump permanece ativo durante o
encerramento do Simulated Worker; o writer fecha por último. Timeout escala para terminate/kill e o
Job elimina remanescentes.
**Decisões:** Launcher é dono apenas de lifecycle, lock e containment; o Core permanece dono do
`state.db`, Health Gate, Single Writer e sockets dos workers. Drenagem nunca espera settlement
futuro nem classifica ordem por tempo. Kill do Simulated Worker financeiro deixa a árvore
`DEGRADED` e não troca automaticamente a porta usada por coordinator/reconciliation. Auth Agent e
Deriv read-only admitem restart bounded porque não substituem uma porta financeira ativa. Kill do
Core ou perda abrupta do Launcher encerra toda a árvore; o próximo startup continua obrigado a
integridade, recovery e reconciliação. Expiração/revogação de lease não aciona shutdown de worker e
continua bloqueando somente novas entradas. Start/stop repetidos são idempotentes. Não foram
adicionados conta real, broker login, estratégia comercial ou dispatch externo.
**Validação executada:** suíte nova de modelos, lifecycle, CLI e árvore — 16 testes aprovados após a
inclusão da prova de drain; regressão direcionada de storage, ordens, Auth e contratos de workers —
76 aprovados. `python -m pytest` coletou 375 casos: 372 aprovados e 3 ignorados legítimos em 157,78
segundos, sem flake. Os testes Windows provaram quatro PIDs distintos, startup dentro de 15
segundos, segunda instância rejeitada, kill do worker financeiro sem queda do Core, restarts de Auth
e Deriv sem trocar o PID do Core, kill do Core com limpeza dos filhos, kill abrupto do processo
Launcher com Job Object fechando descendentes, shutdown normal sem órfãos e reutilização do perfil.
`python -m ruff check .` — aprovado; `python -m ruff format --check .` — 213 arquivos formatados;
`python -m mypy apps packages` — sem issues em 148 arquivos; `python -m compileall apps packages` —
aprovado. `SecretScanner` percorreu 238 arquivos antes deste registro, sem match. A busca manual não
encontrou `state.db`, `SingleDatabaseWriter`, `RiskLedger`, `ORDER_SUBMIT`, `dispatch=True`, token
persistente ou dependência de broker dentro de `apps/launcher/`; o único token lifecycle é gerado
em runtime, redigido por tipo e enviado pelo pipe `stdin`.
**Resultado:** a árvore local da Fase 1 possui comando de produto, containment Windows, locks em
duas camadas, supervisão isolada e escada de encerramento reproduzível. Falha de processo não cria
resultado financeiro, não libera exposição e não deixa filhos vivos na prova Windows. O Launcher
continua incapaz de abrir banco crítico ou executar uma estratégia/ordem.
**Riscos/limitações:** a prova HMAC autentica posse, mas o loopback não é cifrado nem vinculado ao
SID/binário assinado. `NoopProcessContainment` fora do Windows não oferece a garantia de órfãos da
plataforma alvo. Crash abrupto do Launcher é contido pelo Job, não gracioso, e depende de recovery
no próximo Core. O Deriv iniciado nesta composição é somente health/read-only fake e ainda não está
ligado a uma UI. Não existe redirecionamento visual da segunda instância, instalador, log
operacional persistente ou UI. Simulated Worker não possui hot restart seguro nesta fatia.
**Próximo passo:** implementar a Fatia 1.4, UI reativa MVP separada, consumindo apenas projeções e
health via IPC, com ações distintas de `Parar novas entradas` e `Encerrar aplicativo`; manter
practice/read-only, sem acesso da UI a broker/SQLite e sem dispatch externo.

### WL-2026-08-21-10 — UI reativa MVP, projeções IPC e safe stop

**Objetivo:** implementar a Fatia 1.4 com UI desktop separada e descartável, projeções bounded
produzidas pelo Core, comandos explícitos de safe stop/retomada/encerramento e integração da UI na
árvore supervisionada, sem transferir autoridade financeira, persistência ou acesso a broker.
**Requisitos relacionados:** FR-020, FR-070, FR-072, FR-073, FR-074 e FR-075; NFR-004, NFR-013 e
NFR-033; BR-014; R-ARCH-001, R-ARCH-004 e R-ARCH-008; R-UI-001 a R-UI-006; R-ORD-008;
R-STATE-007 e R-STATE-008; R-SEC-001 e R-SEC-003; R-TEST-002, R-TEST-005 e R-TEST-008.
**Arquivos alterados:** novos `packages/protocol/ui_messages.py`, `apps/core/ui_service.py` e pacote
`apps/ui/` (`ipc_client.py`, `controller.py`, `view_model.py`, `app.py`, `runner.py`, `__main__.py` e
exports); protocolo/lifecycle em `packages/protocol/envelope.py`, `packages/protocol/errors.py`,
`packages/protocol/lifecycle_messages.py` e `packages/protocol/__init__.py`; Core em
`apps/core/health.py`, `apps/core/runtime.py`, `apps/core/lifecycle_service.py`,
`apps/core/lifecycle_server.py` e `apps/core/runner.py`; projeções SQL read-only em
`packages/persistence/reader.py`; árvore em `apps/launcher/models.py`,
`apps/launcher/process_controller.py`, `apps/launcher/supervisor.py` e `apps/launcher/cli.py`; testes
novos em `tests/unit/test_ui_projection_models.py`, `tests/contract/test_ui_ipc_contract.py` e
`tests/integration/test_core_ui_projection.py`, com provas da árvore atualizadas em
`tests/unit/test_launcher_supervisor.py` e `tests/integration/test_launcher_process_tree.py`;
documentação atualizada no PRD, `RULES.md`, `docs/OPERATIONS_RUNBOOK.md` e este registro.
**Implementação:** o Launcher gera capability HMAC efêmera distinta para UI, entrega tokens apenas
por `stdin`, inicia o Core, aguarda o endpoint de projeções e só então cria a UI como quinto processo
no mesmo Job Object. O canal TCP loopback usa envelope v1, roles Core/UI, deadlines, correlação,
prova HMAC-SHA-256, payload estrito e replay cache bounded. Snapshots imutáveis limitam gates,
cards e ordens; valores monetários usam minor units inteiros. O Core consulta somente projeções
read-only de ordens e P&L realizado, nunca soma moedas diferentes e expõe saldo/clock como
indisponíveis quando não há fonte autoritativa. A UI Tkinter contrastada mostra banner
`MODO DEMO / PRÁTICA — SEM VALOR REAL`, corretoras, códigos/motivos de Health Gate, ordens e P&L.
`UI_SAFE_STOP_COMMAND` acrescenta `HG_SAFE_STOP`; `UI_RESUME_COMMAND` remove somente esse blocker e
só reabre o dispatcher se os demais gates estiverem abertos. `UI_SHUTDOWN_REQUEST` registra pedido
no Core, que é observado pelo polling do Launcher e inicia a escada existente. Kill da UI apenas
degrada health; não reinicia nem encerra o Core.
**Decisões:** Core continua único dono de Health Gate, ordens, P&L e estado financeiro; Launcher é
dono do processo UI/containment e da escada de shutdown; UI possui somente view-model descartável.
O saldo opcional no wire é uma restrição conservadora sobre a proposta inicial: ausência de fonte
practice comprovada aparece como `INDISPONÍVEL`, nunca como zero fabricado. O token efêmero é apenas
capability de bootstrap do IPC e não integra snapshots, logs, argv ou persistência. Desconexão ou
timeout de comando não prova aplicação nem resultado financeiro e não aciona retry automático.
Modo permanece `DECISION_ONLY`/practice/read-only; nenhuma submissão externa foi adicionada.
**Validação executada:** `python -m pytest` coletou 381 casos: 378 aprovados e 3 ignorados legítimos
em 153,75 segundos. Os testes provaram handshake positivo/negativo, parsing estrito, minor units sem
`float`, safe stop bloqueando uma segunda intenção antes da persistência, ordem já aceita seguindo
até `SETTLED` com aplicação de P&L/liberação únicas, retomada condicionada aos gates, kill da UI com
Core vivo, cinco PIDs distintos, shutdown sem órfãos e Job Object contendo todos os descendentes.
`python -m ruff check .` e `python -m ruff format --check .` — aprovados, 225 arquivos formatados;
`python -m mypy apps packages` — sem issues em 157 arquivos; `python -m compileall apps packages` —
aprovado. `SecretScanner` percorreu 250 arquivos com zero match; busca manual encontrou apenas nomes
de enums do próprio scanner, sem valor sensível. Não houve conta, credencial ou transporte externo.
**Resultado:** a Fatia 1.4 entrega dashboard reativo operacional, comandos distintos de safe stop,
retomada e fechamento seguro, e isolamento reproduzível da UI. A queda visual não abandona ordens,
não altera exposição e não impede o Core de persistir eventos financeiros.
**Riscos/limitações:** o loopback autentica posse da capability, mas ainda não vincula peer ao SID ou
binário assinado e não é cifrado. Tkinter não recebeu QA visual automatizado em múltiplas escalas;
a navegação herda controles nativos. Saldos de conta e clock continuam indisponíveis por desenho,
pois nenhuma sessão practice autoritativa foi conectada. P&L de múltiplas moedas aparece
indisponível em vez de somado. A UI não reinicia automaticamente após crash. IQ Option permanece
sem worker executável e não existe modo real, instalador ou rota externa de ordem.
**Próximo passo:** adicionar feed operacional/diagnóstico bounded e acessibilidade/QA visual da UI,
endurecer autenticação do peer IPC no Windows e integrar apenas fontes demo/practice autoritativas
para saldo/clock, começando por simuladores e contract tests; manter modo real fora de escopo.

### WL-2026-08-21-11 — Deriv Demo live opt-in, clock e saldo read-only

**Objetivo:** concluir a Fatia 1.5 com sessão Deriv demo externa estritamente opt-in/read-only,
guardas anti-real antes do transporte, relógio e saldo demo normalizados por IPC e projeção segura
na UI, preservando o fake público como padrão e sem criar qualquer rota de ordem.
**Requisitos relacionados:** AG-INV-006; R-ARCH-003; R-RISK-009; R-DATA-003; R-AUTH-010;
R-SEC-006; FR-010, FR-011, FR-020 e FR-074; DECISION_ONLY.
**Arquivos alterados:** guardas/transporte/sessão em `apps/deriv_worker/validators.py`,
`request_allowlist.py`, `websocket_client.py`, `demo_session.py`, `fake_transport.py`, `mapper.py`,
`server.py`, `__main__.py` e `__init__.py`; modelos/protocolo em `packages/domain/market.py`,
`packages/domain/__init__.py`, `packages/protocol/envelope.py`, `messages.py`, `errors.py`,
`ui_messages.py` e `packages/protocol/__init__.py`; composição em `apps/core/deriv_telemetry.py`,
`worker_client.py`, `lifecycle_service.py`, `runner.py`, `ui_service.py`,
`apps/launcher/process_controller.py`, `supervisor.py`, `cli.py` e `apps/ui/view_model.py`; isolamento
de ambiente em `packages/security/process_environment.py`, `packages/security/__init__.py`,
`apps/core/auth_supervisor.py` e `apps/core/worker_supervisor.py`; testes novos em
`tests/unit/test_deriv_validators.py`, `test_deriv_telemetry.py`,
`test_broker_secret_environment.py`, `tests/contract/test_deriv_demo_session.py`,
`tests/integration/test_deriv_demo_core_ui.py` e `test_deriv_live_demo_integration.py`;
documentação atualizada em `AGENTS.md`, `SECURITY.md`, `TEST_PLAN.md`, `docs/DERIV_WORKER.md`,
`docs/MARKET_DATA_PIPELINE.md`, `docs/ERROR_AND_HEALTH_CODES.md`, `docs/IPC_PROTOCOL_V1.md` e este
registro.
**Implementação:** `validate_deriv_ws_url` aceita somente o host oficial e os paths público/demo,
exige OTP na URL demo e rejeita `/real`; conta selecionada precisa provar `account_type=demo` antes
do pedido de OTP. A allowlist read-only e a denylist de `buy`, `sell`, `proposal`,
`contract_update`, `cashier`, `deposit` e `withdraw` são aplicadas antes de `send`. O CLI mantém
`fake-public` como default e oferece `fake-demo`, `live-public` e `live-demo`; demo externo requer
simultaneamente flag, opt-in de ambiente, App ID, account ID demo e token. Auth Agent, UI e worker
financeiro simulado recebem ambiente sanitizado sem variáveis de credencial de broker. A sessão
demo não reutiliza o OTP single-use em timeout: não faz retry/reconnect cego e exige restart
explícito para nova descoberta/OTP. Saldo passa por `Decimal`, rejeita precisão abaixo do minor
unit e cruza IPC apenas como inteiro, moeda, tipo DEMO e timestamp. O Core mantém cache imutável de
saldo/clock, mede RTT/offset e adiciona `MD_CLOCK_UNTRUSTED` acima de 1.000 ms/2.000 ms ou quando o
worker falha; prova posterior válida limpa somente esse blocker. A UI rotula `FAKE SIMULADO`,
`PUBLIC LIVE` ou `DEMO LIVE`, sem acesso a token, socket ou banco.
**Decisões:** o Deriv Worker continua dono exclusivo da sessão e tradução externa; Core continua
dono do Health Gate e projeção, e saldo não alimenta stake, Allocator, Risk Ledger ou estado
financeiro. Público nunca fabrica saldo. `can_submit_orders=false` e `can_trade=false` permanecem
defesas de capability além da ausência de handlers financeiros. Timeout, crash e restart não
inferem resultado e não habilitam retry de ordem. Expiração/revogação de licença continua separada:
bloqueia novas entradas sem interromper ordens abertas. Nenhuma conta, credencial ou integração real
foi usada nesta entrega.
**Validação executada:** bateria focal nova e de fronteiras — 21 aprovados e 1 smoke demo externo
ignorado; teste shadow regressivo — aprovado. Duas rodadas de `python -m pytest` coletaram 402 casos
e terminaram cada uma com 396 aprovados, 4 skips legítimos e 2 timeouts de startup Windows antigos,
em conjuntos diferentes (`crash_actor`/`launcher_actor` na primeira; dois spawns do Simulated Worker
na segunda). Todos os casos que oscilaram passaram imediatamente em rerun focal: 2/2 e 3/3,
respectivamente; não houve falha funcional reproduzível e nenhum timeout foi ampliado para mascarar
o problema. `python -m ruff check .`, `python -m ruff format --check .`,
`python -m mypy apps packages` (160 arquivos) e `python -m compileall apps packages` — aprovados.
`SecretScanner` percorreu 259 arquivos, sem match; a busca manual encontrou apenas padrões internos
do próprio scanner/teste. Os testes externos permaneceram skipados e nenhuma rede foi acessada.
**Resultado:** a aplicação pode projetar relógio e saldo Deriv demo por uma sessão externa
explicitamente autorizada, mas continua incapaz de submeter, alterar, cancelar ou reconciliar ordem
na Deriv. Endpoints/contas reais e opcodes financeiros falham antes do socket; falha de clock fecha
novas entradas no Core.
**Riscos/limitações:** o token de desenvolvimento ainda nasce no ambiente do processo Launcher/Core
para ser herdado pelo Deriv Worker, embora seja removido dos ambientes do Auth Agent, UI e
Simulated Worker; distribuição comercial deve substituir esse bootstrap por OAuth/vault e canal
direto ao worker. O websocket demo depende de OTP single-use e sua recuperação requer restart
explícito. Não houve smoke externo por ausência deliberada de credencial/configuração. Os timeouts
intermitentes de spawn Windows da suíte completa precisam de diagnóstico separado baseado em
telemetria, sem apenas aumentar deadlines. IQ Option permanece não implementada e modo real segue
proibido.
**Próximo passo:** executar o smoke `external_deriv_demo` somente em conta demo descartável e host
Windows controlado, coletar telemetria redigida do startup/clock/balance, e diagnosticar a variação
de latência dos spawns da suíte antes do gate de release; depois substituir token de ambiente por
OAuth/vault worker-only, ainda sem habilitar ordens.

### WL-2026-08-21-12 — Fase 2 Fatia 2.1: Submissão e Reconciliação Deriv Demo Controlada

**Objetivo:** implementar a submissão, streaming de eventos e reconciliação autoritativa de ordens na Deriv EXCLUSIVAMENTE em conta DEMO autenticada, mantendo guardas anti-real invioláveis, persistência atômica no Core e invariantes financeiras de zero retry cego e liquidação determinística.
**Requisitos relacionados:** AG-INV-001, AG-INV-002, AG-INV-006; R-ORD-001, R-ORD-002, R-ORD-004, R-ORD-006, R-ORD-008; R-RISK-009; BR-014; FR-010, FR-011, FR-020, FR-074; PRD Fase 2 Fatia 2.1.
**Arquivos alterados:** `apps/deriv_worker/validators.py`, `apps/deriv_worker/request_allowlist.py`, `apps/deriv_worker/websocket_client.py`, `apps/deriv_worker/public_session.py`, `apps/deriv_worker/demo_session.py`, `apps/deriv_worker/order_session.py`, `apps/deriv_worker/reconciliation.py`, `apps/deriv_worker/fake_transport.py`, `apps/deriv_worker/server.py`, `apps/deriv_worker/__init__.py`; `packages/protocol/ui_messages.py`, `packages/persistence/reader.py`, `apps/core/ui_service.py`, `apps/ui/view_model.py`; `tests/contract/test_deriv_order_contract.py`, `tests/integration/test_deriv_demo_order_lifecycle.py`, `tests/chaos/test_deriv_demo_crash_reconciliation.py`; `docs/DERIV_WORKER.md`, `PRD_Trading_Desktop_Deriv_IQOption.md` e `WORKLOG.md`.
**Implementação:** 
- `DerivWorker` agora anuncia `can_submit_orders=true`, `supports_order_status_query=true`, `supports_order_events=true` e `supports_reconciliation=true` estritamente quando em modo de execução `DEMO` autenticado (`connection_mode="DEMO"`). Sessões públicas e read-only mantêm `can_submit_orders=false`.
- Tradução do comando IPC `ORDER_SUBMIT` para o payload da API `buy` da Deriv com conversão exata via `Decimal`, parâmetros contratuais (`symbol`, `contract_type`, `amount`, `currency`) e passthrough imutável (`order_id`, `correlation_id`).
- Streaming e normalização de eventos de contrato WebSocket (`proposal_open_contract`) em envelopes IPC `ORDER_EVENT` (`ACCEPTED` -> `OPEN` -> `SETTLED`), com P&L realizado exato em unidades monetárias menores inteiras (`minor_units`), moeda ISO e evidência criptográfica `evidence_hash`.
- Reconciliador autoritativo no `DerivWorker` respondendo a `ORDER_STATUS_REQUEST` via `proposal_open_contract` ou `statement` com evidência imutável (`FOUND`, `NOT_FOUND`, `UNAVAILABLE`) e conferência estrita de atributos (símbolo, direção, moeda) prevenindo reconciliação espúria.
- Guardrails invioláveis de conta real: qualquer tentativa de submissão em conta `CR...`, endpoint real ou sem autorização demo falha fechada levantando `DERIV_REAL_ACCOUNT_FORBIDDEN`.
- Projeção na UI do `broker_order_id` (Deriv `contract_id`) nos resumos de ordens ativas e históricas.
**Decisões:** O Core permanece o único dono do estado financeiro (`state.db`) e do `RiskLedger`. O `DerivWorker` atua exclusivamente como tradutor de protocolo e normalizador de eventos. Timeouts colocam a ordem em `UNKNOWN` preservando a reserva de risco até reconciliação autoritativa.
**Validação executada:** 
- Suíte completa `python -m pytest` executada: 409 testes aprovados (incluindo todos os novos testes de contrato, integração end-to-end e caos), 4 skips legítimos de ambientes externos opt-in/plataforma.
- `python -m ruff check .` e `python -m ruff format --check .` 100% aprovados.
- `python -m mypy apps packages` aprovado sem erros em 162 arquivos.
- `python -m compileall apps packages` compilado com sucesso.
**Resultado:** Submissão e reconciliação controlada em Deriv Demo concluída com integridade total, sem regressões no ecossistema e com proteção estrita contra operações em conta real.
**Riscos/limitações:** Operação restrita ao ambiente Demo da Deriv. Execução de ordens em conta real permanece categoricamente bloqueada. IQ Option permanece em planejamento arquitetural.
**Próximo passo:** Prosseguir para a próxima fatia da Fase 2 (execução controlada / integração de estratégias do catálogo com o pipeline de execução demo).

### WL-2026-08-21-13 — Fase 2 Fatia 2.2: IQ Option Worker em Subprocesso Isolado (Practice / Demo)

**Objetivo:** implementar o IQ Option Worker isolado em subprocesso (`apps/iqoption_worker/` e `packages/brokers/iqoption/`), com IPC v1 autenticado sobre TCP loopback, guarda anti-conta real inviolável (`balance_type == 4`), transporte fake determinístico, submissão de ordens practice, streaming de eventos de contrato, reconciliação autoritativa, integração com o Launcher e card reativo na UI Tkinter.
**Requisitos relacionados:** AG-INV-001, AG-INV-002, AG-INV-006; R-ARCH-007; R-BRK-001; R-ORD-001, R-ORD-002, R-ORD-004, R-ORD-006, R-ORD-008; R-RISK-009; R-AUTH-013; BR-014; FR-010, FR-011, FR-020, FR-074; PRD Fase 2 Fatia 2.2.
**Arquivos alterados:** `packages/protocol/envelope.py`, `apps/launcher/models.py`, `apps/launcher/supervisor.py`, `apps/core/ui_service.py`, `apps/core/lifecycle_service.py`, `apps/core/runner.py`; `packages/brokers/iqoption/validators.py`, `packages/brokers/iqoption/contracts.py`, `packages/brokers/iqoption/fake_transport.py`, `packages/brokers/iqoption/session.py`, `packages/brokers/iqoption/__init__.py`; `apps/iqoption_worker/schema.py`, `apps/iqoption_worker/order_session.py`, `apps/iqoption_worker/reconciliation.py`, `apps/iqoption_worker/server.py`, `apps/iqoption_worker/__main__.py`, `apps/iqoption_worker/__init__.py`; `tests/unit/test_iqoption_validators.py`, `tests/contract/test_iqoption_worker_contract.py`, `tests/integration/test_iqoption_order_lifecycle.py`, `tests/chaos/test_iqoption_crash_reconciliation.py`, `tests/unit/test_launcher_supervisor.py`; `docs/IQOPTION_WORKER.md`, `WORKLOG.md`.
**Implementação:** 
- Scaffolding completo do IQ Option Worker com servidor TCP loopback IPC v1 (`FramedSocket`) anunciando `broker="IQOPTION"`, `connection_mode="PRACTICE"`, `can_submit_orders=true`, `supports_order_status_query=true`, `supports_order_events=true` e `supports_reconciliation=true`.
- Validador rigoroso anti-conta real (`validate_iqoption_account`): qualquer payload com `balance_type == 1` (Real) ou `account_type == "real"` falha imediatamente levantando `IQOPTION_REAL_ACCOUNT_FORBIDDEN`.
- `FakeIQOptionTransport` com cenários determinísticos (`NORMAL`, `AUTH_REJECTED`, `BUY_REJECTED`, `BUY_TIMEOUT`, `BUY_DISCONNECT`, `BUY_SETTLE_WIN`, `BUY_SETTLE_LOSS`).
- Tradução de `ORDER_SUBMIT` para compra de opções na IQ Option com conversão para `Decimal`, preservação de correlation ID, passthrough de `order_id` e deadline.
- Streaming assíncrono de eventos de contrato via thread dedicada (`ORDER_EVENT`) emitindo transições `OPEN` e `SETTLED` com cálculo exato de P&L realizado (`minor_units`), moeda e hash criptográfico de evidência `evidence_hash`.
- `IQOptionReconciliationHandler` respondendo a `ORDER_STATUS_REQUEST` consultando opções ativas ou histórico practice, com matching estrito de atributos.
- Integração do papel `ManagedProcessRole.IQOPTION_WORKER` no Launcher (Windows Job Object) e adição do card da IQ Option na UI (`IQOPTION | PRACTICE | CONECTADO | saldo USD 10000.00 | relógio OK`).
**Decisões:** O Core e o Deriv Worker não importam nenhum módulo do IQ Option Worker. Isolamento total de falha: a queda ou crash do worker IQ Option não degrada o Core ou a Deriv. Ordens seguem a invariante de persistência prévia no SQLite `state.db` e zero retry cego em timeout.
**Validação executada:** 
- Suíte completa de testes executada via `python -m pytest` cobrindo unitários de validação, contratos IPC, integração de ciclo de vida de ordens practice e testes de caos/reconciliação com crash.
- Ferramentas de qualidade `ruff check`, `ruff format --check`, `mypy` e `compileall` executadas e aprovadas.
**Resultado:** IQ Option Worker em subprocesso isolado totalmente operacional em modo Practice / Demo com garantias invioláveis anti-conta real e isolamento total do restante do sistema.
**Riscos/limitações:** O worker opera exclusivamente com transporte fake practice nos testes locais e isolado de contas reais. Operações financeiras em conta real da IQ Option permanecem estritamente bloqueadas por arquitetura e guardrails.
### WL-2026-08-21-14 — Fase 2 Fatia 2.3: Reconciliação Real Cross-Broker e Health Gates Unificados

**Objetivo:** implementar a governança unificada multi-corretora, o isolamento estrito de falhas de broker no `HealthGate`, o despacho concorrente por escopo `(broker, account_id)` no `OrderCoordinator` e na Outbox, e a reconciliação autoritativa simultânea de múltiplos workers no `ReconciliationCoordinator` para Deriv Demo e IQ Option Practice.
**Requisitos relacionados:** AG-INV-001, AG-INV-002, AG-INV-006; R-ARCH-007; R-BRK-001; R-STATE-001; R-ORD-001, R-ORD-002, R-ORD-004, R-ORD-006, R-ORD-008; R-RISK-009; BR-010, BR-014; FR-010, FR-011, FR-020, FR-074; PRD Fase 2 Fatia 2.3.
**Arquivos alterados:** `apps/core/health.py`, `packages/persistence/writer.py`, `apps/core/coordinator.py`, `apps/core/reconciliation.py`, `apps/core/ui_service.py`, `tests/unit/test_unified_health_gate.py`, `tests/integration/test_cross_broker_dispatch.py`, `tests/chaos/test_mixed_broker_recovery_drill.py`, `docs/ERROR_AND_HEALTH_CODES.md`, `WORKLOG.md`.
**Implementação:** 
- `CoreHealthGate` / `HealthGate` evoluído com `register_broker_health(broker, account_id, is_ready, reason_code)`, `can_enter_order(broker, account_id)`, `global_state`, `state_for(broker, account_id)` e `get_snapshot() -> HealthGateSnapshot`.
- Isolamento estrito de falhas (R-ARCH-007 / BR-010): falha, desconexão ou timeout na IQ Option bloqueia novas entradas apenas no escopo `(IQ_OPTION, account_id)` (`HG_WORKER_DISCONNECTED`), enquanto a Deriv Demo permanece 100% aberta e operacional (e vice-versa).
- Bloqueios globais (`HG_SAFE_STOP`, `DB_WRITE_FAILED`, `HG_AUTH_AGENT_UNAVAILABLE`, `HG_LEASE_EXPIRED`) avaliados com precedência sobre bloqueios locais, bloqueando todas as corretoras simultaneamente.
- `MultiBrokerSubmissionRouter(OrderSubmissionPort)` e `AccountCommandSerializer` indexado por tupla `(broker.upper(), str(account_id))` permitindo que ordens para corretoras distintas despaschem em paralelo com exclusão mútua estrita por conta.
- `claim_next_message(broker, account_id, now)` e `OutboxDispatcher.dispatch_next(broker, account_id)` com isolamento de falha de timeout (`HG_ORDER_UNKNOWN`) no escopo do broker/conta.
- `MultiBrokerStatusRouter(OrderStatusPort)` e `ReconciliationCoordinator.reconcile_all_brokers()` orquestrando consultas de reconciliação segregadas por corretora; timeout ou erro em um worker não atrasa nem cancela a reconciliação do outro worker.
- Projeção de UI (`CoreUiProjectionBuilder.snapshot()`) expondo o `GLOBAL_ENTRY_GATE` e todos os health gates específicos de broker/conta ativos.
**Decisões:** O Core permanece o único dono do estado financeiro (`state.db`). As corretoras operam de forma 100% independente no nível de rede e subprocesso; desastres locais não se propagam entre Deriv e IQ Option.
**Validação executada:** 
- Suíte completa de testes unitários, de integração cross-broker e drills de caos com cenários mistos (`UNKNOWN` na Deriv + `OPEN` na IQ Option pós-crash resolvidos atomicamente).
- Verificação completa de `ruff check`, `ruff format --check`, `mypy` e `compileall`.
**Resultado:** Governança unificada cross-broker e reconciliação simultânea concluídas com integridade total, provando o isolamento de falhas entre Deriv Demo e IQ Option Practice.
**Riscos/limitações:** Operação estritamente em ambientes Demo/Practice. Rota de ordens em contas reais segue categoricamente bloqueada por guardrails.
**Próximo passo:** Prosseguir para a próxima fatia da Fase 2 (Orquestração de Portfólio e Catálogo de Estratégias integrado à execução simultânea cross-broker).

### WL-2026-08-21-15 — Fase 2 Fatia 2.4: Gestão de Risco Global e Alocação Multi-Corretora em Demo

**Objetivo:** implementar a gestão de risco global consolidada (`apps/core/risk.py`), teto de exposição cross-broker (Deriv Demo + IQ Option Practice), teto de exposição por ativo canônico, stop loss diário consolidado, cooldown por perdas consecutivas, arbitragem de sinais cross-broker (`packages/signal_arbitration/arbiter.py`), garantia atômica de limites no SQLite (`packages/persistence/writer.py`) e projeções de risco na UI.
**Requisitos relacionados:** AG-INV-001, AG-INV-002, AG-INV-010; R-RISK-001, R-RISK-002, R-RISK-003, R-RISK-005; R-STATE-001; R-ARCH-007; BR-002, BR-010; FR-047; PRD Fase 2 Fatia 2.4.
**Arquivos alterados:** `apps/core/risk.py`, `packages/signal_arbitration/arbiter.py`, `packages/persistence/writer.py`, `apps/core/coordinator.py`, `apps/core/runtime.py`, `packages/protocol/ui_messages.py`, `apps/core/ui_service.py`, `apps/ui/view_model.py`, `docs/ERROR_AND_HEALTH_CODES.md`, `WORKLOG.md`.
**Implementação:** 
- `GlobalRiskConfig` com limites configuráveis: `global_max_exposure_minor_units` (ex: $500.00), `max_exposure_per_symbol_minor_units` (ex: $200.00), `consolidated_daily_stop_loss_minor_units` (ex: $100.00), `max_consecutive_losses` (ex: 3) e `reference_currency="USD"`.
- `canonicalize_symbol(symbol)` normalizando identificadores entre corretoras (ex: `frxEURUSD` e `EURUSD` mapeiam para `EURUSD`).
- `RiskLedger` evoluído com controle de exposição consolidada em memória e persistência (`check_and_reserve`), apuração de P&L realizado diário (`apply_realized_pnl`), transição para `RISK_LOCKED` (`HG_DAILY_STOP_REACHED`) e `COOLDOWN` (`HG_COOLDOWN_ACTIVE`).
- Verificação transacional no SQLite (`SingleDatabaseWriter.persist_intent_reservation_outbox` / `FinancialUnitOfWork.persist`): dentro do `BEGIN IMMEDIATE`, a soma das reservas ativas é recalculada, rejeitando com `RiskLimitExceededError` (`HG_GLOBAL_EXPOSURE_EXCEEDED` / `HG_SYMBOL_EXPOSURE_LIMIT_EXCEEDED`) qualquer intenção concorrente que violaria os limites consolidados.
- `SignalArbiter.arbitrate_cross_broker`: agrupamento por `(canonical_symbol, timeframe)`, cancelamento de sinais opostos cross-broker com `OPPOSING_SIGNALS_CANCELLED` e consenso sem soma de stakes com `CONSENSUS_NO_STAKE_SUM`.
- `UiProjectionSnapshot` e `DashboardViewModel` atualizados com métricas de exposição ativa vs teto global, P&L diário consolidado, perdas consecutivas e badge de estado de risco.
**Decisões:** O cálculo de risco consolidado soma Deriv Demo e IQ Option Practice sem exceção. Sinais opostos no mesmo ativo/timeframe cancelam a operação imediatamente sem gerar risco. A integridade financeira é mantida no banco com atomicidade estrita contra corridas de threads concorrentes.
**Validação executada:** 
- Testes unitários do `RiskLedger` cobrindo teto global, teto por ativo, stop diário e cooldown.
- Testes unitários do `SignalArbiter` para arbitragem cross-broker com sinais opostos e coincidentes.
- Testes de integração de concorrência atômica provando que threads simultâneas na Deriv e IQ Option nunca excedem o teto global no banco de dados.
- Verificação canônica com `pytest`, `ruff`, `mypy` e `compileall`.
**Resultado:** Gestão de risco global consolidada e alocação multi-corretora operando com 100% de integridade matemática e segurança operacional.
**Riscos/limitações:** Operação estritamente em ambiente simulado/demo practice. Limites de risco são mantidos e auditados localmente no SQLite do desktop.
**Próximo passo:** Prosseguir para o pipeline completo de execução com estratégias integradas ao catálogo, backfill contínuo e telemetria de mercado.

### WL-2026-08-21-16 — Fase 3 Fatia 3.1: Pacote de Diagnóstico Redigido e Bundle de Suporte Local

**Objetivo:** implementar o serviço de geração de pacotes de diagnóstico auditáveis e redigidos (`DiagnosticBundleBuilder`), proteção fail-closed contra vazamento de segredos com `SecretScanner`, retenção bounded com `ReportRetentionManager`, comando e resposta no protocolo IPC (`UI_GENERATE_DIAGNOSTIC_COMMAND` / `UI_GENERATE_DIAGNOSTIC_RESPONSE`), e acionamento desacoplado na UI Tkinter.
**Requisitos relacionados:** NFR-030; R-SEC-001; R-ARCH-007; FR-074; PRD Fase 3 Fatia 3.1.
**Arquivos alterados:** `packages/observability/diagnostic.py`, `packages/observability/retention.py`, `packages/observability/__init__.py`, `packages/protocol/envelope.py`, `packages/protocol/ui_messages.py`, `packages/protocol/__init__.py`, `apps/core/diagnostic_service.py`, `apps/core/ui_service.py`, `apps/ui/ipc_client.py`, `apps/ui/controller.py`, `apps/ui/app.py`, `tests/unit/test_diagnostic_bundle.py`, `tests/integration/test_diagnostic_ui_export.py`, `docs/OBSERVABILITY.md`, `WORKLOG.md`.
**Implementação:** 
- `DiagnosticBundleBuilder` (`packages/observability/diagnostic.py`): constrói bundle `.zip` com `manifest.json`, `environment.json`, `health_gates.json`, `risk_summary.json` e `recent_events.json` (bounded, padrão 1000 eventos).
- Invariantes de Segurança e Exclusão Estrita: proibição absoluta de inclusão de bancos SQLite (`state.db`, `strategy_data.db`), arquivos `.vault`, chaves criptográficas ou tokens de sessão.
- Varredura de Segurança Fail-Closed: execução obrigatória do `SecretScanner.scan_directory()` no diretório temporário antes da compactação; se qualquer segredo for detectado, aborta imediatamente, remove os arquivos temporários com `shutil.rmtree` e levanta `DiagnosticSecurityViolationError`.
- Retenção Bounded: suporte no `ReportRetentionPolicy` a arquivos `diagnostic_bundle_*.zip` e aplicação automática via `ReportRetentionManager` (máx. 5 zips, limite de 50 MB) em `reports/diagnostics/`.
- Protocolo IPC e Dispatch no Core: opcodes `UI_GENERATE_DIAGNOSTIC_COMMAND` e `UI_GENERATE_DIAGNOSTIC_RESPONSE` com `CoreDiagnosticService` coletando metadados de forma não bloqueante.
- Interface UI Tkinter: botão "📦 Gerar Diagnóstico" na barra de ações da `DualTradeDesktopApp`, exibindo modal de conclusão com o caminho do zip gerado, tamanho em bytes e hash SHA-256 verificado.
**Decisões:** O pacote de diagnóstico é estritamente local (`reports/diagnostics/`) para preservação da privacidade e isolamento total do sistema. Nenhuma telemetria é transmitida remotamente. A verificação do scanner de segredos é fail-closed, impedindo a publicação de qualquer arquivo se houver match.
**Validação executada:** 
- Testes unitários (`tests/unit/test_diagnostic_bundle.py`) validando a geração dos arquivos JSON, hashes do manifesto, bloqueio e limpeza total em injeção de segredos (`DiagnosticSecurityViolationError`), e retenção bounded.
- Testes de integração (`tests/integration/test_diagnostic_ui_export.py`) provando o fluxo de ponta a ponta via IPC (comando UI -> Core Diagnostic Service -> bundle zip gerado com SHA-256 e tamanho exatos).
- Suíte completa de 456 testes aprovada (`452 passed, 4 skipped, 0 failed`).
- Verificação estática canônica aprovada: `ruff check`, `ruff format --check`, `mypy` (175 source files) e `compileall`.
**Resultado:** Sistema de diagnóstico redigido e suporte local 100% implementado, testado e em conformidade com as diretrizes de segurança e observabilidade.
**Riscos/limitações:** A geração do bundle compacta informações em memória e disco temporário local; eventos operacionais residem no `InMemoryEventSink` e refletem apenas a sessão em execução.
**Próximo passo:** Prosseguir para a próxima fatia da Fase 3 (Instalador Local Onedir, empacotamento com PyInstaller e validação de inicialização do executável em Windows).

### WL-2026-08-22-01 — Fase 3 Fatia 3.2: Validação Estatística de Estratégias e Registry Durável

**Objetivo:** implementar o mecanismo de validação estatística de estratégias (`StrategyPerformanceMetrics`), o motor de Walk-Forward Analysis (`WalkForwardEngine`), a persistência durável de relatórios de validação no SQLite `strategy_data.db` (`SqliteValidationRepository`) e o enforcement formal dos gates de promoção de ciclo de vida no `StrategyCatalog`.
**Requisitos relacionados:** R-STR-007, R-STR-008; R-CAT-003, R-CAT-004; AG-INV-010; R-DB-002; R-DATA-006; FR-050; PRD Fase 3 Fatia 3.2.
**Arquivos alterados:** `packages/persistence/strategy_data.py`, `packages/persistence/validation_repository.py`, `packages/persistence/__init__.py`, `packages/strategy_catalog/metrics.py`, `packages/strategy_catalog/walk_forward.py`, `packages/strategy_catalog/validation.py`, `packages/strategy_catalog/catalog.py`, `packages/strategy_catalog/__init__.py`, `packages/strategies/runtime.py`, `STRATEGY_PLATFORM.md`, `tests/unit/test_strategy_performance_metrics.py`, `tests/unit/test_walk_forward_engine.py`, `tests/unit/test_validation_repository.py`, `tests/integration/test_strategy_lifecycle_promotion.py`, `WORKLOG.md`.
**Implementação:** 
- `StrategyDataMigration` V2 (`packages/persistence/strategy_data.py`): criação da tabela `strategy_validation_reports` com `report_id`, `strategy_id`, `strategy_version`, `code_hash`, `stage`, `is_approved`, `metrics_json`, `dataset_hash` e `created_at_utc`.
- `SqliteValidationRepository` (`packages/persistence/validation_repository.py`): persistência durável e consultas de relatórios de validação, aprovação por estágio e elegibilidade de liberação (`release_eligible`).
- `StrategyPerformanceMetrics` e `calculate_performance_metrics` (`packages/strategy_catalog/metrics.py`): cálculo estatístico puro com `Decimal` para Total Trades, Win Rate, Gross Profit/Loss, Net Profit, Profit Factor (sem divisão por zero), Max Drawdown absoluto (minor units) e relativo (%), Expectancy matemática, Duração média e Distribuição por regime de mercado.
- `WalkForwardEngine` (`packages/strategy_catalog/walk_forward.py`): particionamento temporal estrito em janelas *In-Sample* e *Out-of-Sample* deslizantes, garantindo zero lookahead bias e zero sobreposição entre treino e teste.
- Enforcement de Promoção de Ciclo de Vida (`packages/strategy_catalog/catalog.py`): `promote_strategy` validando a progressão sequencial (`DRAFT` → `BACKTESTED` → `WALK_FORWARD_VALIDATED` → `REPLAY_VALIDATED` → `PRACTICE_VALIDATED` → `RELEASED`) e bloqueando qualquer promoção a `RELEASED` com `VALIDATION_INCOMPLETE` sem todos os relatórios aprovados com `code_hash` compatível.
**Decisões:** Os relatórios de validação e métricas de estratégias residem exclusivamente no `strategy_data.db`, preservando o isolamento absoluto em relação ao `state.db` financeiro. Todos os cálculos matemáticos utilizam `Decimal` ou inteiros minor units.
**Validação executada:** 
- Testes unitários para a calculadora de métricas (`tests/unit/test_strategy_performance_metrics.py`) cobrindo trades mistos, 0 trades, 100% vitórias e 100% derrotas.
- Testes unitários de Walk-Forward (`tests/unit/test_walk_forward_engine.py`) provando a geração de janelas não sobrepostas e validação cronológica.
- Testes unitários de repositório SQLite (`tests/unit/test_validation_repository.py`) testando gravação e consulta durável de relatórios.
- Testes de integração de promoção (`tests/integration/test_strategy_lifecycle_promotion.py`) provando a rejeição de saltos de ciclo de vida e a validação atômica por estágio.
- Suíte completa do repositório aprovada: **462 passed, 4 skipped, 0 failed** em `python -m pytest`.
- Verificações estáticas canônicas aprovadas: `ruff check`, `ruff format --check`, `mypy` (178 source files) e `compileall`.
**Resultado:** Validação estatística de estratégias, motor de walk-forward e repositório durável 100% implementados e integrados com integridade matemática e segurança comprovadas.
**Riscos/limitações:** A validação é baseada em séries de candles fechados históricos locais; a execução em produção mantém a premissa de que rentabilidade passada não garante rentabilidade futura.
**Próximo passo:** Prosseguir para a próxima fatia da Fase 3 (Instalador Local Onedir, empacotamento com PyInstaller e inicialização em Windows).

### WL-2026-08-22-02 — Fase 3 Fatia 3.3: Empacotamento Windows Onedir e Verificação de Integridade

**Objetivo:** implementar o gerador e verificador de manifesto de integridade (`ReleaseManifestBuilder`, `ReleaseIntegrityVerifier`), o gate fail-closed no startup do Launcher Supervisor (`ProcessTreeSupervisor`), o script de empacotamento Windows Onedir (`build_scripts/build_windows_onedir.py`) e a suíte completa de testes de integridade e adulteração.
**Requisitos relacionados:** NFR-032, R-SEC-004; R-REL-001; R-SEC-001, NFR-030; FR-109; PRD Fase 3 Fatia 3.3.
**Arquivos alterados:** `packages/security/integrity.py`, `packages/security/__init__.py`, `apps/launcher/supervisor.py`, `apps/launcher/cli.py`, `build_scripts/build_windows_onedir.py`, `docs/RELEASE_PROCESS.md`, `SECURITY.md`, `tests/unit/test_release_integrity.py`, `tests/integration/test_launcher_integrity_gate.py`, `WORKLOG.md`.
**Implementação:** 
- `ReleaseManifest` e `FileIntegrityRecord` (`packages/security/integrity.py`): estrutura imutável de manifesto canônico com hashing SHA-256 em streaming para cada arquivo e hash auto-consistente do manifesto (`manifest_hash`).
- `ReleaseManifestBuilder`: varredura recursiva de arquivos com exclusão estrita de artefatos de desenvolvimento e segurança (`*.pyc`, `__pycache__`, `*.db*`, `*.vault`, `.env*`, `tests/*`, `.git*`).
- `ReleaseIntegrityVerifier`: verificação rigorosa de integridade da distribuição (`HASH_MISMATCH`, `MISSING_FILE`, `UNTRACKED_FILE`, `SIZE_MISMATCH`, `MANIFEST_CORRUPTED`).
- `ProcessTreeSupervisor` (`apps/launcher/supervisor.py`): integração de verificação pré-startup (`manifest_path` / `distribution_root`); caso qualquer issue seja detectada, aborta a inicialização, define `failure_reason = "INTEGRITY_CHECK_FAILED"`, transiciona para `FAILED` e não executa nenhum subprocesso filho.
- `build_windows_onedir.py` (`build_scripts/build_windows_onedir.py`): pipeline de montagem da distribuição `dist/DualTrade/`, varredura fail-closed de segredos com `SecretScanner`, geração atômica de `release_manifest.json` e auto-verificação do pacote.
**Decisões:** O verificador de integridade é estritamente *fail-closed*. Qualquer arquivo adulterado, ausente ou não autorizado bloqueia a inicialização imediatamente antes do spawn do primeiro subprocesso.
**Validação executada:** 
- Testes unitários (`tests/unit/test_release_integrity.py`) validando roundtrip, detecção de adulteração de 1 byte (`HASH_MISMATCH`), arquivo ausente (`MISSING_FILE`), arquivo não autorizado (`UNTRACKED_FILE`), manifesto corrompido (`MANIFEST_CORRUPTED`) e exclusão de bancos/vaults.
- Testes de integração (`tests/integration/test_launcher_integrity_gate.py`) provando que o Launcher aceita pacotes íntegros e bloqueia completamente pacotes adulterados sem iniciar subprocessos.
- Execução real do builder `build_scripts/build_windows_onedir.py` gerando e auto-verificando 182 arquivos empacotados com sucesso.
- Suíte completa do repositório aprovada: **470 passed, 4 skipped, 0 failed** em `python -m pytest`.
- Verificações estáticas canônicas aprovadas: `ruff check`, `ruff format --check`, `mypy` (180 source files) e `compileall`.
**Resultado:** Infraestrutura de empacotamento Windows Onedir e verificação de integridade 100% implementadas, testadas e integradas.
**Riscos/limitações:** A verificação atual baseia-se em manifesto SHA-256 local; a assinatura digital por certificado X.509/Authenticode pertence ao pipeline de CI/CD de produção.
### WL-2026-08-22-03 — Fase 3 Fatia 3.4: Atualizador Assinado com Health Check e Rollback Transacional

**Objetivo:** implementar o mecanismo de atualização segura com assinatura criptográfica Ed25519 (`SignedUpdateManifest`, `UpdateSignatureVerifier`), guarda de exposição ativa no Core (`UpdateSafetyGuard`), aplicador atômico com snapshot de rollback (`UpdateApplier`), orquestrador de atualização (`UpdateManager`), flag CLI `--post-update-health-check` no Launcher e testes de rollback transacional.
**Requisitos relacionados:** R-SEC-004, NFR-032; R-REL-003; NFR-022; R-DB-004, R-DB-006; PRD Fase 3 Fatia 3.4.
**Arquivos alterados:** `packages/security/updater.py`, `packages/security/__init__.py`, `apps/launcher/updater_service.py`, `apps/launcher/__init__.py`, `apps/launcher/cli.py`, `docs/RELEASE_PROCESS.md`, `SECURITY.md`, `PRD_Trading_Desktop_Deriv_IQOption.md`, `tests/unit/test_signed_updater.py`, `tests/integration/test_update_rollback_drill.py`, `WORKLOG.md`.
**Implementação:** 
- `SignedUpdateManifest` e `UpdatePackageSigner` / `UpdateSignatureVerifier` (`packages/security/updater.py`): modelo canônico de manifesto assinado com verificação criptográfica Ed25519 (usando `cryptography.hazmat.primitives.asymmetric.ed25519`) e validação de hash SHA-256 do pacote `.zip`.
- `UpdateSafetyGuard`: checagem mandatória de segurança no Core bloqueando o update se houver ordens abertas/não-terminais (`PENDING`, `ACCEPTED`, `OPEN`, `UNKNOWN`, `SETTLEMENT_UNKNOWN`) ou reservas de risco ativas (`UPDATE_BLOCKED_ACTIVE_EXPOSURE`).
- `UpdateApplier`: gerenciamento de staging em `updates/staging/{version}/`, snapshot de backup da versão funcional em `updates/backup/{current_version}/` (com exclusão estrita de `state.db`, `strategy_data.db`, vaults e logs), aplicação atômica de arquivos e rotina de rollback.
- `UpdateManager` (`apps/launcher/updater_service.py`): orquestração transacional de update (assinatura -> guarda de risco -> snapshot -> staging -> apply -> post-update health check -> rollback automático caso o health check falhe).
- Flag CLI `--post-update-health-check` no Launcher (`apps/launcher/cli.py`): executa dry-run do manifesto e migrações no startup pós-atualização.
**Decisões:** O banco financeiro `state.db` e o banco de dados de estratégias `strategy_data.db` são rigorosamente excluídos e preservados durante backups, atualizações e rollbacks. Em caso de qualquer falha no health check pós-atualização, o rollback é 100% automático e fail-closed.
**Validação executada:** 
- Testes unitários (`tests/unit/test_signed_updater.py`) cobrindo assinatura Ed25519 válida, rejeição de chave forjada e manifesto adulterado, verificação de hash do pacote, bloqueio por ordens abertas/reservas ativas e rotinas de backup/staging/apply/rollback.
- Testes de integração (`tests/integration/test_update_rollback_drill.py`) provando a aplicação bem-sucedida de update sem tocar no banco financeiro e o rollback automático quando o health check pós-update falha.
- Suíte completa do repositório aprovada: **476 passed, 4 skipped, 0 failed** em `python -m pytest`.
- Verificações estáticas canônicas aprovadas: `ruff check`, `ruff format --check`, `mypy` (181 source files) e `compileall`.
**Resultado:** Atualizador assinado, guarda de exposição e rollback transacional 100% implementados e testados com segurança comprovada.
**Riscos/limitações:** A chave pública de verificação é injetada na chamada do `UpdateManager`; em distribuição estável, a chave mestre deve ser empacotada no cliente com suporte a rotação de certificados.
### WL-2026-08-22-04 — Fase 3: Interface Gráfica Profissional Trading Lab Desktop (PySide6 / Qt 6) com i18n (ES/EN)

**Objetivo:** implementar a interface gráfica profissional do Trading Lab Desktop em PySide6 (Qt 6) com tema Obsidian Dark, sistema completo de internacionalização (i18n) com alternador dinâmico de idiomas entre Espanhol (`es`) e Inglês (`en`), Cockpit de KPIs, Central de Corretoras (Broker Hub), Livro de Ordens reativo, Barra de Ações de Emergência com botão SAFE STOP e exportação de diagnóstico.
**Requisitos relacionados:** R-ARCH-004, R-UI-006; R-UI-004, BR-014; R-UI-002, FR-074; FR-070; PRD Fase 3 UI.
**Arquivos alterados:** `pyproject.toml`, `apps/ui/i18n.py`, `apps/ui/theme.py`, `apps/ui/components/broker_card.py`, `apps/ui/components/risk_gauge.py`, `apps/ui/components/health_pill.py`, `apps/ui/components/order_table.py`, `apps/ui/components/safe_stop_button.py`, `apps/ui/components/__init__.py`, `apps/ui/app.py`, `apps/ui/runner.py`, `apps/ui/__init__.py`, `tests/unit/test_ui_i18n.py`, `tests/unit/test_ui_theme_and_models.py`, `tests/contract/test_pyside6_headless.py`, `WORKLOG.md`.
**Implementação:** 
- `I18nManager` e `t()` (`apps/ui/i18n.py`): dicionário canônico de traduções cobrindo 100% dos textos da interface em Espanhol (`es`) e Inglês (`en`), com suporte a interpolação de parâmetros e reatividade via padrão Observer/Subscriber.
- Sistema de Design Obsidian Dark (`apps/ui/theme.py`): folha de estilos QSS profissional com cores semânticas (`#080A0F`, `#0E131F`, `#161D2E`, Ciano Elétrico `#00E5FF`, Esmeralda `#00F59B`, Carmim `#FF3366`, Âmbar `#FFB800`), cantos arredondados, tipografia segregada (sans-serif para labels, monospace para valores numéricos) e efeitos hover.
- Componentes Visuais Reativos (`apps/ui/components/`):
  - `BrokerCardWidget`: cards para Deriv Demo e IQ Option Practice com pulso de conexão, saldo formatado, relógio e latência em milissegundos.
  - `GlobalRiskGaugeWidget`: medidor de exposição global consolidada com transição dinâmica de cores (ciano < 70%, âmbar 70-90%, vermelho > 90%).
  - `HealthGatePillWidget`: badges compactos de Health Gate com tooltips detalhados.
  - `OrderTableView`: tabela de ordens persistidas com cores semânticas por direção (CALL verde / PUT vermelho) e estado.
  - `SafeStopButton`: botão de emergência com destaque visual para interrupção imediata de novas entradas sem fechar o Core.
- Janela Principal `TradingLabMainWindow` (`apps/ui/app.py`): layout ergonômico com Header Bar, Cockpit KPIs, Broker Hub, Health Monitor, Livro de Ordens e Barra de Ações com conexão a `UiController` via `QTimer`.
**Decisões:** A UI comunica-se exclusivamente através do `UiController` e protocolo IPC loopback autenticado, sem acesso direto a bancos SQLite (`state.db`, `strategy_data.db`) ou credenciais de corretoras. O botão SAFE STOP preserva o monitoramento e liquidação de ordens já abertas.
**Validação executada:** 
- Testes unitários de i18n (`tests/unit/test_ui_i18n.py`) provando 100% de paridade de chaves entre ES e EN, troca dinâmica de idioma e formatação de strings.
- Testes unitários de tema e modelos (`tests/unit/test_ui_theme_and_models.py`) validando integridade do QSS e formatação de projeções.
- Teste de contrato headless do PySide6 (`tests/contract/test_pyside6_headless.py`) instanciando a `TradingLabMainWindow` em modo offscreen, validando renderização de widgets, reatividade de Safe Stop e troca de idioma.
- Suíte completa do repositório aprovada: **483 passed, 4 skipped, 0 failed** em `python -m pytest`.
- Verificações estáticas canônicas aprovadas: `ruff check`, `ruff format --check`, `mypy` (188 source files) e `compileall`.
**Resultado:** Interface gráfica profissional PySide6 / Qt 6 com tema Obsidian Dark e suporte i18n (ES/EN) 100% implementada, testada e integrada.
**Riscos/limitações:** A execução da interface gráfica completa com janelas nativas requer ambiente com servidor gráfico Windows; a execução em CI/servidor opera em modo headless (`QT_QPA_PLATFORM=offscreen` / `--headless-ui`).
### WL-2026-08-22-05 — Fase 3: Compilação do Executável Windows (TradingLab.exe) e Gerador de Instalador

**Objetivo:** implementar o pipeline completo de build e empacotamento do executável Windows `TradingLab.exe` via PyInstaller (`--onedir`, `--windowed`), script de automação `compile_trading_lab.py` com integração a `SecretScanner` e `ReleaseManifestBuilder`, script de instalador Inno Setup `TradingLab_Setup.iss` e teste de fumaça da distribuição.
**Requisitos relacionados:** R-REL-001; R-SEC-001, NFR-030; NFR-031; PRD Fase 3 Packaging & Distribution.
**Arquivos alterados:** `build_scripts/version_info.txt`, `build_scripts/TradingLab.spec`, `build_scripts/compile_trading_lab.py`, `build_scripts/TradingLab_Setup.iss`, `tests/integration/test_distribution_build_smoke.py`, `docs/RELEASE_PROCESS.md`, `README.md`, `WORKLOG.md`.
**Implementação:** 
- `version_info.txt` (`build_scripts/version_info.txt`): metadados do recurso Windows PE (Versão "1.0.0.0", Nome do Produto "Trading Lab Desktop", Empresa "Trading Lab Systems", Copyright).
- `TradingLab.spec` (`build_scripts/TradingLab.spec`): especificação canônica do PyInstaller apontando para `apps/launcher/__main__.py`, incluindo pacotes `apps`, `packages`, dependências PySide6 (Qt 6), `cryptography`, `websockets`, com `console=False` (`--windowed`) sem janela preta de console.
- `compile_trading_lab.py` (`build_scripts/compile_trading_lab.py`): orquestrador de build automatizado executando PyInstaller, validação da presença de `TradingLab.exe`, varredura fail-closed de segredos via `SecretScanner`, geração atômica de `release_manifest.json` e auto-verificação de integridade via `ReleaseIntegrityVerifier`.
- `TradingLab_Setup.iss` (`build_scripts/TradingLab_Setup.iss`): script do Inno Setup para compilação do instalador `TradingLab_Setup_v1.0.0.exe` em modo x64 com atalhos no Menu Iniciar/Área de Trabalho e desinstalador limpo.
- Teste de fumaça (`tests/integration/test_distribution_build_smoke.py`): validação da integridade de `TradingLab.spec`, `version_info.txt`, criação de manifesto com SHA-256 e aborto imediato fail-closed na detecção de qualquer segredo/arquivo confidencial.
**Decisões:** O pacote final exclui rigorosamente arquivos `.db`, `.vault`, `.env`, `.log` e diretórios de testes. O manifesto de integridade `release_manifest.json` com SHA-256 é embutido na raiz da distribuição para validação automática no startup do Launcher.
**Validação executada:** 
- Execução real do compilador `python build_scripts/compile_trading_lab.py` gerando `dist/TradingLab/TradingLab.exe` (4.6 MB), 393 arquivos empacotados, verificação de segredos 100% limpa e integridade do manifesto SHA-256 confirmada.
- Teste de integração de fumaça (`tests/integration/test_distribution_build_smoke.py`) aprovado com 3 testes cobrindo existência de specs, integridade de staging e rejeição fail-closed de vazamento de segredos.
- Suíte completa do repositório aprovada: **488 passed, 4 skipped, 0 failed** em `python -m pytest`.
- Verificações estáticas canônicas aprovadas: `ruff check`, `ruff format --check`, `mypy` (190 source files) e `compileall`.
**Resultado:** Pipeline de compilação Windows (`TradingLab.exe`) e gerador de instalador Inno Setup 100% funcionais, testados e validados.
**Riscos/limitações:** A compilação do instalador final `.exe` requer o compilador `ISCC.exe` (Inno Setup) instalado no host do Windows ou executado no runner de CI/CD.
**Próximo passo:** Prosseguir para os testes finais de aceitação e documentação do produto.

### WL-2026-08-22-06 — Correção e smoke completo do instalador Windows

**Objetivo:** corrigir o crash `NameError: name 'sys' is not defined` no entrypoint congelado, tornar o startup instalado independente do diretório de trabalho e comprovar o ciclo real de build, instalação, execução bounded e desinstalação do artefato Windows.
**Requisitos relacionados:** FR-002, FR-060, FR-072, FR-073; NFR-004, NFR-012, NFR-014, NFR-020; R-ARCH-001, R-ARCH-002, R-ARCH-007, R-ARCH-008; R-SEC-001, R-SEC-003; R-REL-001; AG-INV-004, AG-INV-005, AG-INV-007, AG-INV-008, AG-INV-011.
**Processo dono do estado e risco:** o Launcher continua dono apenas do lifecycle, `profile.lock`, Job Object e gate de integridade; o Core permanece a única autoridade financeira. Mudança classificada como risco operacional alto e risco financeiro nulo, sem alteração de estratégia, stake, ordem, reconciliação, licença ou broker real.
**Arquivos alterados:** `apps/launcher/cli.py`, `build_scripts/compile_trading_lab.py`, `build_scripts/TradingLab_Setup.iss`, `tests/unit/test_launcher_cli.py`, `tests/integration/test_distribution_build_smoke.py`, `README.md`, `SECURITY.md`, `TEST_PLAN.md`, `docs/RELEASE_PROCESS.md`, `WORKLOG.md`.
**Implementação:** import explícito de `sys`; teste da chamada real `main()` que lê `sys.argv`; derivação automática de `release_manifest.json` ao lado do executável congelado; perfil instalado em `%LOCALAPPDATA%\TradingLab\profiles\default`; execução do health-check do binário no pipeline PyInstaller e no pós-install do Inno Setup; `GetCustomSetupExitCode` propagando falha do health-check também em instalação silenciosa; `WorkingDir={app}` no lançamento; desinstalador movido para `%LOCALAPPDATA%\TradingLab\uninstall`, fora da raiz imutável verificada pelo manifesto. A fixture de segredo do teste de build passou a montar o nome proibido em runtime para continuar provando o bloqueio sem contaminar o scanner global do repositório.
**Timeout, crash, restart, duplicidade e expiração:** timeout ou exit code não zero no health-check empacotado aborta o build; health-check instalado não inicia subprocessos; startup normal adulterado falha fechado antes do Core; shutdown bounded preserva safe stop, drain e Job Object; segunda instância continua bloqueada por `profile.lock`; restart posterior usa o mesmo perfil gravável e revalida o manifesto; não há retry financeiro nem mudança na política de `UNKNOWN`; expiração/revogação de licença permanece bloqueando somente novas entradas.
**Validação executada:** reprodução do traceback original em `python -m apps.launcher --post-update-health-check`; build PyInstaller real com 393 arquivos, scanner limpo, manifesto `219f14a472c9b3c34fe80d9b0bb2ebbe61e9883d59252d0369ad5d20c0c5c243` e health-check empacotado aprovado; compilação real com Inno Setup 6.4.1; smoke instalado em diretório temporário com `installer_exit=0`, arquivos presentes, `health_exit=0`, `app_exit=0`, registro apontando para o alvo validado, `uninstaller_exit=0`, diretório removido e zero processos órfãos. SHA-256 do setup final: `09009A1C8D04B49ED671295C37CB5CC9AE9F43699A72DFB52B601321C3A67FBD`. Suíte final: `491 passed, 4 skipped, 0 failed`; testes focais após o hardening do exit code: `8 passed`; `ruff check` e `ruff format --check` aprovados; `mypy` aprovado em 189 arquivos; `compileall` aprovado; `SecretScanner` limpo em 699 arquivos. Na primeira suíte completa, dois prazos de subprocesso Windows falharam sob carga (`launcher_process_tree` no shutdown e `reconciliation_protocol` no handshake); ambos passaram isoladamente e passaram novamente na segunda suíte completa, sem processo órfão ou alteração de timeout.
**Resultado:** `dist/TradingLab_Setup_v1.0.0.exe` instala, valida e inicializa a árvore local demo/simulada, encerra de forma bounded e desinstala sem violar a raiz de integridade.
**Riscos/limitações:** artefato ainda não possui assinatura Authenticode, SBOM ou CI em VM Windows limpa. O health-check pós-install prova integridade do pacote; o smoke separado prova bootstrap/shutdown. Os dois timeouts transitórios observados na primeira execução devem continuar visíveis como sinal de pressão de recursos, sem aumento cego de deadlines.
**Próximo passo:** automatizar o smoke de instalação/desinstalação em uma VM Windows limpa e assinar executável/instalador no pipeline de release antes de qualquer distribuição alpha/beta.

### WL-2026-08-23-01 — UX multi-corretora e configurações explicáveis

**Objetivo:** reorganizar a interface PySide6 após revisão profissional de UX/UI, separando Deriv e IQ Option em contextos próprios e removendo configurações ambíguas do cockpit operacional.
**Requisitos relacionados:** FR-012, FR-020, FR-070, FR-072, FR-073, FR-074, FR-075; R-ARCH-004, R-ARCH-007; R-UI-001, R-UI-002, R-UI-003, R-UI-004, R-UI-005, R-UI-006; AG-INV-004, AG-INV-006, AG-INV-007, AG-INV-010, AG-INV-011.
**Processo dono do estado e risco:** a UI continua dona apenas de navegação e projeções descartáveis. O Core permanece a única autoridade financeira e confirma todo valor efetivo. Mudança de risco médio de apresentação, sem alteração de protocolo, persistência, ordem, Risk Ledger, estratégia, worker, credencial ou licença.
**Arquivos alterados:** `apps/ui/app.py`, `apps/ui/formatting.py`, `apps/ui/i18n.py`, `apps/ui/theme.py`, `apps/ui/components/__init__.py`, `apps/ui/components/broker_card.py`, `apps/ui/components/order_table.py`, `apps/ui/components/risk_gauge.py`, `apps/ui/components/workspaces.py`, `tests/contract/test_pyside6_headless.py`, `tests/unit/test_ui_money_formatting.py`, `PRD_Trading_Desktop_Deriv_IQOption.md`, `README.md`, `docs/UI_INFORMATION_ARCHITECTURE.md`, `WORKLOG.md`.
**Implementação:** navegação principal com `Visão geral`, `Deriv`, `IQ Option`, `Atividade` e `Configurações`; abas de corretora com `Status` e `Configuração`; ordens filtradas por identidade exata `DERIV`/`IQ_OPTION`; atividade consolidada preservando abertas, `UNKNOWN` e reconciliação; quatro seções explicativas de configuração (`Aplicativo`, `Risco e segurança`, `Estratégias`, `Diagnóstico`); aviso inequívoco de que modo real não existe; barra persistente de Safe Stop/retomada/diagnóstico/fechamento seguro. Controles sem comando IPC confirmável não são simulados: aparecem como somente leitura, com explicação, escopo e valor efetivo projetado. Textos hardcoded relevantes foram internacionalizados em ES/EN. Valores monetários e percentual de exposição deixaram de usar `float` e passaram a formatação/cálculo por minor units inteiros.
**Decisões:** falha de uma corretora altera somente sua projeção; blockers globais permanecem na Visão geral. Broker desconhecido não é inferido por substring. Troca de aba nunca muda estado financeiro. Configuração financeira futura só poderá ganhar controle editável após comando IPC versionado, validação no Core e confirmação do valor efetivo. Modo real continua sem rota de UI.
**Timeout, crash, restart, duplicidade e expiração:** desconexão/restart do IPC marca o Core desconectado e a próxima projeção reconstrói os valores; não existe retry financeiro na UI. Ordens duplicadas permanecem responsabilidade idempotente do Core e todas as ordens projetadas seguem na aba Atividade. Safe Stop e expiração/revogação continuam bloqueando novas entradas sem abandonar ordens abertas/`UNKNOWN`.
**Validação executada:** parecer UX/UI somente leitura; inspeção visual Qt offscreen confirmando cinco abas, fundo Obsidian contínuo e isolamento 1:1 das ordens; 16 testes focais de UI/projeção/diagnóstico aprovados; suíte final `493 passed, 4 skipped, 0 failed`; `ruff check` e `ruff format --check` aprovados; `mypy` aprovado em 191 arquivos; `compileall` aprovado; `SecretScanner` limpo em 705 arquivos e scanner manual da UI sem uso monetário de `float`. Build PyInstaller real aprovado com 395 arquivos, manifesto `4725a3c533e859fec44908e25d0e0be8bdceffb1077566e894725e4a97b8bdb2` e health-check empacotado; setup recompilado e smoke instalado final com installer/health/app/uninstaller em exit code zero, alvo registrado conferido, pasta removida e zero órfãos. SHA-256 do setup: `3081646B6C99072A76807F4FF4B0E4AE03CC5A13CA9E8E23E388B2AB90393186`. Na primeira suíte completa, a tempestade simulada de 102 settlements excedeu seu deadline de 3 segundos; passou isoladamente em 2,17 s, não deixou órfãos e passou na segunda suíte completa, sem alteração de timeout ou código financeiro. O primeiro smoke instalado após build retornou `app_exit=1` sob carga; o mesmo pacote passou diretamente e no segundo smoke instalado com janela bounded de 0,5 s, sem alteração do runtime.
**Resultado:** as corretoras e suas atividades agora são visualmente independentes, configurações possuem contexto próprio e limitações claras, e o cockpit mantém ações críticas e visão consolidada sem misturar ownership financeiro.
**Riscos/limitações:** opções de broker, risco e estratégia permanecem somente leitura porque ainda não há comandos IPC de configuração confirmável. O plugin Qt offscreen deste host renderiza glifos quadrados até em `QLabel` sem tema; estrutura, contraste, foco, conteúdo e filtros foram validados, mas QA visual final de tipografia deve ocorrer no executável Windows nativo. Português do Brasil ainda não integra o catálogo i18n atual ES/EN.
**Próximo passo:** validar a navegação no executável Windows com escala 100/125/150%, teclado e leitor de tela; depois desenhar contratos IPC versionados para a primeira configuração realmente editável, sem ampliar para múltiplas fases.

## 8. Modelo para novas entradas

```markdown
### WL-AAAA-MM-DD-NN — Título curto

**Objetivo:**  
**Requisitos relacionados:**  
**Arquivos alterados:**  
**Implementação:**  
**Decisões:**  
**Validação executada:**  
**Resultado:**  
**Riscos/limitações:**  
**Próximo passo:**
```

