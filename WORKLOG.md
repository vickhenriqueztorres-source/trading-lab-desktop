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
| DEC-051 | Suporte a Bounded Martingale sob guardrails estritos | permitir gestão de stake progressiva delimitada por teto de steps/stake e stop loss, mantendo martingale ilimitado proibido |

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

### WL-2026-08-23-02 — Deriv Live Demo: execução, streaming e reconciliação

**Objetivo:** habilitar a Fatia 2 de execução automatizada de opções Deriv exclusivamente em conta
Demo, com acompanhamento de contratos, liquidação atômica e recuperação de envio ambíguo sem retry.
**Requisitos relacionados:** AG-INV-001, AG-INV-002, AG-INV-006; R-ORD-001, R-ORD-004,
R-ORD-006; R-RISK-009; R-UI-004; BR-014.
**Processo dono do estado e risco:** mudança financeira de risco alto. O Core permanece único dono
de `state.db`, reservas, estado da ordem e P&L; o Deriv Worker somente traduz o protocolo e emite
evidência normalizada; a UI continua descartável. O escopo executável termina em Demo `VRTC...`.
**Arquivos alterados:** `apps/deriv_worker/__init__.py`, `apps/deriv_worker/__main__.py`,
`apps/deriv_worker/fake_transport.py`, `apps/deriv_worker/order_session.py`,
`apps/deriv_worker/reconciliation.py`, `apps/core/coordinator.py`, `apps/core/ui_service.py`,
`apps/ui/components/order_table.py`, `packages/domain/models.py`, `packages/persistence/reader.py`,
`packages/protocol/ui_messages.py`, `tests/contract/test_deriv_live_order_contract.py`,
`tests/integration/test_deriv_live_trade_lifecycle.py`,
`tests/chaos/test_deriv_live_timeout_recovery.py`, `docs/DERIV_WORKER.md`,
`PRD_Trading_Desktop_Deriv_IQOption.md`, `WORKLOG.md`.
**Implementação:** wiring executável de `live-demo` para `DerivLiveOrderSession`; payload `buy` com
stake `Decimal`, CALL/PUT, duração `m`/`s` e passthrough imutável; rejeição local de deadline;
subscrição imediata e `forget` terminal; normalização `OPEN`/`SETTLED` com P&L em minor units e
SHA-256 canônico; timeout pós-envio como `UNKNOWN`; reconciliação por contrato, `statement` e
`profit_table` com matching de símbolo, direção, stake e moeda; UI com contrato Deriv e resultado
WON/LOST; simulador determinístico de settlement após queda.
**Decisões:** zero retry de `buy`; reserva permanece ativa enquanto a exposição for ambígua ou
aberta; conta real e account ID fora de `VRTC` falham antes do socket; Safe Stop não interrompe o
pump de eventos nem a liquidação; aliases antigos foram preservados para compatibilidade IPC.
**Validação executada:** 19 testes focais Deriv aprovados; suíte completa com **501 passed,
4 skipped, 0 failed**; `ruff check .` e `ruff format --check .` aprovados em 299 arquivos; `mypy apps
packages build_scripts` aprovado em 193 arquivos; `compileall apps packages build_scripts`
aprovado. Os quatro skips são gates de plataforma ou integrações externas explicitamente opt-in.
**Resultado:** o modo Demo opt-in possui ciclo local completo de persistência prévia, compra,
streaming, settlement e reconciliação idempotente; modo real permanece proibido.
**Riscos/limitações:** nenhuma chamada externa com token Demo foi executada nesta alteração; a
validação de integração é determinística/local. O contrato externo Deriv pode exigir evolução
versionada se o formato de `statement`/`profit_table` variar.
**Próximo passo:** executar soak externo Demo controlado com stake mínima e credencial fornecida
pelo operador, mantendo os testes externos explicitamente opt-in.

### WL-2026-08-23-03 — Login Deriv Demo protegido no executável Windows

**Objetivo:** tornar a conexão Deriv utilizável no `TradingLab.exe` por uma janela de login
Demo, sem token em arquivo texto, argv, IPC, UI principal ou serviço de identidade.
**Requisitos relacionados:** AG-INV-006; R-RISK-009; R-AUTH-011; R-AUTH-012; FR-010; FR-014;
NFR-030.
**Processo dono do estado e risco:** autenticação de broker de risco alto. O Deriv Worker continua
único dono do transporte e da abertura do token; Launcher e Core conhecem somente o caminho do
cofre. O Core permanece único dono do estado financeiro.
**Arquivos alterados:** `apps/launcher/cli.py`, `apps/launcher/deriv_login.py`,
`apps/launcher/supervisor.py`, `apps/core/lifecycle_service.py`,
`apps/core/read_only_worker_supervisor.py`, `apps/deriv_worker/__main__.py`,
`apps/deriv_worker/order_session.py`, `packages/brokers/deriv/credentials.py`,
`packages/brokers/deriv/__init__.py`, `tests/contract/test_deriv_demo_login.py`, `README.md`,
`docs/DERIV_WORKER.md`, `PRD_Trading_Desktop_Deriv_IQOption.md`, `WORKLOG.md`.
**Implementação:** diálogo pré-startup com App ID, conta Options Demo, token mascarado e confirmação
inequívoca; persistência DPAPI CurrentUser; auto-seleção `live-demo` após configuração; worker lê o
cofre diretamente; timeout de bootstrap externo ampliado; supervisor aceita capabilities
financeiras somente quando completas e explicitamente Demo; falha de login limpa o cofre e mostra
erro visível; conta `CR...` é bloqueada precocemente e a prova autoritativa usa
`account_type = demo` mais endpoint OTP Demo.
**Decisões:** o prefixo `VRTC` deixou de ser requisito porque a API Options atual pode fornecer IDs
de outro formato; nenhum formato textual libera execução sem prova REST/OTP Demo. Cancelar o login
mantém o aplicativo em modo público read-only. O bootstrap legado por ambiente fica restrito a
desenvolvimento controlado.
**Validação executada:** testes focais de launcher/Core/Deriv e quatro novos testes de contrato para
cofre, diálogo, account ID moderno e capability gate aprovados; suíte completa com **505 passed,
4 skipped, 0 failed**; `ruff check .` e `ruff format --check .` aprovados em 302 arquivos; `mypy apps
packages build_scripts` aprovado em 195 arquivos; `compileall` aprovado. Os skips são gates de
plataforma ou integrações Deriv externas explicitamente opt-in.
**Resultado:** o executável oferece onboarding Deriv Demo protegido e falha fechado antes do socket
quando a corretora não comprova conta Demo.
**Riscos/limitações:** OAuth PKCE com navegador ainda não está embutido; o fluxo atual aceita PAT ou
token OAuth já emitido com escopo `trade`. Nenhum teste externo foi executado sem credencial do
usuário.
**Próximo passo:** adicionar OAuth PKCE nativo para eliminar a colagem manual de token e executar
smoke externo Demo com stake mínima sob opt-in explícito.

### WL-2026-08-23-04 — Conexão Deriv movida para dentro do aplicativo

**Objetivo:** preservar a inicialização normal do `TradingLab.exe` e disponibilizar App ID, conta
Demo e conexão somente dentro da aba Deriv.
**Arquivos alterados:** `apps/launcher/cli.py`, `apps/launcher/deriv_login.py`,
`apps/deriv_login_helper/`, `apps/launcher/process_controller.py`, `apps/ui/runner.py`,
`apps/ui/app.py`, `apps/ui/components/workspaces.py`, `apps/ui/i18n.py`,
`apps/ui/ipc_client.py`, `apps/ui/controller.py`, `apps/core/ui_service.py`,
`apps/core/lifecycle_service.py`, `apps/core/deriv_telemetry.py`,
`packages/protocol/envelope.py`, `build_scripts/TradingLab.spec`, testes e documentação.
**Implementação:** o launcher volta a iniciar em `fake-public`, sem janela pré-startup. A área
`Deriv > Configuração` ganhou o botão `Conectar Deriv Demo`; ele abre um helper separado que grava
o token diretamente no cofre DPAPI. A UI principal envia somente um comando IPC sem segredo. O
Core encerra o worker público, inicia `live-demo` a partir do cofre e restaura o worker anterior em
caso de falha. Credenciais salvas podem ser reutilizadas pelo botão sem nova digitação.
**Decisões:** conta real continua bloqueada no diálogo e pela prova oficial
`account_type = demo`; cancelamento ou falha não fecha a ferramenta; o token não entra no processo
principal da UI, no Core, no argv do worker nem no IPC.
**Resultado:** a ferramenta abre como antes e a conexão Deriv ocorre somente depois que o usuário
entra na área interna da corretora.
**Riscos/limitações:** o primeiro cadastro ainda exige que o usuário obtenha um PAT/OAuth com
permissão `trade`; OAuth PKCE embutido permanece futuro.

### WL-2026-08-23-05 — Deriv API Token interno, seleção Demo/Real e release 1.1.0

**Objetivo:** substituir o cadastro manual de App ID/conta por um fluxo interno token-only, listar
as contas Options confirmadas pela Deriv, permitir escolha explícita Demo ou Real e entregar um
executável Windows atualizado sem alterar o startup público read-only.
**Requisitos relacionados:** AG-INV-001, AG-INV-002, AG-INV-004, AG-INV-005, AG-INV-006,
AG-INV-008, AG-INV-010, AG-INV-011 e AG-INV-012; R-ORD-001 a R-ORD-008; R-RISK-001 a
R-RISK-009; R-SEC-001, R-SEC-002 e R-SEC-006; R-UI-001 a R-UI-006; FR-015, FR-074, FR-098.
**Processo dono do estado e risco:** mudança financeira e de autenticação de risco alto. O Core
permanece a única autoridade financeira; o Deriv Worker mantém o transporte e o token; o helper
isolado grava o cofre; a UI mantém somente confirmação/projeção. A autorização explícita de produto
para Deriv Real foi registrada no PRD; IQ Option Real não foi autorizada.
**Arquivos alterados:** configuração/credenciais Deriv, helper de login, worker, validadores,
mapper, sessão de ordem/reconciliação, supervisor/lifecycle/Core, Auth Agent/lease, protocolo/UI,
testes, build/versionamento e documentação normativa.
**Implementação:** App ID público do produto incorporado internamente; diálogo recebe somente PAT,
consulta contas ativas e não pré-seleciona conta; Demo ordenada antes de Real; Real exige checkbox e
digitação de `REAL`; DPAPI persiste somente conta/tipo/token; worker comprova conta e endpoint OTP do
mesmo tipo; troca bloqueada com ordem aberta; capabilities e telemetria distinguem Demo/Real; UI
marca `REAL — DINHEIRO REAL`; lease Real Ed25519 limitada a 24 horas; Health Gate, persistência,
risco e ausência de retry permanecem obrigatórios. O fluxo de compra foi atualizado para a API
atual: `proposal` com `underlying_symbol`, seguido de `buy` pelo ID da proposta e acompanhamento do
contrato.
**Decisões:** startup continua em `fake-public`; conta Real nunca é automática; testes externos
começam e terminam em Demo; nenhum trade Real é permitido em desenvolvimento/aceitação; token não
entra em fonte, argv, IPC financeiro, log, fixture, relatório ou pacote; App ID não é segredo; o
serviço de identidade recebe somente uma decisão reduzida de autorização.
**Validação executada:** token fornecido pelo operador validado sem impressão e guardado apenas em
cofre DPAPI temporário de teste; a API retornou uma conta Options Demo ativa e nenhuma conta Real.
Leitura externa aprovada com moeda USD, relógio sincronizado e 89 símbolos. Smoke financeiro externo
Demo aprovado com proposta, compra mínima de USD 1,00, liquidação comprovada e nova leitura de saldo;
nenhuma ordem Real foi enviada. Adaptação de schema adicionada para `active_symbols` sem
`underlying_symbol_type`. Suíte completa final: **511 passed, 4 skipped, 0 failed**; 69 testes focais
aprovados; `ruff check` e `ruff format --check` aprovados em 305 arquivos; `mypy` aprovado em 198
arquivos; `compileall` aprovado. PyInstaller 1.1.0 gerou `TradingLab.exe`; scanner do pacote e do ZIP
extraído encontrou zero segredos; manifesto interno com 284 entradas e SHA-256
`c0a9ab989d43d593b256834bc45c33e75b294f1203de0af47253ccdd2bc6972a`; ZIP extraído passou
verificação do manifesto e smoke completo da árvore com exit code zero e nenhum processo órfão.
**Resultado:** release onedir/ZIP 1.1.0 pronta para o cliente, com conexão interna exclusivamente por
API Token e seleção controlada Demo/Real.
**Riscos/limitações:** o token usado no teste não possui conta Real associada, portanto a rota Real
foi comprovada somente por testes locais/fakes e lease assinada; não houve e não deve haver teste com
dinheiro real. O Auth Agent/issuer continua simulado localmente e a distribuição ainda não possui
assinatura Authenticode. Como o token foi compartilhado em conversa, deve ser rotacionado pelo
proprietário depois da validação.
**Próximo passo:** validar a conta Real apenas por descoberta/leitura quando o proprietário fornecer
um token que a contenha; antes de distribuição comercial, substituir o issuer simulado, executar QA
em VM Windows limpa e assinar o pacote com Authenticode.

### WL-2026-08-23-06 — Correção da confirmação Deriv após troca de worker

**Objetivo:** corrigir o erro visível `UI_IPC_UNAVAILABLE` ocorrido depois que o Core já havia
conectado com sucesso o worker Deriv autenticado.
**Requisitos relacionados:** R-ARCH-004, R-ARCH-008, R-ORD-004, R-STATE-003, R-UI-003;
AG-INV-002, AG-INV-004 e AG-INV-008.
**Processo dono do estado e risco:** correção de IPC de risco operacional médio. O Core continua dono
do comando e de seu cache de replay; a UI não recebe autoridade financeira. Nenhuma mudança foi
feita em stake, risco, token, ordem, liquidação ou seleção de conta.
**Arquivos alterados:** `apps/ui/ipc_client.py`, `apps/ui/app.py`,
`tests/contract/test_ui_ipc_contract.py`, metadados/pipeline de versão, documentação de release e
`WORKLOG.md`.
**Implementação:** timeout do comando de troca Deriv ampliado para 120 segundos; o cliente UI agora
mantém somente a capability efêmera necessária para restabelecer o canal loopback autenticado;
falha de transporte aciona uma única reconexão e reenvia o mesmo envelope, com o mesmo
`message_id`. O cache bounded do Core devolve a resposta anterior quando o efeito já ocorreu, de
modo que uma confirmação perdida não repete a ação. A janela diferencia falha interna de IPC de
falha de token e informa que a credencial DPAPI foi preservada.
**Decisões:** não usar retry financeiro; a repetição existe somente no plano de controle local e
preserva identidade idempotente. Se o Core não responder após a tentativa bounded, a UI continua
falhando fechado. O token não participa da reconexão UI/Core.
**Validação executada:** reprodução comprovou que, mesmo com o alerta, o Core havia iniciado o
worker `live-demo`. Foram adicionados testes de resposta perdida durante callback lento e conexão
encerrada por ociosidade; ambos reconectam e comprovam exatamente um efeito. Testes focais de UI,
IPC, launcher e lifecycle: 18 aprovados. Suíte completa: **513 passed, 4 skipped, 0 failed**; Ruff,
format, mypy em 198 arquivos e compileall aprovados. Build Windows 1.1.1 aprovado; manifesto com 284
entradas e SHA-256 `4cf9b3c6d38eed6b3e8d53551185e513be779834be83ff474aa9f2a4f0a798d6`;
ZIP extraído com manifesto válido, zero segredos, smoke da árvore com exit code zero e zero órfãos.
O executável empacotado também passou smoke externo autenticado Demo, somente conexão/leitura, com
exit code zero e sem enviar nova ordem.
**Resultado:** a troca para Deriv autenticada não apresenta falha de token quando apenas a resposta
IPC se perde; a confirmação é recuperada de forma idempotente e bounded.
**Riscos/limitações:** o teste visual final exige abrir a nova versão depois de encerrar as instâncias
antigas que ainda mantêm o perfil padrão bloqueado. Nenhum teste Real foi executado.
**Próximo passo:** substituir a versão 1.1.0 pela 1.1.1 no ambiente do usuário e confirmar a projeção
`DEMO LIVE` após reutilizar a credencial salva.

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

### WL-2026-08-24-01 — Risco especializado e painel DIGITDIFF

