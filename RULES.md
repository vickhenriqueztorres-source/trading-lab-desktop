# RULES — Regras Obrigatórias do Projeto

**Projeto:** DualTrade Desktop — Deriv + IQ Option  
**Linguagem normativa:** “DEVE” é obrigatório; “NÃO DEVE” é proibido; “PODE” é opcional.

## 1. Arquitetura

- **R-ARCH-001:** O sistema DEVE manter processos separados para UI, Trading Core, Deriv Worker e IQ Option Worker.
- **R-ARCH-002:** O Trading Core DEVE ser a única autoridade sobre estado financeiro local.
- **R-ARCH-003:** Workers NÃO DEVEM executar estratégias, calcular stake final ou gravar no banco crítico.
- **R-ARCH-004:** A UI NÃO DEVE chamar APIs de corretora nem acessar diretamente o banco.
- **R-ARCH-005:** Integrações específicas NÃO DEVEM vazar condicionais de broker para o domínio compartilhado.
- **R-ARCH-006:** Portas pequenas por capacidade DEVEM ser preferidas a um `BrokerAdapter` monolítico.
- **R-ARCH-007:** Falha de um worker NÃO DEVE derrubar o worker da outra corretora.
- **R-ARCH-008:** Toda comunicação entre processos DEVE usar protocolo versionado, envelopes identificados e limites de tamanho.

## 2. Estado e concorrência

- **R-STATE-001:** Comandos financeiros DEVEM ser serializados por broker e conta.
- **R-STATE-002:** Toda transição DEVE validar o estado anterior.
- **R-STATE-003:** Eventos duplicados DEVEM ser idempotentes no estado local.
- **R-STATE-004:** Evento fora de ordem NÃO DEVE regredir estado terminal.
- **R-STATE-005:** Ordens não terminais DEVEM ser reconciliadas após reinício.
- **R-STATE-006:** `UNKNOWN` e `SETTLEMENT_UNKNOWN` NÃO DEVEM ser resolvidos por tempo decorrido.
- **R-STATE-007:** Filas DEVEM ser limitadas e possuir política explícita de backpressure.
- **R-STATE-008:** Eventos financeiros NÃO PODEM ser descartados.

## 3. Ordens

- **R-ORD-001:** Intenção, reserva e outbox DEVEM ser persistidas na mesma transação antes do envio.
- **R-ORD-002:** Todo comando DEVE possuir `message_id`, `correlation_id`, conta e deadline.
- **R-ORD-003:** Worker NÃO DEVE enviar comando expirado.
- **R-ORD-004:** Retry automático NÃO DEVE envolver submissão potencialmente aceita.
- **R-ORD-005:** Ordem ambígua DEVE bloquear novas entradas no escopo afetado.
- **R-ORD-006:** Liquidação DEVE atualizar ordem, ledger e P&L atomicamente.
- **R-ORD-007:** Resultado da corretora DEVE preservar payload/proveniência suficiente para auditoria, após redação.
- **R-ORD-008:** Parada do bot NÃO DEVE abandonar acompanhamento de contratos abertos.

## 4. Risco

- **R-RISK-001:** Risco DEVE ser reservado atomicamente antes do envio.
- **R-RISK-002:** Exposição DEVE incluir valores reservados, abertos e desconhecidos.
- **R-RISK-003:** Limites DEVEM existir por conta e globalmente.
- **R-RISK-004:** Saldos em moedas diferentes DEVEM manter código de moeda e conversão explícita.
- **R-RISK-005:** O MVP NÃO DEVE oferecer martingale.
- **R-RISK-006:** Estratégia NÃO DEVE alterar, ignorar ou substituir o Risk Ledger.
- **R-RISK-007:** Payout/payoff inválido, expirado ou desconhecido DEVE bloquear a entrada.
- **R-RISK-008:** Configuração de risco ativa DEVE ser imutável; mudança cria nova versão.
- **R-RISK-009:** Modo real NÃO DEVE ser selecionado por padrão.

## 5. Corretoras e produtos