**Objetivo:** implementar configuração imutável, travas financeiras e painel PySide6 ES/EN para
operações Deriv `DIGITDIFF`.
**Requisitos relacionados:** AG-INV-010, R-DB-002, R-RISK-001, R-RISK-005, BR-002, BR-010 e
R-ARCH-004.
**Arquivos alterados:** `apps/core/digit_risk_config.py`, `apps/core/risk.py`,
`apps/core/health.py`, `apps/core/broker_events.py`, `apps/core/ui_service.py`, protocolo UI,
cliente/controlador/UI PySide6, testes e documentação.
**Implementação:** `DigitRiskConfig` frozen com dinheiro em minor units, confiança em `Decimal`,
allowlist de índices sintéticos e validação por reason code; Risk Ledger aplica Stop Loss, Take
Profit e cooldown por relógio monotônico; evento `SETTLED` aplica P&L uma única vez; IPC autenticado
atualiza e projeta a configuração; painel Obsidian Dark fornece validação visual, conversão exata
USD/minor units, seletor de ativo/cooldown, slider e i18n ES/EN.
**Decisões:** configuração permanece em memória do Core nesta fatia; UI nunca escreve no banco;
travas de Stop/Take não são removidas pela edição de limites no mesmo dia; contratos abertos seguem
até liquidação. “Confiança quântica” é somente um limiar configurável, sem afirmação de probabilidade
calibrada, lucro ou vantagem estatística.
**Validação executada:** testes de modelo, gates, cooldown monotônico, roundtrip IPC, paridade i18n e
renderização Qt offscreen adicionados. Suíte completa: **518 passed, 4 skipped, 0 failed**; Ruff,
format, mypy em 200 arquivos e compileall aprovados. Build Windows 1.2.0 gerou manifesto com 286
arquivos e SHA-256 `afb31e400d1a8de98a3ab5de25f07fb73670cfc54e4f757fd37d129a924f2952`;
ZIP extraído passou a verificação do manifesto e o smoke visual confirmou seis processos, janela
`Trading Lab Desktop — MODO PRÁCTICA` responsiva e encerramento limpo.
**Resultado:** infraestrutura especializada de risco e painel configurável integrados ao fluxo
Core/UI para contas Deriv Demo ou Real selecionadas pelo cliente; release 1.2.0 pronta para entrega.
**Riscos/limitações:** a configuração ainda não é persistida entre reinicializações e esta fatia não
implementa nem valida uma estratégia lucrativa. Nenhuma ordem Real é usada em testes.
**Próximo passo:** persistir versões de configuração em armazenamento Core dedicado e integrar o
limiar a uma estratégia `DIGITDIFF` somente após validação estatística independente.

### WL-2026-08-24-02 — Motor O(1) de ticks e contrato DIGITDIFF de 1 tick

**Objetivo:** implementar janela circular fixa de ticks, telemetria de frequência 0–9 e execução
Deriv `DIGITDIFF` de um tick sem violar commit prévio, risco ou reconciliação.
**Requisitos relacionados:** AG-INV-001, AG-INV-010, R-DB-002, R-ORD-001, R-DATA-002 e
R-TEST-001.
**Arquivos alterados:** `packages/market_data/tick_ring_buffer.py`, contratos Deriv, domínio/outbox,
`apps/deriv_worker/tick_stream.py`, sessão/servidor/transport fake, cliente e telemetria Core,
protocolo UI, `DigitFrequencyWidget`, testes, documentação e metadados de release.
**Implementação:** `DigitTick` frozen/slots e `TickRingBuffer` com array estático, contadores de dez
dígitos e matriz 10x10 atualizados em O(1), inclusive na ejeção; stream `ticks` oficial, deduplicação
e latência monotônica; um buffer isolado por símbolo e remoção no unsubscribe; `prediction_digit`
persiste no payload da outbox; compra direta oficial `DIGITDIFF` com stake, barreira e duração `1 t`;
`proposal_open_contract` confere `exit_tick`/`exit_spot` e usa `profit` oficial; UI mostra dez barras
verticais no tema Obsidian, maior frequência em âmbar e menor em ciano.
**Decisões:** contratos não-DIGITDIFF conservam proposta seguida de compra por ID; conflito entre
resultado oficial e dígito de saída falha fechado; frequência histórica é apenas telemetria, nunca
previsão, sinal ou promessa de lucro; eventos de mercado e de ordem usam filas IPC bounded separadas.
**Validação executada:** benchmark local de 10.000 inserções mediu **10,057 µs/tick** em média
(limite 100 µs), janela final 500/500; testes de escala decimal, ejeção, frequências, transições,
payload direto, vitória, derrota, fluxo IPC multi-símbolo e renderização Qt. Suíte completa:
**529 passed, 4 skipped, 0 failed**; Ruff check e format aprovados em 315 arquivos; mypy estrito
aprovado em 203 arquivos; compileall aprovado. Build PyInstaller v1.3.0 aprovado, scanner encontrou
zero segredos, manifesto verificou 289 entradas com SHA-256
`12de82a522ccde61ab8864c293c6f95330f972eba003c15d1ee7b1f2e5dd812b`; executável SHA-256
`6342336676ab97362415d2506bd2066c6d6ea5d51c5dbab15ad889eb68caa4e9` e health check retornou
zero. O ZIP final tem SHA-256
`aedd414f68996764f66179ff5e7d09cc97d123f3435dc35893171992af0e7707`; a extração isolada
revalidou o manifesto sem issues e o executável extraído repetiu o health check com sucesso.
**Resultado:** release Windows v1.3.0 pronta com motor bounded de ticks, painel de frequências ao
vivo e suporte controlado a `DIGITDIFF` de um tick para a conta Demo ou Real escolhida pelo cliente.
**Riscos/limitações:** nenhuma estratégia comercial ou vantagem estatística foi introduzida; testes
financeiros permanecem locais/fake nesta fatia e nenhuma ordem Real foi enviada. O pacote não possui
assinatura Authenticode.
**Próximo passo:** executar soak externo somente de ticks em Demo, sem compra, e validar a ergonomia
visual em VM Windows limpa antes de distribuição comercial.

### WL-2026-08-24-03 — Três estratégias sintéticas Deriv em observação

**Objetivo:** substituir a experiência centrada em frequência de dígitos por três estratégias
especializadas para mercados sintéticos Deriv e entregar novo executável Windows.
**Requisitos relacionados:** AG-INV-001, AG-INV-010, R-DATA-002, R-ORD-001, R-RISK-001,
R-TEST-001 e diretrizes de validação do `STRATEGY_PLATFORM.md`.
**Arquivos alterados:** `packages/strategies/deriv_synthetic.py`, telemetria Core,
protocolo UI, painel Deriv PySide6, lifecycle financeiro, testes, README, documentação de estratégia
e metadados de release.
**Implementação:** foram adicionadas as estratégias `Range Boundary Reversion`,
`Post-Spike Drift Recovery` e `Five-Tick Run Reversal` para RB100/RB200, BOOM500/CRASH500 e Step 500.
O Core agora assina os mercados necessários, aquece histórico limitado, atualiza candles/ticks em
tempo real, publica estado de aquecimento, bloqueio de dados e sinal em observação para a UI, sem
despachar ordens financeiras automáticas. A aba Deriv ganhou seletor profissional de estratégias,
cards de status e painel de execução em modo pesquisa.
**Decisões:** a antiga estratégia de frequência de dígitos foi retirada da navegação principal; as
novas estratégias entram como `RESEARCH_SHADOW` até existir validação estatística e aprovação de
promoção para execução. A conexão Demo/Real por token permanece disponível, mas a composição do Core
não inicia auto-trader para essas estratégias nesta versão.
**Validação executada:** suíte completa com **549 passed, 4 skipped, 0 failed**; suíte direcionada de
telemetria/estratégias/UI/contratos com **15 passed**; Ruff aprovado; mypy aprovado em 207 arquivos;
smoke de inicialização em código-fonte e no pacote PyInstaller aprovados; build v1.6.0 com scanner de
segredos limpo, health check do launcher aprovado, manifesto com 295 arquivos e SHA-256
`a43bcd573f2fe823dbd7ad6c7d56dfd5ac64c03fb83c709904fb6d1a379049cf`.
**Resultado:** EXE `TradingLab-Desktop-v1.6.0-3-ESTRATEGIAS-DERIV.exe` entregue na pasta `outputs`
com SHA-256 `6F57147C10DAF1CF968573F44D9D8A716138C924A4E69564BC97651C50E892FF`.
**Riscos/limitações:** as estratégias ainda não prometem lucro e não enviam operações reais ou demo
automaticamente; elas servem para observar sinais, qualidade dos dados e latência antes de liberar
execução financeira.
**Próximo passo:** rodar sessão monitorada em Demo, coletar evidência por estratégia e só então
decidir qual delas será promovida para execução com travas financeiras completas.

### WL-2026-08-24-04 — Portfólio Deriv Digit Edge e bloqueio financeiro Real

**Objetivo:** substituir as três estratégias sintéticas anteriores por hipóteses especializadas em
contratos de dígitos e entregar um executável Windows verificado.
**Requisitos relacionados:** AG-INV-001, AG-INV-010, R-DATA-002, R-ORD-001, R-RISK-001,
R-RISK-005, R-TEST-001 e critérios de promoção do `STRATEGY_PLATFORM.md`.
**Arquivos alterados:** `packages/strategies/deriv_digits.py`, exportações de estratégias,
telemetria e lifecycle Core, sessão pública e sessão financeira Deriv, protocolo UI, workspace e
painel de estratégias PySide6, testes, documentação e metadados de release.
**Implementação:** `Tail Probability Edge` compara Over/Under em três janelas condicionais;
`Selective Differs Edge` escolhe o dígito de menor probabilidade condicional; `Parity Regime Edge`
procura concordância Even/Odd. As três usam aquecimento de 500 ticks, janelas 200/350/500, limites
conservadores de Wilson a 99%, buffer bounded e uma única análise compartilhada por tick. A carga
de `ticks_history` foi alinhada ao formato atual da API, sem enviar `subscribe: 0`. A UI mostra
contrato, barreira, probabilidade conservadora, piso exigido e latência local em microssegundos.
**Decisões:** as estratégias permanecem em `RESEARCH_SHADOW` e não possuem método de compra. O modo
Real foi fechado para submissão financeira e não recebe sessão/capability de ordens; somente Demo
pode possuir infraestrutura financeira, ainda desacoplada destes sinais. Martingale, Soros e
progressão após perda permanecem proibidos por `R-RISK-005`; stake fixa, stop diário e cooldown não
foram enfraquecidos.
**Validação executada:** suíte completa com **550 passed, 4 skipped, 0 failed**; teste externo
público Deriv aprovado, com carregamento real de 500 ticks de R_100 e avaliação das três
estratégias; benchmark local de 1.000 avaliações mediu mediana **2.162 µs**, p95 **6.973 µs**, p99
**9.732 µs** e máximo **13.573 µs**. Uma amostra externa completa foi analisada em **6.268 µs**.
Ruff check/format, mypy estrito em 207 arquivos e compileall foram aprovados. Build v1.7.0 passou
scanner de segredos, verificação do launcher e manifesto de 295 arquivos, SHA-256
`ee4bd044bd75249a89b08f6d022c181a9df5709260fa702197076ade315c89b6`. O binário compilado abriu e
encerrou em smoke controlado com código 0. O SFX final foi extraído novamente, confirmou 507 entradas
e os arquivos obrigatórios; SHA-256 do EXE:
`270EF8CD6F1119416A2FE2736F3F40A7DCAD7FA4B8A331E43723C827F928E8A5`.
**Resultado:** release `TradingLab-v1.7.0.exe` entregue como arquivo único portátil com as três
estratégias de dígitos funcionando em observação e telemetria de latência disponível na interface.
**Riscos/limitações:** não existe “delay zero”; o tempo local ficou abaixo de 10 ms no p99 medido,
mas rede, cotação e resposta da Deriv continuam externos. Sinal estatístico não implica lucro e
nenhuma ordem Demo ou Real foi enviada pelos testes. O pacote não possui assinatura Authenticode.
**Próximo passo:** coletar amostra walk-forward em Demo, registrar propostas/payout disponíveis e
somente promover uma estratégia depois de evidência fora da amostra e aprovação explícita das
travas financeiras.

### WL-2026-08-24-05 — Identificação inequívoca da release v1.7.1

**Objetivo:** corrigir a percepção de versão antiga no executável portátil e tornar a release
identificável tanto nas propriedades do Windows quanto dentro da interface.
**Requisitos relacionados:** R-TEST-001 e processo de release documentado.
**Arquivos alterados:** `apps/ui/app.py`, `pyproject.toml`, metadados/scripts de build,
`build_scripts/PortableLauncher.cs`, testes de UI/distribuição e este worklog.
**Implementação:** a interface e o título da janela agora exibem `v1.7.1`, com badge permanente
`DIGIT EDGE`. O autoextrator IExpress, que herdava `FileVersion` do Windows, foi substituído por um
launcher portátil versionado que incorpora o payload verificado, repassa argumentos ao launcher
interno, espera seu encerramento e remove a extração temporária.
**Decisões:** o nome da entrega mudou para `TradingLab-Desktop-v1.7.1-DIGIT-EDGE.exe` para não ser
confundido com releases anteriores. Nenhuma regra de estratégia, ordem ou risco foi modificada.
**Validação executada:** seis testes direcionados de UI/build aprovados; Ruff aprovado e mypy
estrito aprovado em 207 arquivos. Build interno v1.7.1 passou scanner de segredos, health check e
manifesto com 295 arquivos, SHA-256
`a1cf952a4c7479a316a73e75a105ab44a69bc8f5b63993095a65855f77abe342`. O EXE portátil confirmou
`FileVersion 1.7.1.0`, `ProductVersion 1.7.1`, health check com código 0 e SHA-256
`5CD73C950478ED1745034CFA478EB6358BA4F137CF7B985D26B2954C6AB2A896`. Smoke com encerramento
automático deixou zero processos e zero pastas temporárias da v1.7.1.
**Resultado:** novo arquivo único portátil com versão externa e interna coerentes, interface
claramente identificada e conteúdo Digit Edge atualizado.
**Riscos/limitações:** o executável ainda não possui assinatura Authenticode; o primeiro startup
precisa extrair o payload em diretório temporário e pode levar alguns segundos.
**Próximo passo:** distribuir somente o nome v1.7.1 e arquivar releases antigas depois de confirmação
do usuário.

### WL-2026-08-24-06 — Processo de exceção e especificação de Bounded Martingale na documentação

**Objetivo:** atualizar os documentos normativos, arquiteturais e de produto do repositório para permitir a introdução e o suporte a estratégias com Bounded Martingale (Martingale Estritamente Delimitado) sob rigorosos guardrails de risco.
**Requisitos relacionados:** R-RISK-005, R-RISK-001, R-RISK-002, R-RISK-003, R-RISK-004, AG-INV-001, AG-INV-004, BR-012, A-07, DEC-051.
**Arquivos alterados:** `RULES.md`, `AIGUARD.md`, `PRD_Trading_Desktop_Deriv_IQOption.md`, `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`, `STRATEGY_PLATFORM.md`, `WORKLOG.md`.
**Implementação:** aplicada a Seção 13 de `RULES.md` (Processo de Exceção). A regra `R-RISK-005` e o guardrail `AIGUARD` foram reformulados para proibir expressamente o martingale ilimitado ou sem controle prévio de risco, permitindo o Bounded Martingale como modelo de gestão de stake do Portfolio Allocator subordinado ao Risk Ledger, com travas obrigatórias:
1. Teto mandatório de etapas (`max_steps`);
2. Teto financeiro absoluto de stake (`max_stake_cap`);
3. Validação e reserva atômica de risco no Risk Ledger antes do envio ao worker;
4. Parada imediata com falha fechada (`HG_DAILY_STOP_REACHED` / `RISK_LOCKED`) ao atingir o Stop Loss diário ou esgotar o saldo livre;
5. Desacoplamento total: estratégias permanecem puras geradoras de sinais; o cálculo da progressão é restrito ao Core.
**Decisões:** DEC-051; o modelo padrão continua sendo stake fixa com opt-in explícito do usuário para progressão delimitada e visualização da perda máxima projetada da sequência.
**Validação executada:** varredura de consistência em todos os documentos markdown do projeto (`RULES.md`, `AIGUARD.md`, `PRD`, `Arquitetura`, `STRATEGY_PLATFORM.md`, `WORKLOG.md`); verificação de ausência de conflitos de normas e garantia de integridade estrutural.
**Resultado:** documentação normativa, funcional e arquitetural 100% atualizada e alinhada para permitir o desenvolvimento da funcionalidade de Bounded Martingale.
**Riscos/limitações:** a documentação autoriza e especifica os limites do modelo Bounded Martingale; a implementação de código da máquina de estados no Portfolio Allocator/UI permanece sujeita aos testes de integridade financeira e unitários correspondentes.
**Próximo passo:** implementar a máquina de estados de Bounded Martingale no módulo de gestão de stake / Portfolio Allocator com cobertura de testes para todos os limites de etapas e stop loss.

### WL-2026-08-24-07 — Bounded Martingale compartilhado pelas três estratégias Digit Edge