- **R-BRK-001:** Deriv e IQ Option DEVEM possuir workers e dependências independentes.
- **R-BRK-002:** O Core DEVE consultar capacidades em runtime.
- **R-BRK-003:** Estratégia só PODE ser ativada quando seu manifesto for compatível com broker, produto e dados.
- **R-BRK-004:** Símbolo canônico NÃO torna fontes de dados intercambiáveis.
- **R-BRK-005:** Produto, símbolo da corretora, conta e timeframe DEVEM fazer parte da identidade da série.
- **R-BRK-006:** Mudança na integração IQ DEVE ser confinada ao worker IQ sempre que possível.
- **R-BRK-007:** Reconexão NÃO DEVE retornar diretamente a `READY`; deve sincronizar e reconciliar.
- **R-BRK-008:** Falhas repetidas DEVEM acionar circuit breaker com backoff e jitter.

## 6. Dados e tempo

- **R-DATA-001:** Cada evento DEVE guardar origem, timestamp da fonte e timestamp de recebimento.
- **R-DATA-002:** Duração local DEVE usar relógio monotônico.
- **R-DATA-003:** Expiração e deadline DEVEM considerar o relógio da corretora.
- **R-DATA-004:** Suspensão do Windows DEVE invalidar cotações e forçar sincronização.
- **R-DATA-005:** Candle incompleto, atrasado ou com gap crítico NÃO DEVE gerar entrada.
- **R-DATA-006:** Dados críticos e dados volumosos de mercado DEVEM usar armazenamentos separados.
- **R-DATA-007:** Payload externo DEVE ser validado antes de entrar no domínio.
- **R-DATA-008:** Dados brutos persistidos DEVEM passar por redação e limite de tamanho.

## 7. Persistência

- **R-DB-001:** Apenas o Single Database Writer do Core DEVE gravar no `state.db`.
- **R-DB-002:** Valores financeiros NÃO DEVEM usar `float`.
- **R-DB-003:** Migrações DEVEM ser versionadas, transacionais e testadas.
- **R-DB-004:** Migração publicada NÃO DEVE ser editada retroativamente.
- **R-DB-005:** Falha de I/O, disco cheio ou integridade DEVE fechar o Health Gate.
- **R-DB-006:** Histórico financeiro NÃO DEVE ser reescrito para esconder inconsistência.
- **R-DB-007:** Backup DEVE usar mecanismo consistente com SQLite em uso.
- **R-DB-008:** Retenção NÃO DEVE remover evidência necessária para reconciliação.

## 8. Estratégias

- **R-STR-001:** Estratégias DEVEM ser funções/componentes determinísticos sempre que possível.
- **R-STR-002:** Estado DEVE ser isolado por versão, broker, conta, produto, ativo e timeframe.
- **R-STR-003:** A estratégia inicial DEVE usar candle fechado.
- **R-STR-004:** Sinais DEVEM possuir validade e evidência estruturada.
- **R-STR-005:** “Confiança” NÃO DEVE ser apresentada como probabilidade sem calibração demonstrada.
- **R-STR-006:** O mesmo código de estratégia DEVE ser utilizável em replay e execução live.
- **R-STR-007:** Backtest NÃO DEVE misturar aleatoriamente dados temporais.
- **R-STR-008:** Rentabilidade NÃO DEVE ser assumida como propriedade da arquitetura.

## 9. Segurança e privacidade

- **R-SEC-001:** Segredos NÃO DEVEM entrar em código, banco, logs, fixtures, analytics ou pacote de suporte.
- **R-SEC-002:** Credenciais persistidas DEVEM usar proteção vinculada ao usuário do Windows.
- **R-SEC-003:** IPC NÃO DEVE usar `pickle` ou desserialização arbitrária.
- **R-SEC-004:** Atualizações DEVEM ser verificadas por assinatura e permitir rollback.
- **R-SEC-005:** Dependências DEVEM ser fixadas e builds reproduzíveis.
- **R-SEC-006:** Funções de depósito e saque NÃO DEVEM existir.
- **R-SEC-007:** Telemetria remota DEVE ser opt-in e não conter dados financeiros identificáveis ou segredos.
- **R-SEC-008:** Fixtures externas DEVEM ser minimizadas e redigidas.

## 10. Interface

- **R-UI-001:** A UI DEVE exibir claramente broker, conta, modo e moeda.
- **R-UI-002:** Practice/demo e real DEVEM ser visualmente inequívocos.
- **R-UI-003:** Todo bloqueio DEVE apresentar motivo estável e compreensível.
- **R-UI-004:** “Parar novas entradas” DEVE ser diferente de “encerrar aplicativo”.
- **R-UI-005:** A UI DEVE continuar exibindo ordens abertas, desconhecidas e em reconciliação.
- **R-UI-006:** Fechar a UI NÃO DEVE apagar estado nem interromper o Core sem encerramento seguro.