**Objetivo:** implementar o Bounded Martingale autorizado pela DEC-051 para Tail Probability Edge,
Selective Differs Edge e Parity Regime Edge, preservando a separação estratégia → alocação → risco.
**Requisitos relacionados:** AG-INV-001, AG-INV-004, AG-INV-009, AG-INV-010, R-RISK-001,
R-RISK-002, R-RISK-003, R-RISK-005, R-RISK-006, R-RISK-008, R-TEST-001, BR-012 e DEC-051.
**Arquivos alterados:** `packages/portfolio_allocation/martingale.py`, configuração e Risk Ledger
do Core, processamento de liquidações, protocolo/projeção UI, configuração e resumo das estratégias,
testes, README, plano de testes e metadados/pacote da release v1.8.0.
**Implementação:** o Portfolio Allocator recebeu matemática determinística em `Decimal` para stake
base, multiplicador, etapas e teto absoluto; o Risk Ledger mantém a etapa sequencial, calcula a
próxima stake, exige correspondência exata na reserva e atualiza a progressão somente após
liquidação confirmada. Ganho, empate ou perda na última etapa reiniciam a stake base. Over/Under,
Digit Differs e Even/Odd usam a mesma autoridade bounded do Core. A validação rejeita multiplicador
fora de 1,10–3,00, mais de quatro etapas, teto inferior à stake base, sequência maior que o Stop
Loss, limite de perdas insuficiente e mudança durante sequência ativa. Se a próxima perda possível
ultrapassar o orçamento diário restante, `HG_DAILY_STOP_REACHED` fecha novas entradas antes do
envio. A UI oferece opt-in desativado por padrão, multiplicador, etapas, teto de stake e projeção
completa da sequência/perda máxima; o resumo mostra etapa e próxima stake.
**Decisões:** Bounded Martingale é uma gestão compartilhada do Core, não parte da lógica estatística
das estratégias. A configuração padrão continua stake fixa. Desativar a progressão durante uma
sequência é permitido como ação segura e reinicia a etapa; aumentar ou alterar a progressão durante
a sequência falha fechado. Modo Real permanece read-only e nenhum teste usa dinheiro real.
**Validação executada:** suíte completa com **565 passed, 4 skipped, 0 failed**; testes específicos
cobrem projeção 100→200→400, teto, reset por ganho/empate/última etapa, Stop Loss projetado, mudança
durante sequência, stake divergente e as cinco famílias de contrato usadas pelas três estratégias.
Ruff check/format aprovado em 329 arquivos, mypy estrito aprovado em 208 arquivos e compileall
aprovado. Build v1.8.0 passou scanner de segredos, health check e manifesto com 296 arquivos,
SHA-256 `965c3590fb0b095e382d64869b80fa13df17bd5df2e97398f2ee103d4d2cde1a`. O EXE portátil confirmou
`FileVersion 1.8.0.0`, health check e smoke de abertura com código 0, zero processos/pastas
temporárias remanescentes e SHA-256
`3E27CD9028B816891A242B4B1D867724238FA6D674A6AF0A78EF8402D7AD4987`.
**Resultado:** `TradingLab-Desktop-v1.8.0-BOUNDED-MARTINGALE.exe` entregue com opt-in delimitado e
gestão central de progressão disponível na configuração das três estratégias.
**Riscos/limitações:** Bounded Martingale aumenta exposição e risco de perda mesmo com limites; não
cria vantagem estatística nem promessa de recuperação. As três estratégias continuam
`RESEARCH_SHADOW`, portanto a progressão está pronta no caminho de risco, mas não promove nem ativa
despacho financeiro automático por si só. O estado de etapa Digit ainda é mantido em memória e
reinicia de forma conservadora para stake base após reinício do Core.
**Próximo passo:** persistir a etapa/versionamento da configuração no writer único, executar
walk-forward e sessão Demo supervisionada antes de considerar promoção de qualquer estratégia.

### WL-2026-08-24-08 — Execução Demo Digit Edge e recuperação supervisionada Deriv

**Objetivo:** corrigir a ausência de operações Demo, a perda de conexão autenticada e a falha de
abertura do aplicativo, mantendo o modo Real sem submissão financeira nesta release.
**Requisitos relacionados:** AG-INV-001, AG-INV-004, AG-INV-009, AG-INV-010, R-ORD-001,
R-RISK-001, R-RISK-005, R-DATA-002, R-TEST-001 e DEC-051.
**Arquivos alterados:** lifecycle, telemetria e auto-trader do Core; sessão, reconciliação e
transporte fake do worker Deriv; modelos de ordem; projeção/UI; cofre DPAPI; testes, documentação,
metadados e pacote da release v1.9.0.
**Implementação:** as três estratégias Digit Edge podem despachar em conta Demo as cinco famílias
de contrato de um tick (`DIGITOVER`, `DIGITUNDER`, `DIGITDIFF`, `DIGITEVEN`, `DIGITODD`) pelo mesmo
caminho persistente do Core. O bot inicia desligado, exige 500 ticks, aceita cada época de sinal no
máximo uma vez e mantém somente uma ordem em voo. A conta Real permanece explicitamente somente
leitura. Falhas de relógio, assinatura ou ticks bloqueiam novas entradas e solicitam recuperação
supervisionada: a sessão financeira e o worker são substituídos, um OTP novo é obtido, a telemetria
é reassinada e ordens não terminais são reconciliadas, sem reenvio cego da compra anterior. O
backoff é limitado a 0, 1, 2, 5, 10 e 30 segundos. A abertura também foi corrigida para perfis com
caminho longo no Windows: o arquivo temporário atômico do cofre não repete mais o digest de 64
caracteres, evitando exceder o limite legado de caminho antes de a UI iniciar.
**Decisões:** execução automática fica restrita à Demo para validação; selecionar Real não habilita
compras. Nenhuma ordem é repetida automaticamente após uma queda, pois um sinal de um tick já pode
estar obsoleto. Não existe promessa de conexão sem falhas; a prevenção adotada é falhar fechado,
recuperar a sessão e exigir estado saudável antes de novas entradas.
**Validação executada:** suíte completa com **577 passed, 4 skipped, 0 failed**; o teste de regressão
do cofre confirmou escrita DPAPI em perfil longo; Ruff check/format aprovado em 330 arquivos, mypy
estrito aprovado em 210 arquivos e compileall aprovado. O processo completo em código-fonte e o
binário PyInstaller iniciaram Auth Agent, Core, worker simulado, worker Deriv e UI em estado
`READY`, com encerramento limpo. O build passou scanner de segredos (0 achados), manifesto de 296
arquivos e SHA-256 do manifesto
`5916099985ab5dc76f0c61b4b74038c740d8004e9dc6db0db8e9ec87f5154fbc`. O portátil passou smoke
completo e health check com código 0 e deixou zero pastas temporárias.
**Resultado:** `TradingLab-Desktop-v1.9.0-DERIV-FIXED.exe`, `FileVersion 1.9.0.0`, SHA-256
`04516DFF46E139D89D484CA112AFA4B0F9D2FD20C5ED734743B6557E189276E0`.
**Riscos/limitações:** nenhum teste enviou ordem para conta Real; os testes financeiros externos de
Demo continuam opt-in e não reutilizaram token fornecido em conversa. Bounded Martingale continua
opt-in e aumenta o risco mesmo limitado. O executável ainda não possui assinatura Authenticode.
**Próximo passo:** conectar uma conta Demo pela interface, manter o bot desligado até a telemetria
mostrar 500 ticks e então executar uma sessão supervisionada antes de qualquer distribuição ampla.

### WL-2026-08-25-09 — Inicialização estável, paginação de ticks e depuração das três estratégias

**Objetivo:** corrigir a aplicação que aparentava não abrir, eliminar a queda da conexão Demo após
o aquecimento e validar Tail Probability Edge, Selective Differs Edge e Parity Regime Edge dentro
do mesmo caminho executável usado pela aplicação.
**Requisitos relacionados:** AG-INV-001, AG-INV-004, AG-INV-009, AG-INV-010, R-DATA-002,
R-ORD-001, R-RISK-001, R-TEST-001 e invariantes do IPC v1.
**Arquivos alterados:** servidor e sessão pública do worker Deriv, telemetria e auto-trader do Core,
supervisor do Launcher, textos/projeções da UI, testes de contrato/unidade/integração, documentação
do worker, metadados e pacote da release v1.9.1.
**Implementação:** a investigação encontrou duas causas independentes. Instâncias antigas sem janela
mantinham `profile.lock`; a perda da UI agora encerra com segurança toda a árvore supervisionada e
libera o perfil. Além disso, a resposta única de 500 ticks ultrapassava o limite IPC de 64 KiB e
derrubava o worker. O worker agora limita cada página a 100 ticks e o Core compõe a janela de 500
por paginação regressiva com deduplicação. O único fluxo de recepção WebSocket autenticado foi
serializado entre comandos IPC e o pump de mercado, evitando consumidores concorrentes. Foram
adicionados testes completos para cada estratégia, cobrindo sinal do motor, auto-trader, Risk
Ledger, coordinator, compra Demo simulada e liquidação persistente: `DIGITOVER` para Tail,
`DIGITDIFF` para Selective Differs e `DIGITODD` para Parity.
**Decisões:** o limite de framing não foi afrouxado; payloads grandes continuam rejeitados e o
worker permanece utilizável. Nenhuma ordem Real é permitida. A validação externa autenticada usou
Demo com o bot pausado; o ciclo financeiro foi provado deterministicamente no caminho completo da
aplicação sem forçar uma operação externa quando o mercado não apresentou vantagem conservadora.
**Validação executada:** suíte completa com **583 passed, 4 skipped, 0 failed**; Ruff check e format
aprovados em 330 arquivos; mypy estrito aprovado em 210 arquivos; compileall aprovado. Em dados
públicos reais R_100, as três estratégias processaram 500 ticks e a análise ficou na faixa de
milissegundos. Na sessão autenticada Demo, a UI carregou saldo, relógio sincronizado, aquecimento
500/500 e permaneceu conectada por observação repetida sem eventos de desconexão. Tail e Parity
ficaram corretamente em monitoramento; Selective Differs encontrou sinal Demo elegível na amostra.
O portátil passou smoke isolado com código 0 e sem processo remanescente, depois abriu visivelmente
como `Trading Lab Desktop v1.9.1`, respondeu à automação e manteve o worker `live-demo` ativo.
Scanner de segredos: zero achados; manifesto com 296 arquivos e SHA-256
`e3e55777a37295157032e2584ca89f61b313d70f79d203eea66da3c2293d6dd9`.
**Resultado:** `TradingLab-Desktop-v1.9.1-DERIV-STABLE.exe`, `FileVersion 1.9.1.0`, tamanho
44.746.240 bytes e SHA-256
`2A22D1325CF2FB8BD8EA3A9CC32427D94E2FBF68C30576904AC77028F11DBC89`.
**Riscos/limitações:** conexão e latência de rede externas nunca são zero; uma estratégia pode
permanecer monitorando por longos períodos quando os critérios estatísticos não são satisfeitos.
Isso é comportamento de segurança, não travamento. Nenhuma compra externa Real foi feita e o EXE
ainda não possui assinatura Authenticode.
**Próximo passo:** manter o bot pausado até o usuário decidir iniciar uma sessão Demo supervisionada
e arquivar as versões 1.9.0 e anteriores para evitar abrir um binário antigo por engano.

### WL-2026-08-25-10 — Dashboard de liquidações em tempo real

**Objetivo:** corrigir resultados que só eram atualizados depois que o operador pausava o bot e
entregar uma nova versão portátil com atualização contínua recuperável.
**Requisitos relacionados:** AG-INV-001, AG-INV-009, R-ORD-001, R-TEST-001 e arquitetura de UI
descartável baseada exclusivamente na projeção autoritativa do Core.
**Arquivos alterados:** controller da UI, teste de regressão do polling, documentação da arquitetura
de informação, metadados/scripts de build e pacote da release v1.9.2.
**Causa raiz:** a thread `ui-projection-poll` terminava definitivamente após qualquer `UiIpcError`
transitório. A dashboard continuava mostrando seu último snapshot; o botão de pausa parecia
“corrigir” os números porque esse comando executava uma consulta manual logo após o Safe Stop.
**Implementação:** o polling bounded de 500 ms agora marca temporariamente a conexão como
indisponível, preserva o último snapshot e continua ativo. O cliente serializado tenta reconectar no
ciclo seguinte; quando a projeção volta, a UI retoma automaticamente P&L, ganhos, perdas, contagem e
tabela de liquidações, sem alterar estado financeiro nem depender de pausa.
**Decisões:** não foi adicionado acesso direto da UI ao SQLite, evento de corretora ou estado local
autoritativo. O Core continua sendo a única fonte dos resultados confirmados e uma indisponibilidade
real continua visível como desconexão.
**Validação executada:** novo teste injeta uma falha IPC depois do snapshot inicial e comprova que o
poll permanece vivo, recebe a projeção atualizada e restaura `connected=True` sem comando manual.
Suíte completa: **584 passed, 4 skipped, 0 failed**. Ruff check e format aprovados nos 331 arquivos
de produto/teste/build; mypy aprovado em 208 arquivos; compileall aprovado. O build PyInstaller
passou scanner de segredos com zero achados, verificou 296 arquivos e gerou manifesto SHA-256
`4ca450fd302c4b754240bd62404105b6b1dec3026a80f2a631473eb6233fe0ce`. O portátil passou smoke
isolado com código 0 e zero processos v1.9.2 remanescentes; depois abriu visivelmente, reutilizou o
perfil, autenticou a conta Demo salva e manteve a UI responsiva com o worker `live-demo` ativo.
**Resultado:** `TradingLab-Desktop-v1.9.2-LIVE-DASHBOARD.exe`, `FileVersion 1.9.2.0`, tamanho
44.747.264 bytes e SHA-256
`2FEB1EBCBFBD69524C8B69A49E2CB9670A863B9202946ADF029060BFBDC202EE`.
**Riscos/limitações:** a UI só publica liquidações confirmadas pelo Core; atrasos externos da Deriv
antes da confirmação continuam possíveis e não são inventados como resultado. Nenhuma ordem Real
ou Demo externa foi enviada nesta correção e o binário permanece sem assinatura Authenticode.
**Próximo passo:** observar uma sessão Demo com o bot ligado e confirmar visualmente que cada nova
liquidação aparece sem acionar pausa; manter o Safe Stop disponível para qualquer divergência.

### WL-2026-08-25-11 — Gestão de risco ampliada e integralmente visível

**Objetivo:** corrigir a gestão de risco comprimida/oculta na workspace Deriv e apresentar todos os
controles e indicadores em uma tela sem rolagem.
**Requisitos relacionados:** arquitetura de informação da UI, AG-INV-001, AG-INV-009,
R-RISK-001, R-RISK-005 e R-TEST-001.
**Arquivos alterados:** resumo de estratégia Deriv, painel de configuração Digit Edge, composição
da aba de parâmetros, tema visual, traduções, testes de UI, documentação, versões/scripts de build e
pacote v1.9.3.
**Implementação:** no Resumo, a área de gestão de risco passou de uma faixa única para seis cartões
maiores em grade 3×2, com valores em tipografia ampliada, progresso de exposição mais visível e uso
de todo o espaço vertical. Na aba `Parámetros y riesgo`, os blocos introdutórios que consumiam a
altura foram retirados da composição visível; os controles autoritativos agora ocupam a área útil
com largura flexível. Campos monetários, ativo, perdas máximas, cooldown, confiança, checkbox de
Martingale, multiplicador, etapas, teto absoluto, projeções, validação e botão Aplicar permanecem
simultaneamente visíveis.
**Decisões:** a mudança não cria estado financeiro na UI nem altera cálculos/limites; a configuração
continua validada e aplicada pelo Core. Informações técnicas da estratégia permanecem no hero,
biblioteca, mercado ao vivo e tooltip, sem competir com a gestão de risco.
**Validação executada:** inspeção visual antes/depois e renderização no tamanho real **1382×744**;
automação Windows confirmou todos os controles relevantes com `IsOffscreen=False`, inclusive
`Martingale` e `Aplicar Parámetros`. Suíte completa: **585 passed, 4 skipped, 0 failed**; testes
direcionados finais: **8 passed**. Ruff check/format aprovados em 331 arquivos, mypy aprovado em 208
arquivos e compileall aprovado. Build final passou scanner de segredos com zero achados, health
check e manifesto de 296 arquivos, SHA-256
`4e919cbfdf9e768720b9a6bad96ff1a0ca996d66d06f1f0ef1064de5f0958c6b`. O portátil final passou
smoke isolado com código 0 e abriu visivelmente com a conta Demo conectada e bot pausado.
**Resultado:** `TradingLab-Desktop-v1.9.3-RISK-MANAGEMENT-FINAL.exe`, `FileVersion 1.9.3.0`,
tamanho 44.749.312 bytes e SHA-256
`DFD750B116EED6E577BA9E7D2F2B64F57913C4B4CA0AA3A44B9C101582607AE1`.
**Riscos/limitações:** o layout foi validado na resolução observada e possui mínimos seguros, mas
escalas de acessibilidade extremas do Windows podem exigir adaptação futura. Nenhuma ordem externa
foi enviada nesta alteração e o EXE permanece sem assinatura Authenticode.
**Próximo passo:** o usuário pode revisar os valores na aba já aberta e aplicar somente depois de
confirmar Stop Loss, meta, stake e, se optar, os limites do Martingale delimitado.

### WL-2026-08-25-12 — Progressão Martingale efetiva e persistente

**Objetivo:** corrigir o relato de que as ordens Digit continuavam sempre na stake base mesmo com o
Martingale delimitado habilitado e entregar um novo portátil verificável.
**Causa raiz:** o histórico autoritativo confirmou que as liquidações e seus produtos chegavam
corretamente ao Core, porém a configuração de risco existia apenas em memória. Depois de reiniciar o
aplicativo, o Core voltava ao padrão com Martingale desligado. Além disso, reaplicar uma configuração
idêntica zerava desnecessariamente uma sequência já iniciada.
**Implementação:** a configuração de risco Digit agora é salva atomicamente no perfil local e
restaurada pelo Core na próxima inicialização. Aplicações idempotentes preservam o passo ativo. Foi
adicionado um teste de aplicação completo para cada uma das três estratégias, comprovando que uma
perda com stake USD 1.00 produz a próxima ordem com USD 2.00; a validação unitária continua cobrindo
a sequência delimitada USD 1.00 → USD 2.00 → USD 4.00 e o retorno à base.
**Validação executada:** testes direcionados **46 passed**; suíte completa **591 passed, 4 skipped,
0 failed**; Ruff e mypy aprovados; compileall aprovado. O build PyInstaller passou scanner de
segredos com zero achados, gerou manifesto de 297 arquivos com SHA-256
`56fb4fcd3fa05de4626107d578229bb4710ac941194cf25f7dd2b735a4fb1b2a` e passou o health check. O
portátil passou smoke isolado com código 0 e abriu visivelmente como v1.9.4 em modo prática. Nenhuma
ordem externa Demo ou Real foi enviada durante esta correção.
**Resultado:** `TradingLab-Desktop-v1.9.4-MARTINGALE-FIXED.exe`, `FileVersion 1.9.4.0`, tamanho
44.757.504 bytes e SHA-256
`C5751F0E411C36151DC7886A479967EA5146D1B64E97B66779AA21DA46265C94`.
**Estado entregue:** configuração ativa preservada em USD 1.00, multiplicador 2.00×, duas etapas,
teto USD 4.00 e máximo de três perdas consecutivas. O aplicativo inicia com o bot pausado; a conexão
Demo e o início das entradas continuam sendo ações explícitas do operador.

### WL-2026-08-25-13 — Radar multiativo Shadow com isolamento por símbolo

**Objetivo:** implementar a primeira fatia segura da seleção dinâmica de ativos: observar os cinco
índices R clássicos, comparar evidência estatística conservadora e mostrar um candidato ou
abstenção, sem permitir que o radar envie ordens ou altere automaticamente o ativo do executor.
**Implementação:** o Core descobre `R_10`, `R_25`, `R_50`, `R_75` e `R_100` por
`active_symbols`/`contracts_for`, mantém uma instância independente do motor de três estratégias e
uma janela paginada de 500 ticks para cada símbolo. O ranking usa apenas sinais Shadow atuais e
ordena pela margem entre estimativa e piso conservador, com um único candidato visual. A UI recebeu
uma tabela somente leitura com ativo, estado, hipótese, margem, aquecimento e aviso explícito de que
payout/EV ainda é requisito futuro. O executor continua consumindo exclusivamente as projeções do
ativo selecionado pelo operador. Falha de um stream secundário não fecha o Health Gate nem derruba
a conexão principal.
**Decisões:** esta versão não faz troca automática, não cria `TradeIntent`, não toca em stake,
Martingale, Risk Ledger ou roteamento. Sem payout válido/recente e EV líquido conservador, o ranking
é evidência de pesquisa e pode permanecer em abstenção. A expansão para índices 1HZ foi adiada para
evitar carga operacional antes da validação dos cinco ativos iniciais.
**Validação executada:** testes novos cobrem buffers independentes, preservação durante refresh,
candidato único, abstenção, isolamento de falha, protocolo IPC estrito e UI sem controles de
execução. Suíte completa: **599 passed, 4 skipped, 0 failed**; bateria final direcionada:
**37 passed**. Ruff format/check aprovados em 335 arquivos, mypy aprovado em 210 arquivos e
compileall aprovado. O build passou scanner com zero achados, manifesto de 298 arquivos com SHA-256
`db000057e0d80c566de6897e780bb84d7a3412b2a677769f83c7d3207482b14d` e health check com código 0.
**Resultado:** `TradingLab-Desktop-v1.9.5-MULTI-ASSET-SHADOW-RADAR.exe`, `FileVersion 1.9.5.0`,
tamanho 44.791.296 bytes e SHA-256
`7075FBDAA8D38C65ED69340759C328C41A0F06E1E10CADB8112A51846F8B78F6`.
**Segurança operacional:** nenhuma ordem externa Demo ou Real foi enviada. A instância v1.9.4 que
já estava aberta foi preservada; o novo executável não substitui nem encerra uma sessão ativa sem
ação explícita do operador.

### WL-2026-08-25-14 — Reconciliação Deriv sem passthrough e desbloqueio das entradas

**Objetivo:** diagnosticar por que o bot conectado não abria novas operações e corrigir o bloqueio
sem apagar a ordem ambígua nem liberar risco sem evidência da corretora.
**Causa raiz comprovada:** a última submissão ficou `UNKNOWN` após timeout de possível envio. A
Deriv havia executado e liquidado a compra no contrato `10526152179`, mas as respostas atuais de
`statement` e `profit_table` omitiram o `passthrough.order_id`. O reconciliador anterior pesquisava
exclusivamente esse campo e retornava `DERIV_CONTRACT_NOT_FOUND`; uma reserva de USD 1.00 permanecia
ativa e `HG_ORDER_UNKNOWN` bloqueava corretamente todas as entradas seguintes. Stop Loss e Take
Profit estavam dentro dos limites e não eram a causa.
**Implementação:** `OrderStatusQuery` agora transporta o timestamp UTC persistido da submissão. Se
o ID do contrato e o passthrough estiverem ausentes, o worker examina `profit_table` dentro de uma
janela pós-submissão bounded e exige correspondência única de horário, ativo, tipo de contrato e
stake Decimal exata. Zero ou mais de uma correspondência continuam fail-closed com razão explícita;
nenhuma heurística libera reserva. O contrato encontrado ainda é consultado por
`proposal_open_contract` e passa por todas as validações financeiras antes de produzir evidência.
**Validação:** consulta autenticada somente leitura confirmou `DIGITDIFF`, `R_100`, USD 1.00,
liquidação `won` e P&L USD +0.09. Testes direcionados: **57 passed**; suíte completa:
**601 passed, 4 skipped, 0 failed**; Ruff, mypy em 210 arquivos e compileall aprovados. Build com
scanner de segredos sem achados, 298 arquivos e manifesto SHA-256
`48b34dff8e80ff44659624f7337e80f6b8138c8e2a0d7029dae6ba90a209bef7`; health check código 0.
**Prova no perfil real de teste:** após iniciar a v1.9.6, a ordem mudou de `UNKNOWN` para `SETTLED`,
o broker ID foi persistido, P&L +9 minor units aplicado uma vez, reserva liberada uma vez e Outbox
reconciliado. Estado final: zero ordens não terminais e zero reservas ativas. Durante a observação
Demo o bot esteve ativo e houve novas liquidações confirmadas; ele foi colocado em Safe Stop e
permaneceu pausado ao final.
**Resultado:** `TradingLab-Desktop-v1.9.6-RECONCILIATION-FIXED.exe`, `FileVersion 1.9.6.0`, tamanho
44.795.392 bytes e SHA-256
`82F577322214C9673D71577FDCB89857584C5D82F7187B3CC0C3A6AE423CB83B`.

### WL-2026-08-25-15 — Seleção automática Demo e filtro por vantagem líquida

**Objetivo:** remover o fundo branco dos controles numéricos, habilitar variação automática entre
os índices R monitorados e impedir que uma taxa de acerto alta seja confundida com resultado
financeiro positivo.
**Diagnóstico:** 1.551 operações Deriv liquidadas no perfil de teste mostraram que
`deriv-digit-diff-frequency` acertou 90,05% em 221 operações, porém acumulou -409 minor units; a
`selective-differs-edge` acertou 88,87% em 1.330 operações, mas acumulou -4.612 minor units. O payout
assimétrico tornava a taxa de acerto isolada um critério incorreto.
**Implementação:** os `QSpinBox`, campos e listas receberam tema escuro explícito, inclusive foco,
seleção e estado desabilitado. A configuração persistida ganhou seleção automática de ativo, ligada
por padrão para perfis existentes e visível como opção Demo; o ativo manual permanece fallback. O
executor Demo agora consome o ranking multiativo, preserva isolamento dos buffers e fixa o ativo
durante uma sequência Martingale já iniciada. Antes de cada ordem, exige uma margem estatística
mínima derivada do filtro conservador e consulta as últimas 200 liquidações da estratégia. Com ao
menos 10 resultados, P&L recente não positivo bloqueia novas entradas; quando há payout histórico,
o break-even observado eleva o piso exigido. Sem vantagem líquida o comportamento correto é
abstenção. Conta Real continua fora da automação financeira.
**Segurança operacional:** além do `dispatcher_started`, o executor exige um segundo estado de
armamento explícito do operador, fornecido pelo ciclo de vida. Assim, reconexão, reconciliação ou
mudança de worker não podem armar o bot. O bot permanece pausado ao iniciar e a automação só pode
executar quando o operador liga explicitamente a sessão Demo.
**Validação Demo observada:** uma abertura intermediária da v1.9.7 executou 16 contratos Demo em
`R_10`, todos `tail-probability-edge`, e comprovou a troca automática. A sequência somou -339 minor
units. A evidência motivou reduzir o circuito financeiro de 30 para 10 liquidações e acrescentar o
armamento explícito independente do estado do dispatcher. A entrega final foi novamente iniciada
pausada, com zero ordem não terminal e zero reserva ativa.
**Validação final:** 605 testes aprovados e 4 externos/opcionais ignorados; verificações Ruff e
mypy aprovadas nos módulos alterados. O pacote foi escaneado sem segredos, passou a verificação do
manifesto e o health check. A UI v1.9.7 abriu responsiva após a inicialização dos serviços internos,
em modo prática e com automação desarmada.

### WL-2026-08-26-01 — Documentação consolidada do projeto v1.9.11

**Objetivo:** criar documentação completa e navegável do estado efetivamente implementado,
distinguindo recursos operacionais, simulados, somente leitura e planejados.
**Escopo auditado:** Launcher/árvore de processos, UI, Core, Auth Agent, Deriv Worker, IQ Option de
laboratório, Simulated Worker, protocolo IPC, persistência, estratégias de dígitos, radar
multiativo, gestão de risco, Bounded Martingale, diagnóstico, testes e pipelines de build/release.
**Arquivos criados:** `docs/README.md`, `docs/PROJECT_OVERVIEW.md`, `docs/USER_GUIDE.md`,
`docs/DERIV_STRATEGIES_AND_RISK.md`, `docs/CURRENT_ARCHITECTURE.md`,
`docs/COMPONENT_REFERENCE.md`, `docs/DEVELOPMENT_BUILD_AND_TEST.md` e
`docs/TROUBLESHOOTING.md`.
**Arquivos atualizados:** `README.md`, `docs/RELEASE_PROCESS.md`,
`docs/OPERATIONS_RUNBOOK.md` e este `WORKLOG.md`.
**Decisões:** a documentação consolidada declara explicitamente que a execução financeira externa
é habilitada somente em Deriv Demo; a conta Real permanece somente leitura; IQ Option possui
infraestrutura e testes, mas não sessão externa operacional. Documentos históricos foram
preservados e receberam ponte para a documentação atual quando necessário. Nenhuma alegação de
rentabilidade foi adicionada.
**Validação executada:** coleta de testes encontrou **613 testes**; verificador local analisou 75
links relativos em 22 documentos sem encontrar link quebrado; o `SecretScanner` analisou os 11
documentos criados/alterados sem encontrar material sensível. A documentação foi conferida contra
as constantes, fluxos e limites da implementação v1.9.11.
**Riscos/limitações:** PRD, arquitetura histórica e alguns documentos especializados continuam
registrando fases anteriores por valor de rastreabilidade. O índice `docs/README.md` identifica a
ordem de leitura e a fonte consolidada atual. O invólucro portátil ainda não possui um único script
canônico de montagem ponta a ponta.
**Próximo passo:** manter os documentos consolidados no mesmo diff de qualquer alteração futura de
produto, estratégia, risco, conexão, schema, UI ou release.

### WL-2026-08-26-02 — Pacote normativo alinhado à baseline v1.9.11

**Objetivo:** entregar arquitetura, PRD, briefing, regras e instruções de agentes sem contradições
com o produto executável atual.
**Arquivos atualizados:** `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`,
`PRD_Trading_Desktop_Deriv_IQOption.md`, `BRIEFING.md`, `RULES.md`, `AGENTS.md` e este
`WORKLOG.md`.
**Decisões:** Deriv Demo é o único caminho financeiro externo; Deriv Real conecta somente para
leitura; IQ Option externa permanece roadmap. As três estratégias atuais, radar de ativos,
armamento explícito, troca segura de estratégia, reconexão, ordem única em voo, Martingale limitado
e proteção DPAPI foram promovidos à documentação normativa. Requisitos futuros foram preservados,
mas demarcados como arquitetura/roadmap em vez de capacidade entregue.
**Validação:** revisão cruzada contra a documentação consolidada, verificação de links locais,
scanner de segredos e inspeção de diferenças documentais.
**Entrega:** `artifacts/TradingLab-v1.9.11-Documentacao-Atual.zip`, contendo exatamente os cinco
documentos solicitados e conferido pela listagem interna e hash SHA-256.
**Limitação:** os documentos extensos mantêm histórico e requisitos-alvo úteis; a seção de baseline
no início de cada documento prevalece sobre descrições de fases futuras.

### WL-2026-08-26-03 — Liveness, recovery e estado financeiro durável

**Objetivo:** eliminar estados em que o aplicativo permanecia aberto, mas deixava de executar após
queda, pausa, troca de estratégia ou substituição do worker, sem reduzir as garantias financeiras.
**Causas confirmadas:** Core mantinha referência ao cliente IPC aposentado depois do restart; circuit
breaker não fazia probe automático; recovery autenticado podia rearmar; market/clock blockers não
alcançavam a conta financeira; geração antiga de telemetria podia alterar o gate; e step, pin e
cooldown do Martingale existiam apenas em memória. Também foi reproduzida uma corrida de shutdown
com recovery que deixava o processo novo segurando o SQLite do simulador.
**Implementação:** supervisor financeiro estável com delegação ao cliente atual, recovery automático
`OPEN → HALF_OPEN → CLOSED`, restart/shutdown serializados, reconciliação antes da rota de submissão,
desarmamento obrigatório após queda, generation fencing de telemetria, escopo broker-wide de market
health, projeção única de readiness, journal JSONL persistente e migration
`0005_digit_risk_runtime`. Settlement e evidência reconciliada atualizam P&L, reserva e Martingale na
mesma transação; uma ordem já `SETTLED` não avança novamente com evidência tardia diferente.
**Invariantes preservadas:** persist-before-act, sem retry cego, `UNKNOWN` conserva exposição, evento
e P&L exactly-once, uma ordem Deriv em voo, asset pin durante recuperação, ARM explícito e Real
somente leitura.
**Validação:** suíte local completa com **615 passed, 4 skipped, 0 failed**; Ruff check/format, mypy em
211 arquivos-fonte, compileall e verificação de whitespace aprovados. Foram cobertos crash antes e
durante settlement, substituição de worker, circuito, reconciliação tardia, restart completo do Core,
cooldown, pin, isolamento de broker, geração antiga e ausência de auto-rearm.
**Limitação:** os smokes externos Deriv permanecem opt-in; esta execução não usou token, não acessou
conta externa e não enviou ordem Demo ou Real. O veredito detalhado está em
`docs/LIVENESS_RECOVERY_AUDIT_V1_9_11.md`.

### WL-2026-08-26-04 — Pós-validação compilada e portable v1.9.11