**Aplicação executável (Fase 1 / Fatia 1.4):** a UI é um processo de projeção descartável e não
importa persistência, domínio de risco ou SDK de corretora. Seu protocolo bounded não contém
credenciais de broker nem material de identidade; o único token efêmero é a capability de spawn do
canal IPC, entregue por pipe e nunca incluída no snapshot, argv, log ou banco. Saldo/clock sem fonte
autoritativa DEVEM aparecer como indisponíveis, nunca como `0`/sincronizados. Retomada remove apenas
`HG_SAFE_STOP`; qualquer outro blocker mantém novas entradas fechadas. Kill da janela não equivale
a “Encerrar com segurança” e não encerra o Core.

## 10A. Identidade e licenciamento

- **R-AUTH-001:** O cliente DEVE ver um único login do produto por e-mail + código de seis dígitos; NÃO DEVE precisar criar senha própria, informar `user_id`, copiar token ou digitar chave de licença.
- **R-AUTH-002:** E-mail NÃO DEVE ser usado como chave primária; o domínio usa `user_id` estável e e-mail mutável.
- **R-AUTH-003:** Aplicativo desktop DEVE ser tratado como cliente público e NÃO DEVE conter `client_secret` confiável.
- **R-AUTH-004:** O fluxo suportado DEVE usar Authorization Code + PKCE quando aplicável ao provedor; access tokens DEVEM ser curtos e refresh tokens DEVEM ser rotativos, expirantes e revogáveis.
- **R-AUTH-005:** Refresh token, chave privada do dispositivo e lease DEVEM ser protegidos no escopo do usuário do Windows; proteção equivalente a `LOCAL_MACHINE` NÃO DEVE ser usada para esses segredos.
- **R-AUTH-006:** Device ID DEVE ser aleatório e associado a par de chaves; serial de disco, MAC address ou fingerprint de hardware NÃO DEVEM ser fator autenticador principal.
- **R-AUTH-007:** Lease DEVE ser assinada e vinculada pelo menos a `user_id`, `device_id`, validade, plano/entitlements, brokers, strategy packs e permissão de modo real; compatibilidade de versão DEVE ser verificável.
- **R-AUTH-008:** Lease practice NÃO DEVE autorizar novas entradas por mais de 7 dias sem renovação; quando modo real for formalmente liberado, lease real NÃO DEVE autorizar novas entradas por mais de 24 horas sem renovação.
- **R-AUTH-009:** Expiração, revogação, assinatura inválida ou entitlement ausente DEVEM bloquear somente novas entradas no escopo afetado; ordens abertas continuam acompanhadas e liquidadas.
- **R-AUTH-010:** Serviço de identidade/licenciamento NÃO DEVE receber senha, cookie ou token de corretora, ordens, saldo ou histórico operacional completo.
- **R-AUTH-011:** Autenticação Deriv e IQ Option DEVE permanecer separada da identidade DualTrade.
- **R-AUTH-012:** Deriv DEVE preferir OAuth na distribuição comercial; PAT, quando permitido em protótipo, NÃO DEVE virar credencial do produto.
- **R-AUTH-013:** Credenciais/sessão IQ Option DEVEM permanecer no IQ Option Worker e armazenamento local protegido; NÃO DEVEM transitar pelo serviço de identidade.
- **R-AUTH-014:** Limite e revogação de dispositivos DEVEM ser aplicados pelo `user_id` e pelo registro criptográfico do dispositivo, sem depender de hardware fingerprint.
- **R-AUTH-015:** Código de e-mail, access token, refresh token, chave privada, lease bruta e respostas de autenticação NÃO DEVEM aparecer em logs, traces, analytics, fixtures ou pacotes de suporte.

## 10B. Plataforma de estratégias