**Objetivo:** validar novamente a baseline pós-correção, produzir o build Windows atual e provar
liveness/recovery no executável compilado sem usar conta Real.
**Correções de build/runtime:** subprocessos congelados de um executável `windowed` agora restauram
os pipes standard herdados pelo handle Win32; o dispatch `-m` não importa a árvore completa do
Launcher antes da hora; o spec remove as DLLs ICU estrangeiras descobertas via Poppler; e a UI
headless não transforma uma oscilação transitória de polling em encerramento do processo.
**Validação final:** **616 passed, 4 skipped, 0 failed**; Ruff check/format, mypy em 211 arquivos,
compileall, scanner e `git diff --check` aprovados. O teste de Martingale foi ampliado para cobrir
duas perdas com dois restarts, restauração de step/pin/stake, vitória e reset, preservando
idempotência de settlement e reconciliação tardia.
**Build:** onedir final com 342 arquivos, scanner sem achados, manifesto lógico
`469ed7990cc93aefa99fa193338e3f28605cd8c2f11bd5725c6591972c414fb3` e health check aprovado.
`TradingLab.exe` tem SHA-256
`563e00f8e7a8394903b291441bb2129a3a9fbbe936a580ce3f925cdace0ef2ab`.
**Portable:** `TradingLab-Desktop-v1.9.11-PORTABLE.exe`, 46.260.736 bytes, SHA-256
`a39ef7ed72cb183dc5c5c66a9560cb6d31aa5a50946682a87c3bdd6552596863`; health check e smoke
completo retornaram 0, sem nova pasta temporária residual. Installer Inno não foi gerado porque
`ISCC.exe` não está instalado.
**Smokes compilados:** três startups/restarts (incluindo UI gráfica) retornaram 0; banco passou
`quick_check` e migrations 0001–0005; abertura duplicada foi recusada sem derrubar a instância
saudável; kill abrupto eliminou toda a árvore e o mesmo perfil reiniciou; o circuit breaker abriu
após três quedas, fez probe automático e voltou a worker saudável; todos os cenários terminaram
com zero processos órfãos e trading desarmado.
**Segurança:** pacote e journals tiveram zero achados de segredo; `allow_real_financial_submission`
permanece `False`; `live-real` não anexa auto trader. Nenhuma ordem Real ou Demo externa foi
enviada. A validação externa Demo foi marcada BLOCKED/NOT EXECUTED porque não havia credencial DEMO
atual comprovável no perfil isolado.
**Relatório:** `docs/POST_LIVENESS_EXTERNAL_VALIDATION_V1_9_11.md`. Veredito:
`LOCAL_FIX_VALIDATED`.

### WL-2026-08-26-05 — Hotfix de ARM após cooldown expirado

**Sintoma real:** após a última sequência de losses, `HG_COOLDOWN_ACTIVE` foi persistido no Health
Gate. Embora o cooldown de 30 segundos tivesse vencido e `digit_risk_runtime` já mostrasse step 0,
pin nulo e cooldown nulo, uma tentativa posterior de ligar o bot foi recusada com
`trading_arm_evaluated(armed=false, reason=HG_COOLDOWN_ACTIVE)`.
**Causa:** `resume_new_entries()` removia apenas `HG_SAFE_STOP` e consultava o estado global sem
antes atualizar os bloqueios de risco dependentes do tempo. A limpeza existia no caminho de
projeção/entrada, mas não era garantida no próprio comando ARM.
**Correção:** o Core agora chama `refresh_digit_health_gate()` antes de avaliar ARM. Cooldown vencido
é encerrado e limpo atomicamente; cooldown ainda vigente, Stop Loss, Take Profit, UNKNOWN e demais
proteções continuam fail-closed.
**Regressão:** novo teste reproduz `loss → cooldown → Safe Stop → expiração sem polling de UI → ARM`
e exige retorno ativo e remoção do blocker. Suíte completa: **617 passed, 4 skipped, 0 failed**;
Ruff check/format, mypy em 211 arquivos, compileall e `git diff --check` aprovados.
**Build:** onedir v1.9.11 com 342 arquivos, scanner limpo, manifesto
`10b49de6caa1242935a7592d8873ab04d516c00d9a570357eb06630d18a5d687` e health check aprovado.
O ensaio no Core compilado confirmou `health_gate_cleared(HG_COOLDOWN_ACTIVE)` no comando ARM; o
smoke completo Launcher/Core/UI retornou 0, registrou worker ready e shutdown completo, com zero
processos residuais.
**Entrega:** `dist_hf/TradingLab-Desktop-v1.9.11-COOLDOWN-HOTFIX.exe`, 46.260.736 bytes, SHA-256
`945322FBAC8EA4727B71664839B4B8AEEFAEF1161E9D4DFBEEF4F02846A26A70`.
**Estado do perfil observado:** zero ordens não terminais, zero reservas ativas e zero Outbox
ambígua. Nenhum dado do perfil foi alterado e nenhuma ordem externa foi enviada.

### WL-2026-08-26-06 — Simplificação visual dos limites internos de Martingale

**Objetivo:** remover da tela de gestão de risco os controles `Passos de recuperação` e `Teto
absoluto de stake`, reorganizando o bloco sem retirar os limites obrigatórios do motor.
**Implementação:** a UI agora apresenta somente a habilitação do Martingale e o multiplicador. O
número máximo de passos e o teto absoluto continuam sendo carregados da configuração persistida,
enviados ao Core e validados pelo Bounded Martingale, mas não podem mais ser alterados diretamente
na tela. Foi acrescentada regressão headless que confirma a ausência dos dois rótulos/controles e
a preservação integral dos valores internos.
**Segurança:** `max_steps`, `max_stake`, Stop Loss e perda máxima projetada continuam obrigatórios;
nenhum guard financeiro, regra de conta Real ou comportamento de execução foi alterado.
**Validação:** **618 passed, 4 skipped, 0 failed**; Ruff check/format, mypy em 211 arquivos,
compileall e `git diff --check` aprovados. A prévia headless confirmou o novo layout. O onedir
compilado passou scanner de segredos, manifesto e health check; manifesto lógico
`e85c4e63914f15ab4599e328dae11228e181a6a184ebac4cb101c620b787d1fe` e SHA-256 do
`TradingLab.exe` `6C9DD236D76A3D05C14B2AA91CC4D0069B60D4493B1BB6181362FF97D064B9E9`.
O smoke do Launcher/Core/UI em perfil isolado registrou worker pronto, shutdown completo e zero
processos residuais.
**Entrega:** `dist_ui/TradingLab-Desktop-v1.9.11-RISK-UI-HOTFIX.exe`, 56.753.152 bytes, SHA-256
`2B23642D80EC7C2617762FCFB84382D6113C044E93BB232B6E1214B2B006AC26`. O recurso ZIP incorporado
foi comparado byte a byte por SHA-256 com o payload externo. A instância anterior em uso pelo
operador não foi interrompida; nenhum teste externo nem ordem Demo/Real foi executado.

### WL-2026-08-27-01 — Transporte Deriv tolerante a backpressure e mensagens futuras

**Defeito:** o reader WebSocket encerrava toda a conexão quando as filas de tick, saldo ou contrato
enchiam, quando recebia um `msg_type` ainda não conhecido, uma resposta duplicada, um frame binário
isolado ou um único JSON inválido. A fila de contratos também não recebia a notificação fatal do
reader, deixando seu consumidor depender apenas de timeout.
**Correção:** ticks e saldos agora usam drop-oldest com contadores; mensagens não assinadas,
`ping`/`pong`, erros não pareados e tipos futuros são tratados sem derrubar o socket. A fila de
contratos foi ampliada para 256, aguarda por até um segundo quando cheia e, persistindo o overflow,
preserva a conexão e publica evidência para o Core solicitar reconciliação financeira. Somente erro
real de `recv` ou cinco frames inválidos/binários consecutivos falham o reader. O snapshot imutável
de saúde é propagado por IPC, com generation fencing e sem criar
`HG_BROKER_EVENT_BACKPRESSURE`. A notificação fatal agora substitui o backlog nas três filas,
incluindo contratos.
**Segurança:** allowlist read-only, encoder sem `float`, envio de ordens, risco, ledger, Martingale e
máquina de estados permaneceram inalterados. Overflow financeiro não gera retry nem descarte
silencioso: ele aciona reconciliação pela verdade persistida do broker. Logs contêm apenas
`msg_type`/`error.code` sanitizados. Nenhuma credencial foi lida e nenhuma ordem externa foi enviada.
**Validação:** 11 regressões novas, incluindo 10.000 ticks com consumidor lento; suíte completa
**630 passed, 4 skipped, 0 failed**. Ruff check/format, mypy, compileall e `git diff --check`
aprovados.

### WL-2026-08-27-02 — Watchdog WebSocket contra conexão half-open

**Defeito:** ausência indefinida de frames era tratada como conexão válida, pois `recv(timeout)`
apenas repetia o polling. Wi-Fi interrompido, NAT expirado ou suspensão do Windows podiam deixar o
socket half-open, sem ticks e sem erro capaz de iniciar a recuperação supervisionada.
**Correção:** cada frame agora renova a prova monotônica de vida. Uma thread daemon dedicada avalia
a conexão a cada segundo, envia ping read-only após 15 segundos ociosos, exige nova prova de vida
em 10 segundos, encerra stall de RX após 30 segundos e invalida imediatamente o socket quando o
utilitário compartilhado detecta gap de suspensão superior a 10 segundos. Kills são idempotentes,
abortam o socket sem handshake bloqueante, propagam `DERIV_HEARTBEAT_TIMEOUT` às três filas e
publicam motivo/contadores no snapshot Worker/Core. `close()` continua silencioso e encerra reader
e watchdog.
**Segurança:** o ping cru `{\"ping\": 1}` passa pela allowlist read-only já existente; nenhuma
permissão foi ampliada. O watchdog nunca chama `request()`, nunca reenvia operação, não toca ordem,
risco, ledger, Martingale, reconciliação ou estado financeiro e não cria blocker por contagem de
reconexões. Logs contêm apenas motivo sanitizado e idade de RX. Todos os prazos usam monotonic.
**Validação:** 12 regressões de heartbeat com relógio controlado, incluindo half-open, pong,
suspensão, send failure, concorrência, filas e término da thread. Suíte completa: **642 passed, 4
skipped, 0 failed**; Ruff, mypy, compileall, scanner de segredos e `git diff --check` aprovados.
Nenhum teste externo ou ordem Demo/Real foi executado.

### WL-2026-08-27-03 — Reconciliação auto-recuperável e NOT_FOUND comprovado

**Defeitos:** o coordenador usava timeout de 0,5 segundo, uma única repetição após 0,05 segundo e
nenhum ciclo periódico; uma indisponibilidade transitória deixava
`HG_RECONCILIATION_UNAVAILABLE` bloqueado para sempre. `NOT_FOUND` permanecia inconclusivo
indefinidamente, `HG_SETTLEMENT_UNKNOWN` não possuía limpeza positiva e um ciclo vazio podia limpar
gates sem ter realizado prova. O timeout escalado do Core também não chegava ao transporte Deriv,
que continuava limitado internamente a três segundos.
**Correção:** consultas usam quatro tentativas com timeouts 8/12/16/20 segundos e backoff
exponencial 1/2/4 segundos, teto de 15 segundos e jitter de 25%, com prazos monotônicos. O novo
`ReconciliationScheduler` executa em thread daemon serializada, reage a startup/reconexão e tenta
periodicamente enquanto houver candidato ou gate transitório, com ciclos 5/10/20/30 segundos e
shutdown limpo. Falhas de transporte, IPC e `WORKER_NOT_READY` são transitórias; inconsistências de
protocolo/evidência vão para revisão humana e saem do ciclo automático.
**NOT_FOUND:** o Worker consulta somente fontes read-only e só produz prova negativa quando
`portfolio` e `statement` foram ambos verificados. Depois de 90 segundos de carência e duas provas
distintas separadas por pelo menos 10 segundos, o `SingleDatabaseWriter` faz, numa única transação,
`UNKNOWN → RECONCILING → REJECTED`, reconcilia a outbox e libera a reserva exatamente uma vez. Uma
fonte, carência incompleta ou falha de consulta preserva `UNKNOWN` e a exposição. A progressão
comprovada `SETTLEMENT_UNKNOWN → SETTLED` foi permitida sem aceitar regressões ou contradições.
**Invariantes:** nenhuma rota de submissão foi acrescentada; o scheduler conhece apenas
`OrderStatusPort`. Não existe retry de `ORDER_SUBMIT`, buy, proposal ou operação financeira. Core
continua único escritor; gates só são limpos após ciclo positivo e releitura persistente; conflito
continua exclusivamente humano. Nenhum token ou credencial foi usado.
**Validação:** **658 passed, 4 skipped, 0 failed**. Ruff check/format, mypy em 213 arquivos-fonte,
compileall e `git diff --check` aprovados. Regressões cobrem timeout de 3 segundos, três falhas e
sucesso, worker ainda não pronto, seis ciclos do scheduler, idle sem polling de reconciliação,
reentrância, daemon/shutdown, dupla prova NOT_FOUND, carência, fonte única, idempotência, limpeza de
settlement, ciclo vazio, conflito fora do auto-loop, jitter/teto e execução fora do caminho quente.
**Limitação:** testes externos Deriv permaneceram opt-in e não foram executados; a validação usou
somente SQLite temporário, workers simulados e transporte fake. Nenhuma ordem Demo ou Real foi
enviada.

### WL-2026-08-27-04 — Exposição de risco com fonte única persistente

**Defeito:** o `RiskLedger` mantinha reservas ativas em memória ao mesmo tempo em que o SQLite
mantinha `risk_reservations.state = 'ACTIVE'`. Release, restart, restore parcial ou falha entre a
transação e a atualização do dicionário podiam produzir exposição fantasma ou subcontagem.
**Correção:** foi introduzido `ActiveExposurePort`; no runtime financeiro ele lê, a cada decisão,
somente as reservas `ACTIVE` persistidas e vinculadas ao símbolo do `trade_intent`. Ausência ou
falha dessa leitura bloqueia com `HG_EXPOSURE_UNKNOWN`. `restore`, `register_active_reservation` e
`release_reservation` permanecem apenas como compatibilidade validada e não participam mais dos
cálculos ou limites. O coordenador deixou de registrar uma segunda cópia após o commit.
**Atomicidade e símbolos:** o writer preserva `BEGIN IMMEDIATE` e repete o check-and-insert na mesma
transação que cria intent, reserva e outbox. A exposição global é agregada no SQLite; a exposição
por símbolo é agregada em Python com a mesma canonicalização compartilhada para `frx`, `OTC_` e
símbolos Deriv. A unicidade parcial por broker/conta continua garantida pelo banco.
**Moeda:** foi adotada uma única moeda de referência configurada, em minor units inteiros. Pedido
ou reserva ativa em outra moeda falha com `HG_EXPOSURE_CURRENCY_MISMATCH`; não existe soma entre
moedas nem conversão implícita. Nenhum cache de exposição foi adicionado.
**Regressões:** 14 testes novos cobrem leitura direta do banco, release sem atualização de memória,
20 ciclos sem exposição fantasma, canonicalização, fail-closed e recuperação do gate, port ausente,
restore validate-before-swap, registro idempotente/divergente, unicidade persistida, moeda mista,
minor units, limites global/por símbolo e ausência de envio em falha de risco. O conjunto focal
terminou com **36 passed, 0 failed**; o replay por queda passou **2/2** após receber uma fonte vazia
explícita de simulação. Ruff check/format, mypy em 214 arquivos, compileall e `git diff --check`
foram aprovados; 382 arquivos versionados/novos foram examinados pelo scanner, com zero achados.
**Limitação do host:** a execução ampla independente do cofre terminou com **647 passed, 3 skipped,
1 deselected** e uma falha do scanner causada por fixtures secretas intencionais deixadas em
diretórios `work/test-tmp-risk-*` ignorados pelo Git e protegidos por ACL de execução anterior. Os
testes DPAPI/Auth/Launcher dependentes do contexto de usuário também não puderam rodar neste sandbox
(`VAULT_ENCRYPTION_FAILED`). As proteções não foram relaxadas nem ocultadas para obter resultado
verde. Nenhuma credencial foi usada e nenhuma ordem Demo ou Real foi enviada.

### WL-2026-08-27-05 — Auto trader sem leitura de banco no caminho quente e espera visível

**Defeitos:** o loop Deriv fazia leitura SQLite a cada avaliação para descobrir se havia ordem em
voo e também consultava desempenho recente durante a decisão. Em rajadas de ticks, isso disputava o
writer pelo lock do banco justamente no fluxo de intenção/liquidação. A espera correta após
`begin_new_run()` também permanecia invisível: o operador via o bot ligado e parado, religava ou
trocava estratégia, e reiniciava a janela de sinal novo.
**Correção:** `DerivDigitAutoTrader` agora mantém cache em memória de ordens Deriv não terminais e
amostras recentes de desempenho, sem chamar `reader` em `notify_tick()`, `evaluate_once()` ou
`_execution_candidates()`. O cache é semeado na inicialização, atualizado por eventos de ordem,
recarregado depois de reconciliação/reconexão e falha fechado: cache ausente, erro de leitura ou
conflito de evento bloqueia nova entrada com motivo explícito. Divergências entre cache e banco em
reload emitem `autotrader_inflight_cache_divergence`.
**Loop e UI:** os checks baratos agora ocorrem antes da telemetria completa. Notificações de tick
são coalescidas por geração, com piso monotônico de 0,25 segundo e sem atraso quando os ticks chegam
em cadence normal de aproximadamente 2 segundos. O estado exposto para a UI recebeu
`UiBotWaitingStatus` com `reason_code`, `description`, `waiting_since_seconds`, `symbol`,
`armed_epoch` e `rearm_notice`; a aba Deriv mostra o motivo legível e há quanto tempo aguarda.
`begin_new_run()` continua descartando sinais antigos e apenas passou a reportar que o rearme
reiniciou a espera.
**Regressões:** foram adicionados testes unitários explícitos para zero leitura de banco em 1.000
avaliações, cache semeado no startup, atualização por eventos, reload pós-reconciliação, bloqueio
fail-closed, divergência observável, curto-circuito antes da telemetria, coalescing de rajada,
cadência de 2 segundos sem atraso, exposição do motivo com duração, rearme reportado, semântica de
`begin_new_run()` preservada, ausência de envio nos caminhos de falha e carga de 10.000 ticks sem
submissão.
**Validação:** antes do ajuste final de nomenclatura, a suíte completa desta etapa havia terminado
com **679 passed, 4 skipped, 0 failed**. Após padronizar o campo como `waiting_since_seconds`, este
host não expôs um Python de desenvolvimento com `pytest`, `ruff` ou `mypy`; o único Python
disponível era um runtime embutido sem dependências. Foram executados `py_compile` nos arquivos
afetados e `git diff --check`, ambos aprovados. Nenhuma credencial foi usada e nenhuma ordem Demo ou
Real foi enviada.

### WL-2026-08-27-06 — Martingale por payout real, recuperação dividida e stake de UI

**Defeitos:** a progressão 2× ignorava o retorno líquido do contrato. Em `DIGITDIFF`, uma perda de
USD 1 podia gerar stake USD 2 mesmo quando o lucro líquido era aproximadamente 9%–10%. A UI também
mantinha um teto oculto de USD 4, fazendo uma entrada válida de USD 10 aparecer como se estivesse
abaixo do mínimo.
**Correção:** o worker Deriv ganhou uma rota de cotação somente de leitura. O Core usa
`(payout - ask_price) / ask_price` e calcula `ceil(prejuízo_pendente / retorno_líquido)`, preferindo
recuperação integral e dividindo o alvo entre as tentativas restantes quando necessário. Restart
exige cotação nova. Recuperação sem orçamento falha fechado com
`DIGIT_MARTINGALE_RECOVERY_UNAFFORDABLE`; não há clamp, retry financeiro nem alteração das regras
de conta Real. Ganhos parciais reduzem o prejuízo pendente e a sequência só reseta quando ele é
coberto ou quando termina o limite de passos.
**UI:** o teto interno acompanha o Stop Loss informado, eliminando o bloqueio fixo de USD 4. O
campo USD 10 passa a ser aceito quando é válido para o broker e para os limites gerais. A mensagem
de validação agora distingue stake, Stop Loss e Take Profit. O multiplicador visual foi substituído
por cálculo automático pela cotação.
**Validação:** 89 testes matemáticos passaram, incluindo 80 combinações de estresse com retornos de
5% a 95%, quatro níveis de prejuízo e quatro passos. O caminho IPC de proposta sem compra passou.
A suíte ampla terminou com **766 passed, 4 skipped** e duas falhas de limpeza de arquivo SQLite
temporário no Windows; após retry delimitado da limpeza, os dois testes foram repetidos e passaram.
Ruff check/format e mypy em 214 arquivos passaram. Nenhuma credencial foi usada e nenhuma ordem
Demo ou Real foi enviada.
**Build:** o pipeline canônico v1.9.11 gerou onedir com 450 arquivos, scanner com zero achados,
manifesto lógico `e614d1cb60a4fc4fb6502513f42a81448cbc5be15f86af77eb20f7b11feadf53` e health
check aprovado. O portátil de arquivo único possui 57.069.568 bytes, versão `1.9.11.0`, SHA-256
`321CC75C271FE65A92D15D96086B296EFD8B33D77E4D5C558F2F8916E85CA617` e também terminou seu
health check com código 0. Inno Setup não estava disponível e nenhum installer foi declarado.

### WL-2026-08-27-07 — Reset controlado da gestão de risco para testes Demo

**Defeito:** depois de uma perda, a sequência persistida impedia qualquer alteração nos parâmetros
com `DIGIT_MARTINGALE_SEQUENCE_ACTIVE`, mesmo sem ordem aberta. Isso deixava o operador sem como
iniciar uma nova rodada de teste com outra stake.
**Correção:** o comando existente `Aplicar Parâmetros` agora funciona como fronteira explícita de
uma nova rodada. Quando não existe ordem Deriv de dígitos não terminal, o Core limpa passo do Gale,
ativo fixado, prejuízo de recuperação, perdas consecutivas e cooldown, tanto em memória quanto no
`state.db`, e então salva a configuração. O P&L diário não é apagado. Ordem aberta continua
bloqueando a alteração, assim como Stop Loss, Take Profit e limites global/por símbolo.
**Validação:** 13 testes focais e 53 testes de risco/UI/persistência/fluxo Deriv passaram. A
regressão inclui sequência ativa persistida no SQLite e confirma reset exatamente no botão Aplicar,
preservando o P&L diário. Ruff e mypy passaram. O onedir v1.9.11 passou scanner de segredos,
manifesto de 450 arquivos e health check; o portátil atualizado também retornou código 0 no health
check executado diretamente. Artefato final: 57.070.080 bytes, versão `1.9.11.0`, SHA-256
`0C18003FED43DCBC4B4457F25999311AC41875D5BDC09CE147F5AC7C7FE7C90A`. Nenhuma ordem Demo ou
Real foi enviada.

### WL-2026-08-27-08 — Nova sessão Demo e ARM fail-closed visível

**Defeito reproduzido:** o perfil do operador acumulou P&L da sessão de dígitos em USD -51,57 com
Stop Loss de USD 50,00. O Core recusava corretamente o ARM com `HG_DAILY_STOP_REACHED`, porém não
havia uma ação explícita na UI para começar outra rodada Demo. Além disso, uma tentativa de ARM
recusada removia `HG_SAFE_STOP`; a UI podia então projetar o bot como ligado apesar de todas as
ordens continuarem bloqueadas.
**Correção:** a aba de parâmetros ganhou `Nueva Sesión Demo`. Com confirmação humana, Safe Stop,
transporte `live-demo`, zero ordem Deriv de dígitos não terminal e zero reserva ativa, o Core zera
atomicamente no `state.db` apenas o baseline de P&L e a progressão da sessão de teste. Ordens e
resultados históricos permanecem. Conta Real, bot armado ou exposição pendente falham fechado. Uma
tentativa de ARM recusada agora restaura `HG_SAFE_STOP`, mantém o botão desligado e devolve à UI o
motivo específico, inclusive `HG_DAILY_STOP_REACHED`.
O startup também passou a reconectar automaticamente a credencial Demo já salva, sempre mantendo
Safe Stop; conta Real não é auto-selecionada sem necessidade de reconciliação.
**Validação:** suíte completa final com **774 passed, 4 skipped, 0 failed**; Ruff check/format, mypy em
214 arquivos, compileall e `git diff --check` aprovados. O onedir v1.9.11 passou scanner com zero
achados, manifesto de 450 arquivos (`4e4479929ba00d3a109447a0ecbbe8b8bf9150d08c56b04a491d44ccef32a10c`)
e health check. O portátil retornou código 0 e deixou zero processos residuais. Artefato:
`TradingLab-Desktop-v1.9.11-BOT-START-FIXED.exe`, 57.077.760 bytes, versão 1.9.11.0,
SHA-256 `703E7F60155804EA9AD6E586980168C00B696C89C55FC2D7665A9457B5326D31`. Nenhuma ordem Demo ou
Real foi enviada.

### WL-2026-08-27-09 — Reset persistente dos resultados da rodada Demo

**Pedido:** permitir reiniciar os testes depois da trava por três losses sem apagar o histórico
financeiro auditável.
**Implementação:** a ação foi renomeada para `Reiniciar Resultados del Bot`. A migration append-only
`0006_digit_test_session` adiciona um marco UTC persistente à rodada. O reset continua exigindo
Safe Stop, conta Demo, zero ordem não terminal e zero reserva ativa; ele zera P&L de risco, losses,
Stop/Take da rodada, cooldown, Gale, pin e prejuízo de recuperação. Dashboard, operações visíveis e
cache de desempenho do auto trader passam a ler apenas settlements posteriores ao marco. As linhas
históricas continuam intactas no SQLite e podem ser auditadas; conta Real permanece sem essa rota.
**Validação:** regressões provam persistência após restart, exclusão visual dos resultados antigos,
P&L corrente zerado, cache estatístico recarregado e histórico bruto preservado. Conjunto focado:
67 testes aprovados. Suíte completa: **776 passed, 4 skipped, 0 failed**. Ruff check/format, mypy,
compileall e `git diff --check` aprovados. O onedir passou scanner com zero achados, manifesto de
450 arquivos (`311a4022346eac50195d76dcc04397b80a8cedcd332f2c69ab980fda11dbfa54`) e health
check. O portátil `TradingLab-Desktop-v1.9.11-RESET-RESULTADOS.exe` tem 57.084.416 bytes, versão
1.9.11.0 e SHA-256 `CC38167C899F85B6941E91DE28EFAE41B71B5E2094126D2AA141FECF395B0F9C`; health
check terminou em código 0 e deixou zero processos. Nenhuma ordem externa foi enviada.

### WL-2026-08-27-10 — Fase 1: catálogo Digit, EnginePool e seleção multi-estratégia

**Defeitos confirmados:** o motor Digit instanciava as três estratégias em uma tupla literal; uma
engine aceitava símbolo estrangeiro apagando silenciosamente seu deque e reiniciando o warm-up; e
o executor tratava `SHADOW_SIGNAL` como filtro de execução, embora a evidência declarasse
`entry_mode=SHADOW_ONLY`.

**Implementação:** foi criado um registry local tipado sobre `strategy_catalog`, com manifest,
factory, IDs estáveis, nome pt-BR, contratos, parâmetros, risco, lifecycle e warm-up. As três
classes matemáticas existentes são injetadas pelo registry; uma quarta estratégia empacotada de
teste é descoberta sem editar o engine. `DerivDigitEnginePool` mantém uma engine por símbolo,
criada sob demanda, descartada no unsubscribe, limitada a 12 e com erro explícito para roteamento
estrangeiro. O conjunto persistido `enabled_strategy_ids` controla elegibilidade, enquanto todas
continuam em shadow. A UI ganhou seleção compacta das três e modo estresse Demo, habilitado por
padrão; seleção vazia bloqueia com `BOT_NO_STRATEGY_SELECTED` e Real recusa o estresse com
`BOT_STRESS_MODE_REQUIRES_DEMO`.

**Arbitragem e auditoria:** o `SignalArbiter` existente recebeu arbitragem ranqueada de N candidatos
por maior margem conservadora, maior amostra condicional, ID de estratégia e símbolo, sem
aleatoriedade. Um ciclo consome vencedor e perdedores, grava motivos individuais e mantém o slot
único de ordem do Risk Ledger. Mudança de seleção não altera ordem em voo. O journal registra
`EXECUTABLE_DEMO` apenas para a vencedora e `SHADOW_ONLY` para as descartadas. Evidência histórica
com `SHADOW_ONLY` não foi reescrita; esta semântica vale a partir da v1.9.11 em 27/08/2026.

**Telemetria e saturação:** projeções por estratégia e símbolo incluem sinais emitidos, executados,
perdidos na arbitragem, amostra, warm-up e p95 de latência. O canal da UI recebeu métricas agregadas
de taxa, engines ativas, estratégias habilitadas, candidatos e p95 do ciclo. Ciclo acima do budget
de 20.000 µs emite evidência de saturação e reduz gradualmente a cadência de cálculo, mantendo
todos os ticks no buffer e sem descartar decisão persistida. O hot path não acessa SQLite.

**Validação:** comportamento das três estratégias foi comparado contra as próprias implementações
originais sem alteração das fórmulas, janelas, Wilson, limiares ou `_conditional_outcomes`. A carga
local de 10 símbolos × 3 estratégias × 10.000 ticks terminou com `p95=206 µs`, pico de memória de
`2.172.862 bytes`, 10 engines e zero leituras de banco. Suíte completa: **793 passed, 4 skipped, 0
failed**. Ruff check/format, mypy em 215 arquivos, compileall e `git diff --check` aprovados. Testes
externos não foram executados e nenhuma ordem Demo ou Real foi enviada.

### WL-2026-08-27-11 — Correção pós-Fase 1: estado executável independente do ambiente

**Versão:** v1.9.11, preservada conforme a política vigente do projeto.

**Correção de evidência:** novos eventos de arbitragem deixam de combinar elegibilidade e ambiente
no valor `entry_mode=EXECUTABLE_DEMO`. A vencedora passa a registrar
`entry_mode=EXECUTABLE_SIGNAL`, enquanto o ambiente ocupa o campo próprio
`execution_environment`. O auto trader financeiro continua restrito à conta Demo e, por isso,
registra `execution_environment=DEMO`; as descartadas continuam `entry_mode=SHADOW_ONLY` e também
recebem o campo explícito de ambiente. Nenhum registro histórico foi migrado ou reescrito:
ocorrências anteriores de `EXECUTABLE_DEMO` e `SHADOW_ONLY` permanecem auditáveis como foram
gravadas.

**Estrutura de pacotes:** a verificação física e dos imports confirmou que existe somente
`packages/strategy_catalog/`. O caminho `packages/strategies/strategy_catalog/` não existe e nenhum
consumidor o importa, portanto não havia dois pacotes para consolidar nem API pública a mover.

**Equivalência matemática:** o teste de regressão compara, byte a byte em serialização canônica e
também por igualdade integral do dataclass, as decisões das três classes instanciadas diretamente
com as mesmas estratégias descobertas pelo registry. Foram cobertas quatro séries determinísticas
(499 ticks de warm-up, 500 alternando 9/0, 500 alternando 1/2 e 500 em ciclos uniformes 0–9), num
total de 12 comparações. Estado, motivo, contrato, direção, barreira, probabilidades e toda a tupla
de evidência são comparados sem modificar fórmula, limiar, janela, Wilson ou amostra mínima.

**Validação:** 12/12 comparações de equivalência foram idênticas. Suíte completa com **793 passed,
4 skipped, 0 failed**; Ruff check/format e mypy em 215 arquivos aprovados. Os testes externos
permaneceram opt-in/ignorados, nenhuma credencial foi usada e nenhuma ordem Demo ou Real foi
enviada.

### WL-2026-08-27-12 — Fronteiras Wilson no teste de equivalência das 3 estratégias

**Versão:** v1.9.11, preservada.

**Escopo:** foram acrescentadas séries determinísticas de fronteira ao teste de regressão
`test_digit_strategy_phase1_regressions.py`, sem alterar fórmula, limiar, janela, Wilson,
amostra mínima ou código de execução financeira. A cobertura agora prova os pontos imediatamente
acima e abaixo dos limiares para DIGITOVER, DIGITUNDER, DIGITDIFF, DIGITEVEN e DIGITODD; amostra
condicional exatamente 70 aceita e 69 retorna `CONTEXT_INSUFFICIENT`; divergência de janelas no
Digit Differs retorna `DIFFERS_EDGE_WINDOWS_DISAGREE`; e há séries com `p_hat` realista
aproximado de 0,70, 0,90 e 0,50.

**Evidência numérica:** acima do limiar: Over/Under `Wilson=52.000832`, margem `+0.000832pp`;
Differs `Wilson=92.286830`, margem `+0.036830pp`; Even/Odd `Wilson=52.003746`, margem
`+0.003746pp`. Abaixo do limiar: Over/Under `Wilson=51.999930`, margem `-0.000070pp`; Differs
`Wilson=92.248471`, margem `-0.001529pp`; Even/Odd `Wilson=51.999440`, margem `-0.000560pp`.
Séries realistas: Tail `p_hat=0.699399`, Differs `p_hat=0.900000`, Parity `p_hat=0.500000`.

**Validação:** regressão focada: **21 passed**. Suíte completa: **808 passed, 4 skipped, 0
failed**. Ruff check aprovado, Ruff format check aprovado e mypy em 215 arquivos aprovado.
Nenhuma credencial foi usada e nenhuma ordem Demo ou Real foi enviada.

### WL-2026-08-28-13 — Fase 2 revisada: proposal subscription, payout probe e sessão Differs dimensionada

**Versão:** v1.9.11, preservada.