- **R-CAT-001:** Toda estratégia DEVE possuir `strategy_id`, versão, hash, manifesto e status de ciclo de vida.
- **R-CAT-002:** O manifesto DEVE declarar brokers/produtos suportados, dados necessários, timeframe, histórico/warm-up, schema de parâmetros, classe de risco, relatório de validação e `release_status`.
- **R-CAT-003:** Código ou parâmetros alterados DEVEM gerar nova versão imutável.
- **R-CAT-004:** Estratégia NÃO DEVE ser `RELEASED` sem backtest, walk-forward, replay e practice aprovados conforme política.
- **R-CAT-005:** Strategy Runtime DEVE isolar estado por estratégia + versão + broker + conta + produto + ativo + timeframe.
- **R-CAT-006:** Signal Arbiter DEVE processar conflitos antes do Portfolio Allocator e do Risk Ledger.
- **R-CAT-007:** Sinais opostos no mesmo contexto DEVEM resultar em nenhuma entrada no MVP.
- **R-CAT-008:** Sinais iguais NÃO DEVEM somar stakes automaticamente e DEVEM gerar no máximo uma intenção arbitrada para o mesmo contexto.
- **R-CAT-009:** Portfolio Allocator DEVE respeitar orçamento por estratégia, conta e global; NÃO DEVE permitir que cada estratégia trate o saldo inteiro como orçamento próprio.
- **R-CAT-010:** Suspensão ou retirada DEVE impedir novas entradas e preservar acompanhamento das existentes.
- **R-CAT-011:** Métricas DEVEM ser separadas por versão, broker, produto, ativo, timeframe e regime.
- **R-CAT-012:** No MVP, estratégias DEVEM vir empacotadas com a aplicação; execução de Python/código arbitrário baixado remotamente É PROIBIDA.
- **R-CAT-013:** Pacote remoto futuro DEVE possuir assinatura válida, hash, manifesto, compatibilidade e entitlement correspondente antes de ser carregado.
- **R-CAT-014:** Estratégia com manifesto incompatível, hash divergente, status não liberado ou entitlement ausente DEVE falhar fechado.
- **R-CAT-015:** Continuação de tendência, reversão lateral e expansão de volatilidade DEVEM ser tratadas apenas como candidatas iniciais até evidência de validação; documentação NÃO DEVE apresentá-las como lucrativas por definição.

## 11. Testes

- **R-TEST-001:** Toda mudança em ordem, risco ou persistência DEVE incluir teste de falha relevante.
- **R-TEST-002:** Testes comuns DEVEM usar workers simulados.
- **R-TEST-003:** Testes externos DEVEM ser marcados e usar demo/practice.
- **R-TEST-004:** A suíte DEVE cobrir crash antes/depois do envio, duplicidade, evento fora de ordem, suspensão e disco cheio.
- **R-TEST-005:** Contract tests DEVEM validar cada worker contra o protocolo interno.
- **R-TEST-006:** Testes de concorrência DEVEM demonstrar que limites de risco não são ultrapassados.
- **R-TEST-007:** Scanner de segredos DEVE verificar logs e pacote de diagnóstico.
- **R-TEST-008:** Conta real NÃO DEVE ser requisito para teste automatizado.
- **R-TEST-009:** Identidade/licenciamento DEVEM possuir testes de OTP/PKCE simulado, rotação/revogação, adulteração de lease, expiração e indisponibilidade do serviço.
- **R-TEST-010:** Testes DEVEM provar que expiração/revogação de entitlement não interrompe acompanhamento/liquidação de ordens abertas.
- **R-TEST-011:** Strategy Catalog/Arbiter/Allocator DEVEM cobrir manifesto incompatível, hash divergente, status suspenso, sinais opostos, sinais iguais e orçamento excedido.

## 12. Entrega e documentação

- **R-DOC-001:** Mudanças relevantes DEVEM atualizar `WORKLOG.md`.
- **R-DOC-002:** Mudança de escopo DEVE atualizar PRD e arquitetura quando aplicável.
- **R-DOC-003:** Decisão estrutural DEVE ser registrada como decisão no worklog ou ADR futuro.
- **R-DOC-004:** Código e documentação NÃO DEVEM prometer lucro, win rate ou ausência total de falhas.
- **R-REL-001:** Distribuição Windows DEVE preferir onedir com instalador assinado.
- **R-REL-002:** Worker incompatível DEVE ser bloqueado antes da autenticação/operação.
- **R-REL-003:** Atualização NÃO DEVE ocorrer enquanto houver ordem ambígua.
- **R-REL-004:** Release real exige todos os critérios do PRD para conta real.

## 13. Processo de exceção

Se uma regra precisar mudar:

1. descreva o problema que a regra impede resolver;
2. liste riscos novos;
3. proponha alternativa segura;
4. obtenha decisão explícita;
5. atualize `AIGUARD.md`, `RULES.md`, PRD e arquitetura conforme necessário;
6. registre a decisão no `WORKLOG.md`;
7. adicione testes que provem a nova regra.