**Etapa A — proposal subscription:** a documentação oficial da Deriv confirma que `proposal`
aceita `subscribe: 1` e que a linha de limite compartilhada por `proposal`,
`proposal_open_contract`, `buy` e `sell` é de 360 requests/minuto e 14.400 requests/hora. A sonda
externa pública, sem token e sem `buy`, abriu 5 subscriptions simultâneas de `proposal` para
R_10/R_25/R_50/R_75/R_100 com barreira 0; todas foram aceitas e removidas com `forget_all:
proposal`. Como a página oficial contabiliza requests, não updates empurrados pelo WebSocket, o
escopo aprovado usa 5 subscriptions iniciais e reserva margem explícita de 60 requests/minuto e
2.400 requests/hora para `buy`, `proposal_open_contract` e reconciliação.

**Etapa B — payout por barreira:** a sonda pública de 50 proposals pontuais
(5 símbolos × 10 barreiras), executada sequencialmente e sem autenticação, retornou
`payout_return_ratio=0.090000` para todas as barreiras em todos os símbolos. A divergência por
barreira foi `0.000000pp`; portanto, a rotação de barreira foi removida do escopo operacional e a
estratégia usa barreira fixa 0, configurável por parâmetro. A escolha permanece invariante ao
histórico de dígitos.

**Implementação:** foi adicionado o modelo imutável `BrokerProposalQuote` com `Money` e `Decimal`,
sem payload bruto, login, token ou conta. O transporte Deriv agora possui fila própria para updates
assinados de `proposal`, impedindo que subscriptions de proposal caiam como `msg_type`
desconhecido. O worker continua sem caminho novo de `buy`; `quote_digit_contract` só normaliza
evidence de cotação e preserva o método antigo de razão para Martingale. O Core recebeu
`PayoutRoutedDiffersProposalCache`, `SlidingWindowBrokerMessageBudget`, cálculo exato
`EV = 0.9 * W - 0.1`, TTL fail-closed, seleção determinística por maior payout entre símbolos e
barreira fixa. O orçamento nomeado é 300 requests/minuto e 12.000 requests/hora para proposal,
com evento `broker_message_budget_pressure` ao aproximar-se do teto. Como o IPC ainda não expõe
um comando de subscription de proposal até o Core, o feeder operacional inicial usa polling bounded
fora do caminho quente: 5 cotações por rodada, somente quando a estratégia está habilitada,
equivalente a 150 requests/minuto com TTL de 2s, dentro do orçamento reservado.

**Catálogo e execução:** a nova estratégia empacotada `payout-routed-differs-session` foi
registrada com nome pt-BR “Sessão Differs por Melhor Payout”, contrato `DIGITDIFF`, warmup zero e
status `PRACTICE_VALIDATED`. Ela não usa previsão de dígito e não emite sinal por histórico; o
auto trader só cria candidato executável quando recebe proposal fresca do cache, em Demo, e passa
pelo mesmo Signal Arbiter/Risk Ledger/slot único das estratégias existentes. A telemetria de
histórico filtra essa estratégia para não contaminar o radar estatístico das três estratégias
sniper. Martingale permanece não suportado nessa sessão.

**Validação:** sonda externa pública: 5/5 subscriptions aceitas; 50/50 proposals pontuais
coletadas; nenhuma credencial usada; nenhuma ordem Demo ou Real enviada. Testes focados:
**79 passed**. Suíte completa: **820 passed, 4 skipped, 0 failed**. Ruff check global aprovado,
Ruff format global aprovado, mypy em 216 arquivos aprovado, compileall em `apps` e `packages`
aprovado e `git diff --check` aprovado.

**Limitação residual:** esta etapa implementa o escopo dimensionado e os guardrails centrais da
sessão, mas não promove conta Real, não cria retry financeiro, não promete expectativa positiva e
não transforma subscription de proposal em requisito de teste unitário externo. A troca futura do
feeder bounded por subscription streaming no IPC deve preservar a mesma fila de proposal, o mesmo
orçamento de mensagens e os mesmos testes fail-closed.

### WL-2026-08-28-14 — Correção pós-sonda: payout fixo, gate destravado e Sessão Differs mínima

**Versão:** v1.9.11, preservada.

**Correção crítica:** a sonda de payout confirmou `payout_return_ratio=0.090000` constante em todos
os 5 símbolos e 10 barreiras testadas. O gate `min_payout_return_ratio` deixou de atuar como
otimizador e passou a ser somente piso de segurança contra degradação: default `0.088`, aceitando o
payout vigente `0.090000` e rejeitando cotações abaixo do piso. Foi adicionado teste com o payout
real observado `0.090000`, provando que a entrada retorna `EXECUTABLE_SIGNAL`.

**Escopo operacional revisado:** rotação por payout foi removida. A barreira permanece fixa em 0 e
a cotação passa a usar apenas o símbolo ativo selecionado pelo cliente ou pelo ranking existente do
sistema; nenhum novo seletor foi criado. O ID persistido permanece
`payout-routed-differs-session`; apenas o nome exibível foi alterado para “Sessão Differs”.

**Orçamento de mensagens:** o feeder agora faz somente verificação do símbolo em uso com TTL de 2s:
1 cotação por rodada, equivalente a **30 requests/minuto** e **1.800 requests/hora**. Isso reduz o
consumo anterior de 150 requests/minuto e mantém ampla margem da linha oficial compartilhada de
360 requests/minuto para `buy`, `proposal_open_contract` e reconciliação.

**Telemetria:** foi adicionado o evento `broker_payout_changed` quando a cotação DIGITDIFF da
barreira fixa diverge do baseline observado `0.090000`, sem repetir o mesmo alerta para o mesmo
símbolo/valor.

**UI:** a tela de parâmetros aceita a Sessão Differs como estratégia configurável e exibe antes do
início o custo esperado da sessão com EV exato negativo: `0.9 × 0.09 - 0.1 = -1,9%` por entrada,
em valor absoluto conforme o stake configurado. O texto não sugere lucro garantido.

**Validação:** testes focados da sessão Differs: **15 passed**. Testes focados de UI/protocolo/risco:
**37 passed**. Suíte completa: **823 passed, 4 skipped, 0 failed**. Ruff check aprovado, Ruff format
check aprovado, mypy em 216 arquivos aprovado, compileall em `apps` e `packages` aprovado e
`git diff --check` aprovado. Nenhuma credencial foi usada e nenhuma ordem Demo ou Real foi enviada.

### WL-2026-08-28-15 — Sonda diagnóstica de EV por contrato Deriv public proposal

**Versão:** v1.9.11, preservada.

**Coleta:** executada em 2026-08-27 22:27:40 -03:00 (2026-08-28 01:27:40 UTC) via WebSocket público
Deriv, sem token, sem `authorize`, sem `loginid`, sem conta e sem `buy`. Foram planejadas e
executadas 65 chamadas `proposal` read-only (13 combinações × 5 símbolos), sequenciais e com pausa
entre requisições. O plano cabia no orçamento de 300 requests/minuto, portanto não houve redução
para 2 símbolos.

**Implementação diagnóstica:** adicionada a sonda `apps.core.contract_ev_probe`, reutilizando
`DerivWebSocketClient`, `DerivOperation.PROPOSAL` e `SlidingWindowBrokerMessageBudget`. A allowlist
pública passou a aceitar somente `proposal` estritamente read-only, com tipos de contrato
necessários à sonda (`CALL`, `PUT`, `DIGITEVEN`, `DIGITODD`, `DIGITOVER`, `DIGITUNDER`,
`DIGITMATCH`, `DIGITDIFF`) e sem `passthrough`, `buy`, `sell`, autenticação ou campos de conta. A
sonda não registra estratégia, não toca catálogo, não cria migração e não persiste dado de domínio.

**Tabela completa — payout e EV por contrato:**

| Símbolo | Contrato | Barreira | Duração | Payout return | EV | Distância justo (pp) | Status |
|---|---|---:|---:|---:|---:|---:|---|
| R_10 | DIGITEVEN | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | DIGITODD | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | DIGITOVER | 4 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | DIGITUNDER | 5 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | DIGITOVER | 2 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_10 | DIGITUNDER | 7 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_10 | CALL | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | PUT | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | CALL | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | PUT | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | CALL | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | PUT | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_10 | DIGITMATCH | 0 | 1 | 7.930000 | -0.107000 | -107.000000 | OK |
| R_25 | DIGITEVEN | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | DIGITODD | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | DIGITOVER | 4 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | DIGITUNDER | 5 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | DIGITOVER | 2 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_25 | DIGITUNDER | 7 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_25 | CALL | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | PUT | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | CALL | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | PUT | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | CALL | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | PUT | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_25 | DIGITMATCH | 0 | 1 | 7.930000 | -0.107000 | -107.000000 | OK |
| R_50 | DIGITEVEN | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | DIGITODD | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | DIGITOVER | 4 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | DIGITUNDER | 5 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | DIGITOVER | 2 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_50 | DIGITUNDER | 7 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_50 | CALL | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | PUT | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | CALL | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | PUT | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | CALL | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | PUT | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_50 | DIGITMATCH | 0 | 1 | 7.930000 | -0.107000 | -107.000000 | OK |
| R_75 | DIGITEVEN | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | DIGITODD | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | DIGITOVER | 4 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | DIGITUNDER | 5 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | DIGITOVER | 2 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_75 | DIGITUNDER | 7 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_75 | CALL | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | PUT | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | CALL | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | PUT | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | CALL | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | PUT | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_75 | DIGITMATCH | 0 | 1 | 7.930000 | -0.107000 | -107.000000 | OK |
| R_100 | DIGITEVEN | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | DIGITODD | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | DIGITOVER | 4 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | DIGITUNDER | 5 | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | DIGITOVER | 2 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_100 | DIGITUNDER | 7 | 1 | 0.400000 | -0.020000 | -2.857143 | OK |
| R_100 | CALL | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | PUT | — | 1 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | CALL | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | PUT | — | 5 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | CALL | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | PUT | — | 10 | 0.950000 | -0.025000 | -5.000000 | OK |
| R_100 | DIGITMATCH | 0 | 1 | 7.930000 | -0.107000 | -107.000000 | OK |

**Ranking por EV:** melhor grupo medido: `DIGITOVER` barreira 2 e `DIGITUNDER` barreira 7, nos 5
símbolos, payout `0.400000`, EV `-0.020000` e distância do justo `-2.857143pp`. Referência
anterior: `DIGITDIFF` barreira 0, payout `0.090000`, EV `-0.019000`. Portanto nenhum contrato
medido superou o DIGITDIFF.

**Respostas objetivas:** payout constante entre os 5 símbolos: sim, para todas as combinações
medidas. EVEN e ODD pagam igual entre si: sim, `0.950000`. OVER 4 e UNDER 5 pagam igual a EVEN/ODD:
sim, `0.950000`. Rise/Fall por duração 1/5/10 ticks: não melhorou nem piorou nesta coleta, todos
`0.950000`; o EV reportado usa `p=0,5` aproximado e o EV real dependeria de drift/spread do índice,
não medido nesta sonda. Contrato com EV menos negativo que DIGITDIFF: não.

**Recomendação:** com estes números, não há base para implementar Parity Session nem Rise/Fall
agora. O melhor candidato novo ficou em EV `-2,0%`, pior que a referência DIGITDIFF `-1,9%`; os
contratos de paridade, Over/Under 4/5 e Rise/Fall ficaram em `-2,5%`; DIGITMATCH ficou em `-10,7%`
como controle negativo.

**Validação:** teste focado da sonda/validators/websocket: **41 passed**. O teste prova que a sonda
usa exclusivamente `DerivOperation.PROPOSAL`, respeita orçamento, falha fechado quando o orçamento
acaba e não emite `BUY` nem em caminho de falha/rate-limit. Nenhuma credencial foi usada e nenhuma
ordem Demo ou Real foi enviada.

## WL-2026-08-28-16 — Startup fix para perfil com Martingale Demo travado

**Data/hora:** 2026-08-28 00:40 BRT.

**Contexto:** o EXE atualizado fechava antes de abrir a UI quando o perfil padrão continha
`digit_risk_runtime` com sequência de Martingale ativa presa a um fingerprint antigo de configuração.
O caso real observado tinha `martingale_step=2`, `pinned_symbol=R_10`, nenhuma ordem Deriv não
terminal e nenhuma reserva de risco ativa.

**Correção:** `CoreRuntime.start()` agora trata `DIGIT_MARTINGALE_SEQUENCE_ACTIVE` no startup como
estado recuperável quando o perfil está flat: se não houver ordem Deriv digit não terminal nem
reserva `ACTIVE`, o Core reaplica a política atual com `reset_active_sequence=True`, mantém o P&L
diário preservado, emite `digit_runtime_startup_sequence_reset` com
`DIGIT_RUNTIME_POLICY_MISMATCH_FLAT` e permite que a aplicação abra em Safe Stop. Se houver exposição
ativa, a inicialização continua fail-closed.

**Validação:** adicionado teste de regressão
`test_startup_resets_flat_stale_martingale_sequence_after_config_drift`. Validação focada:
`tests/integration/test_storage_resilience.py` + `tests/unit/test_digit_risk_config.py` =
**35 passed**. Nenhuma ordem foi enviada.

## WL-2026-08-28-17 — Reset automático de sessão Demo ao rearmar testes

**Data/hora:** 2026-08-28 00:57 BRT.

**Contexto:** ao tentar rearmar o bot em Demo, o Health Gate mantinha bloqueios de sessão já
encerrada, como `HG_DAILY_STOP_REACHED`, impedindo novos testes mesmo quando não havia ordem aberta
nem reserva ativa.

**Correção:** `CoreLifecycleService.resume()` agora reconhece bloqueios de sessão de teste Demo
(`HG_DAILY_STOP_REACHED`, `HG_DAILY_TAKE_PROFIT_REACHED`, `HG_COOLDOWN_ACTIVE`) e, somente em
`live-demo` e com Safe Stop ativo, executa o reset de sessão antes de tentar armar novamente. Conta
Real e demais ambientes não recebem auto-reset.

**Validação:** adicionado teste para auto-reset em Demo e teste garantindo que Real não reseta
automaticamente. Validação focada: **43 passed**; Ruff, format, mypy e compileall aprovados.

## WL-2026-08-28-18 — Debug desktop completo, cache de ordem rejeitada e reset Demo

**Data/hora:** 2026-08-28 02:00 BRT.

**Contexto:** sessão manual no aplicativo desktop compilado para reproduzir a queixa operacional:
bot ligava, executava por um tempo, depois parava de abrir operações após loss/pausa/troca de
estratégia ou reset.

**Problemas reais encontrados e corrigidos:**

- Ordem Deriv rejeitada de forma síncrona podia permanecer no cache em memória como se estivesse em
  voo. O auto trader agora recarrega a projeção persistida após submissão financeira e só limpa a
  ordem do cache quando o banco comprova estado terminal. Caso não haja prova, mantém fail-closed.
- Diálogo de reset Demo podia não executar a ação após confirmação por comparação inadequada do enum
  do PySide. A comparação agora usa igualdade de valor.
- Reset Demo podia ser recusado quando o runtime já estava em Safe Stop, mas a flag do serviço estava
  defasada. O reset agora aceita Safe Stop vindo do serviço ou do runtime e recarrega caches do auto
  trader após sucesso.
- A janela podia abrir maior que a área útil do monitor em telas baixas, deixando o botão inferior
  parcialmente atrás da barra do Windows. A UI agora ajusta a geometria inicial à área útil.
- O launcher interno podia permanecer vivo após o fechamento seguro porque o loop principal não
  encerrava quando o supervisor já havia convergido para `STOPPED`. O launcher agora sai com código
  0 nesse estado.
- Aplicar parâmetros de risco com o bot ligado agora aciona Safe Stop antes de enviar a nova
  configuração ao Core, evitando troca de regra em runtime ativo.

**Testes manuais no desktop:** EXE portátil aberto; aba Deriv selecionada; cards Tail Probability
Edge, Selective Differs Edge, Parity Regime Edge e Sessão Differs inspecionados; abas Resumen,
Parámetros y riesgo, Mercado en vivo e Operaciones acionadas; botão de ligar/desligar validado com
clique físico; reset de resultados validado pela UI.

**Teste Demo controlado:** após reset, o runtime ficou com `daily_pnl_minor=0`,
`consecutive_losses=0`, `martingale_step=0`, sem símbolo pinado e sem perda acumulada. Em seguida o
bot foi armado em Demo, abriu e liquidou operações, e terminou com `daily_pnl_minor=99`, zero
reservas ativas e zero ordens Deriv não terminais. O bot foi desligado por Safe Stop ao final.
Nenhuma conta Real foi usada.

**Validação automatizada:** suíte completa **836 passed, 4 skipped**; `ruff check`, `ruff format
--check`, `mypy`, `compileall` e `git diff --check` aprovados. Após a correção final do launcher,
testes focados de launcher/shutdown e regressões críticas: **23 passed**.

**Build:** pipeline canônico `build_scripts/compile_trading_lab.py` gerou `dist/TradingLab` com
scanner de segredo limpo, manifesto de 453 arquivos e health check aprovado. O smoke final do EXE
confirmou startup completo e fechamento seguro sem processo órfão. Portátil final:
`dist/TradingLab-Desktop-v1.9.11-DESKTOP-DEBUG-FIX.exe`, 57.199.104 bytes, SHA-256
`98521BFF381678C41B2505DB1874E6522CC993C5861EE63462777F4C670C8026`.

**Relatório:** `docs/DESKTOP_DEBUG_SESSION_V1_9_11.md`.

## WL-2026-08-28-19 — Retomada automática pós-pausa e piso da recuperação Martingale

**Data/hora:** 2026-08-28 11:45 BRT.

**Contexto:** o aplicativo Demo continuava exibindo sinais e criando intenções após uma loss, mas
as entradas seguintes apareciam como `REJECTED`. A sequência real auditada tinha perda de USD 1,00,
recuperação de +USD 0,97 e residual de USD 0,03. O cálculo antigo gerou stakes inválidas de USD 0,34
em `DIGITDIFF` e USD 0,04 em `DIGITODD`, produzindo rejeições sucessivas sem motivo persistido.

**Correções:** a recuperação quote-aware agora nunca retorna valor abaixo da stake base já validada
para o broker, mantendo todos os tetos de risco. O auto trader atualiza a expiração do
`HG_COOLDOWN_ACTIVE` antes de consultar o Health Gate, permitindo retomada automática no primeiro
tick posterior. O motivo de rejeição confirmado passa a ser persistido em
`outbox_messages.state_reason`, emitido no journal e refletido no motivo operacional do bot. Ordem
rejeitada não consome tentativa do lote de performance.

**Segurança:** nenhuma regra de Stop Loss, exposição, stake máxima, limite de passos, UNKNOWN ou
reconciliação foi reduzida. Deriv Real permaneceu read-only e nenhum caminho Real foi habilitado.

**Validação:** regressões focadas **146 passed**. Suíte completa **840 passed, 4 skipped, 0 failed**.
Ruff check/format, mypy, compileall e `git diff --check` aprovados. Relatório:
`docs/PAUSE_AND_REJECTION_DEBUG_V1_9_11.md`.

## WL-2026-08-28-20 — Build e validação externa Demo da retomada pós-loss

**Data/hora:** 2026-08-28 11:55 BRT.

**Build:** o pipeline canônico gerou a distribuição onedir em `dist_pause_fix/TradingLab`, com
scanner de segredo limpo, manifesto de 453 arquivos e health check aprovado. O smoke do EXE em
profile temporário isolado confirmou startup, Safe Stop, shutdown completo, banco íntegro e zero
processos órfãos.

**Teste Deriv Demo:** o artefato compilado foi conectado somente com `live-demo` e armado pela UI.
Foram observadas duas perdas naturais de USD 1,00. Em ambas, o auto trader retomou sem intervenção,
usou o piso válido de USD 1,00 em vez dos valores inválidos antigos de USD 0,34/0,04 e liquidou a
recuperação calculada com stake de USD 10,12. Foram 12 ordens Demo novas, todas liquidadas, nenhuma
nova rejeição, `pnl_application_count` máximo 1 e `release_count` máximo 1.

**Estado final:** Safe Stop confirmado, zero reservas ativas, zero ordens Deriv não terminais,
`PRAGMA integrity_check=ok`, fechamento seguro e zero processos restantes. Nenhuma conta Real foi
selecionada e nenhuma ordem Real foi enviada.

**Artefato:** `dist_pause_fix/TradingLab-Desktop-v1.9.11-PAUSE-RECOVERY-FIX.exe`, SHA-256
`4525C17A7A916B062D16B7A7AFF21173D4E85E986F82E9A122A2D32A0BD3B231`.

## WL-2026-08-28-21 — Destravamento da retomada pós-loss

**Data/hora:** 2026-08-28 16:00 BRT.

**Causas reproduzidas:** a catraca de desempenho podia exigir uma confiança acima do que a
estratégia estruturalmente produz; o cooldown de desempenho podia ser renovado sem uma sonda
liquidada; sinais eram consumidos enquanto o gate apenas bloqueava; e um pino de Martingale podia
aguardar indefinidamente um ativo sem novo sinal.

**Correções:** adicionados teto configurável de `1.0` ponto percentual à catraca, janela de
desempenho de 20 operações e 24 horas, concessão explícita de lote de sondas após cada expiração,
preservação de sinais não julgados, escape monotônico do pino após 300 segundos e ação
`digit_operator_manual_resume` que persiste o reset transitório sem apagar P&L ou histórico. O
canal de estado da UI agora mostra exigido, estimado, P&L da janela, quantidade de operações,
tempo restante e sondas previstas. Nenhum gate, cooldown ou limite de perdas foi removido.

**Validação:** `pytest` **840 passed, 4 skipped**; Ruff check/format, mypy, compileall e
`git diff --check` aprovados. Nenhuma ordem externa foi enviada nesta alteração; Deriv Real
permanece read-only.

Os testes de regressão da fatia foram adicionados em `tests/unit/test_post_loss_resume.py` e
passaram isoladamente (**5 passed**), cobrindo teto da catraca, janela expirada, preservação de
sinal, retomada manual e transparência do bloqueio.

## WL-2026-08-28-22 — Seleção individual de estratégia e estresse explícito

**Data/hora:** 2026-08-28 17:00 BRT.

**Decisão:** o modo de seleção agora é único, conjunto ou estresse. `SINGLE` é o padrão e
`active_strategy_id` governa exclusivamente a execução; `MULTI` usa somente o conjunto escolhido;
`STRESS` considera todas as estratégias registradas e continua restrito à conta Demo. O campo
legado `stress_test_all_strategies_enabled` permanece apenas para compatibilidade de wire/persistência
e não compete com `selection_mode`.

**Migração/UI:** perfis antigos com a flag legada verdadeira podem ser carregados como STRESS quando
o tipo Demo for informado; em Real são reduzidos a SINGLE com o primeiro id determinístico. A tela
de configuração ganhou seletor de modo, seleção exclusiva no modo único, checkboxes no modo conjunto
e toggle de teste de carga rotulado como Demo-only. Seleção vazia ou id órfão falha fechado.

**Validação:** regressões de seleção **6 passed**; conjunto focado (config/store/UI/Core) **53
passed**; suíte completa **851 passed, 4 skipped**; Ruff, formatação, mypy, compileall e
`git diff --check` aprovados. Nenhuma matemática de estratégia, arbitragem ou proteção de risco foi
alterada; máximo de uma ordem em voo e Deriv Real read-only permanecem vigentes.

## WL-2026-08-28-23 — Revisão final e build da seleção de estratégias

**Data/hora:** 2026-08-28 18:20 BRT.

**Correção final:** removida a compatibilidade que inferia estresse a partir de uma configuração
antiga. O motor agora obedece exclusivamente a `selection_mode`: SINGLE executa apenas o id ativo,
MULTI executa somente os ids habilitados e STRESS é bloqueado quando o ambiente é Real. O tipo de
conta é encaminhado ao carregamento do perfil para que a migração Demo/Real seja determinística.
Sinais não selecionados continuam disponíveis apenas como sombra e não geram rejeições artificiais.

**Validação local:** `pytest` **853 passed, 4 skipped**; Ruff check, Ruff format check, mypy,
compileall e `git diff --check` aprovados. Os testes cobrem seleção SINGLE/MULTI/STRESS, ids órfãos,
persistência, migração da flag legada por ambiente e bloqueio de estresse em Real. Nenhuma ordem foi
enviada por esta alteração.

**Build:** pipeline canônico PyInstaller onedir concluído em `dist_strategy_selection_final/TradingLab`,
scanner limpo, manifesto com 453 arquivos (SHA-256
`f6951543ca7c8e46c26e55482fac3ee8cfdc236d963e88ae62394ab6e2f2fcbe`) e health check compilado com
saída 0. Portátil final:
`dist_strategy_selection_final/TradingLab-Desktop-v1.9.11-STRATEGY-SELECTION.exe`, SHA-256
`94F7F871C9AC6B0F862E8421FBD9BB89FF40DD957D240CBBDBA1E075570AC0A2`. O artefato não contém
credenciais, bancos ou perfil pessoal.

## WL-2026-08-31-01 — Fundação Enterprise IQ Option (Fase 0)

**Data/hora:** 2026-08-31 11:00 BRT.

**Escopo:** adicionados os contratos de domínio independentes para a futura integração IQ Option:
estados de ordem, intenções, resultados de execução e eventos tipados; `BrokerPort`, mapa de
capabilities e adapter IQ Option com mapeamento de erros; SQLite State Store versionado com índices
únicos, reservas e idempotência; máquina de transições, dedupe determinístico, logging JSON com
redaction e métricas mínimas. A camada é Practice/Demo-only e não habilita execução Real.

**Validação:** testes novos de estado, deduplicação, porta/adapter e a suíte existente passaram.
Resultado da execução completa: **861 passed, 4 skipped**; os 4 skips são testes opcionais/externos.
Ruff check/format, mypy (228 arquivos), compileall e `git diff --check` aprovados. A primeira
execução completa encontrou um timeout transitório de subprocesso do Auth Agent; o teste isolado e
a execução subsequente passaram sem alteração no Auth Agent.

## WL-2026-08-31-02 — Worker Seguro IQ Option (Fase 1)

**Data/hora:** 2026-08-31 12:30 BRT.

**Implementação:** criado `WorkerProcess` assíncrono com estados de ciclo de vida, shutdown
gracioso e health check separado. O `ConnectionManager` tornou-se o único dono de conexão e
reconexão, usando backoff exponencial com jitter completo, limites configuráveis e reset somente
após sincronização. Foram adicionados circuit breakers independentes para conexão, autenticação,
dados, consultas de conta e submissão.

**Estado e recuperação:** `OrderReconciler` consulta saldo, ordens abertas/liquidadas e posições,
com janela configurável e fail-closed em divergência. `OrderQueue` é uma PriorityQueue limitada e
o `OrderCoordinator` serializa por conta/ativo, verifica liderança, conexão, breaker e idempotência,
persistindo ordem e reserva antes do dispatch. `BrokerAdapterWrapper` aplica timeout por operação e
normaliza erros do adapter.

**Validação:** testes de integração adicionados para processo, conexão/backoff, circuit breaker,
reconciliação, divergência, single writer, idempotência e backpressure. Suíte completa: **870
passed, 4 skipped**. Ruff, formatação, mypy, compileall e `git diff --check` aprovados. Nenhuma
execução Real ou ordem externa foi realizada.

## WL-2026-08-31-03 — Alta Disponibilidade IQ Option (Fase 2)

**Data/hora:** 2026-08-31 15:30 BRT.

**Implementação:** adicionados `PostgresStore` assíncrono com migração versionada e `RedisStore`
assíncrono com chaves separadas para leases, sinais e estado efêmero. `LeaderLease` implementa
aquisição/renovação/liberação com `SET NX EX`, fencing token monotônico e intervalo mínimo entre
trocas. `WorkerProcess` agora aceita standby: mantém conexão e health, não envia ordens e promove-se
somente após adquirir a lease e reconciliar.

**Supervisão e observabilidade:** criado `SupervisorClient` com heartbeat funcional e detector de
crash-loop; documentação systemd, Docker Compose e Kubernetes adicionada em
`docs/supervisor_systemd.md`. Métricas de lease/fencing/failover e campos de lease no health check
foram expostos.

**Validação:** testes de integração adicionados para PostgreSQL/Redis, aquisição concorrente,
renovação, expiração, fencing, failover, promoção e reconciliação. Suíte completa: **876 passed,
4 skipped**. Ruff, formatação, mypy, compileall e `git diff --check` aprovados. Nenhuma conta Real ou
ordem externa foi utilizada.

## WL-2026-08-31-04 — Enterprise Operacional (Fase 3)

**Data/hora:** 2026-08-31 16:30 BRT.

**Objetivo:** adicionar observabilidade SLO, deploy reversível, migrações por fases, auditoria
imutável, fault injection local, sonda de throughput, backup/restore operacional e runbooks, sem
alterar a matemática das estratégias nem habilitar execução Real.

**Implementação:** `apps/core/observability/slo.py` calcula burn rate, orçamento restante,
projeção de breach e severidade para os sete SLOs da fase. `apps/core/security/audit_log.py`
mantém cadeia SHA-256 com assinatura HMAC e consulta filtrável. A API expand/migrate/contract foi
incorporada ao módulo de migrações SQLite existente para preservar imports; nenhuma migração
publicada foi modificada. `apps/core/resilience/chaos.py` limita cenários e garante callback de
recuperação. Scripts em `deploy/` e `operations/` exigem caminhos explícitos, checksum e não
incluem vault/token; o backup exige chave externa e cifra AES-256-CBC via OpenSSL; documentação de
SLO, deploy, DR e doze runbooks foi adicionada.

**Validação:** testes novos de SLO, auditoria, caos e throughput local: **7 passed**. Ruff check,
Ruff format, mypy e compileall aprovados. O teste de carga é local, bounded e não envia mensagens a
corretora. Nenhuma conta Real, credencial ou ordem externa foi utilizada.

**Limitações:** os scripts shell são templates para execução controlada pela operação; não foram
executados contra PostgreSQL/Redis/S3 nesta máquina Windows. O canary permanece read-only/shadow.

## WL-2026-08-31-05 — Prontidão Controlada (Fase 4)

**Data/hora:** 2026-08-31 18:00 BRT.

**Objetivo:** fechar a validação de prontidão sem execução Real automática, documentando Demo,
segurança, dependências, aprovação operacional e habilitação gradual.

**Implementação:** criado `tests/e2e/test_demo_validation.py` com 24 horas simuladas, lease/fencing,
supervisor/crash-loop, invariantes de duplicidade e UNKNOWN, divergência fail-closed, checksum e
contagem de restore, auditoria sem segredos, SLO e inventário de runbooks. Adicionados
`docs/demo_validation_report.md`, `docs/security_review.md`, `docs/dependencies_review.md`,
`docs/operational_approval.md`, `docs/gradual_enablement.md` e `docs/final_documentation.md`.

**Validação:** E2E e testes de Fase 3/HA: **9 passed**; Ruff check/format, mypy, compileall e
`git diff --check` aprovados. Nenhuma credencial, conta Real ou ordem externa foi usada.

**Limitações e aprovação:** o E2E é local/simulado; soak Demo externo, pip-audit/Safety no CI,
restore contra infraestrutura e assinaturas operacionais permanecem pendentes. A conta Real segue
somente leitura e não há habilitação automática.

## WL-2026-08-31-06 — Merge, Release e Pronto para Produção Controlada (Fase 5)

**Data/hora:** 2026-08-31 19:00 BRT.

**Implementação:** adicionados workflow `.github/workflows/ci.yml` com testes, Ruff, mypy,
compileall, diff check, pip-audit, secret scanning, build e staging read-only. Criados
`docs/production_readiness.md`, `docs/pull_request_enterprise_ready.md` e
`RELEASE_NOTES_v1.0.0-enterprise-ready.md`.

**Validação:** os testes focados das Fases 3–4 permanecem verdes. A suíte histórica completa
continua dependente de um runner Windows limpo: o host local apresentou falhas de permissão em
temp/artefatos e falhas DPAPI, já documentadas, portanto não foi declarada aprovação falsa.

**Estado de release:** a branch contém dois commits locais à frente do remoto. PR, aprovação,
merge em `main`, tag e publicação de release exigem autenticação GitHub e aprovação humana; não
foram executados automaticamente. Nenhuma ordem ou conta Real foi usada.

## WL-2026-08-31-07 — Build EXE Enterprise Fases 0–4

**Data/hora:** 2026-08-31 19:45 BRT.

**Build:** pipeline canônico PyInstaller onedir/windowed executado com a branch atual. O artefato
ficou em `dist_enterprise_phase4/TradingLab/TradingLab.exe`; manifesto com 482 arquivos e hash
`638C206A45F6A9C6172318E74FF9AD62EC60EA701E7C16155D839B9F0D5CA602`. O executável tem hash
`A2665A353524602BB55EB7651A0A03C7EB5F58BA38C597EE8D3F3448E8CEB332`.

**Distribuição:** ZIP onedir `dist_enterprise_phase4/TradingLab-Desktop-v1.9.11-ENTERPRISE-PHASE4.zip`,
hash `A23C1E49191CB6AEBEFC252079F2230557261CA22AFC3E7065BF084CAA370061`.

**Checks:** scanner de segredos limpo, manifesto auto-verificado e health check compilado com
sucesso. O build mantém UI windowed, Safe Stop inicial e nenhuma capacidade de ordem Real. IQ Option
permanece apenas com a fundação isolada; login e execução externa não foram habilitados.
