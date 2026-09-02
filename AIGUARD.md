# AIGUARD — Guardrails para Desenvolvimento Assistido por IA

**Projeto:** DualTrade Desktop — Deriv + IQ Option  
**Status:** obrigatório  
**Escopo:** todo código, configuração, teste, documentação, build e release deste repositório

## 1. Finalidade

Este arquivo define limites de segurança para qualquer agente de IA ou automação que trabalhe no projeto. O objetivo é impedir que uma alteração:

- ultrapasse limites de risco do Risk Ledger;
- perca o estado de uma operação;
- exponha credenciais sem proteção DPAPI;
- misture as semânticas de Deriv e IQ Option;
- esconda falhas para manter o bot operando de forma perigosa.

Uma implementação que “continua funcionando” em estado incerto é considerada defeituosa. O comportamento correto é interromper novas entradas, preservar evidências e reconciliar.

## 2. Ordem de leitura obrigatória

Antes de alterar o projeto, leia nesta ordem:

1. `AIGUARD.md`;
2. `RULES.md`;
3. `AGENTS.md`;
4. `PRD_Trading_Desktop_Deriv_IQOption.md`;
5. `Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md`;
6. `WORKLOG.md`;
7. instruções locais mais específicas, se existirem em subdiretórios.

Se houver conflito, preserve primeiro as restrições de segurança deste arquivo e registre a divergência. Uma mudança explícita de produto deve atualizar os documentos afetados no mesmo trabalho.

## 3. Invariantes protegidos

Os seguintes invariantes não podem ser enfraquecidos silenciosamente:

### AG-INV-001 — Persistir antes de agir

Nenhum comando financeiro pode chegar a um worker antes de intenção, reserva de risco e outbox serem confirmadas no armazenamento crítico.

### AG-INV-002 — Ambiguidade não permite retry

Timeout ou perda de conexão depois de um possível envio produz estado `UNKNOWN`. O comando não pode ser repetido automaticamente sem reconciliação prévia.

### AG-INV-003 — Ambiguidade conta como exposição

Ordem `UNKNOWN` ou `SETTLEMENT_UNKNOWN` mantém reserva de risco conservadora até reconciliação ou revisão manual auditada.

### AG-INV-004 — Core é o único escritor financeiro

UI, estratégias e workers não podem gravar diretamente no banco transacional nem alterar o Risk Ledger.

### AG-INV-005 — Falhar fechado

Falha de banco, relógio, dados, catálogo, cotação, protocolo, versão ou reconciliação bloqueia novas entradas.

### AG-INV-006 — Conta real exige seleção e confirmação explícita do operador

Nenhum build ou perfil pode operar em conta real sem que o operador selecione e arme deliberadamente o robô.

### AG-INV-007 — Workers são isolados

Dependências e falhas da IQ Option não podem contaminar o Core ou o worker Deriv. O mesmo vale no sentido inverso.

### AG-INV-008 — Segredos não são observabilidade

Senha, token, cookie, cabeçalho de autenticação, código de desafio e conteúdo equivalente não podem aparecer em logs, traces, analytics, fixtures, screenshots ou relatórios.

### AG-INV-009 — Estratégia não executa

Estratégias geram sinais e decisões estruturadas. Elas não decidem o stake final nem chamam APIs de corretora diretamente sem passar pelo Risk Ledger e Auto Trader.

### AG-INV-010 — Dinheiro não usa `float`

Saldo, stake, exposição, payout monetário e P&L usam moeda explícita e representação decimal/integer minor units.

### AG-INV-011 — Licença não abandona operação aberta

Expiração, revogação ou falha do serviço de identidade pode bloquear novas entradas, mas nunca pode matar worker, liberar reserva indevidamente ou interromper o acompanhamento/liquidação de uma ordem já aberta.

### AG-INV-012 — Identidade do produto não recebe credencial de corretora

O plano de controle de identidade/licenciamento não pode receber senha, cookie ou token de Deriv/IQ Option, nem estado financeiro operacional completo.

### AG-INV-013 — Desktop não contém segredo confiável

O executável é cliente público. Não pode conter `client_secret`, segredo mestre de licença ou chave que permita forjar entitlement/lease.

### AG-INV-014 — Estratégia executável tem proveniência

Uma estratégia só pode abrir caminho para nova entrada quando versão, parâmetros, compatibilidade e status permitirem.

### AG-INV-015 — Arbitragem precede risco

Signal Arbiter e Portfolio Allocator atuam antes do Risk Ledger. Sinais opostos cancelam a entrada no MVP e sinais iguais não multiplicam stake.

### AG-INV-016 — Execução Stealth e Anti-Detecção na IQ Option

A comunicação e submissão de ordens na IQ Option DEVE empregar técnicas avançadas de evasão e proteção contra detecção de bot:
1. **Jitter e Micro-Delays Aleatórios:** Introdução de atraso dinâmico de 50ms a 250ms nas requisições para quebrar padrões robóticos perfeitamente periódicos.
2. **Emulação de Navegador Real:** Utilização de User-Agents legítimos e modernos de navegadores Windows (Chrome/Edge), headers HTTP padronizados e TLS compatível.
3. **Controle de Cadência de Mensagens:** Limitação de chamadas WebSocket a taxas humanas normais, evitando rajadas anômalas que disparem alertas nos firewalls da corretora.
4. **Proteção Total do Risk Ledger:** Mesmo sob execução forçada ou modo Real, nenhuma ordem pode ultrapassar os limites atômicos de Stop Loss, Take Profit ou perdas consecutivas definidos pelo operador.

## 4. Ações proibidas

Um agente não deve:

- executar ordens sem seleção e armamento explícito do operador (**Ligar Bot**);
- usar credenciais reais fornecidas em texto claro, arquivos de exemplo ou variáveis improvisadas sem proteção DPAPI;
- remover ou contornar o Health Gate ou o Risk Ledger;
- transformar `UNKNOWN` em `REJECTED`, `SETTLED` ou “tentar novamente” por suposição;
- liberar reserva apenas porque um timeout passou;
- fazer worker ou UI escrever no `state.db`;
- compartilhar a mesma instância de estratégia entre corretoras/contas/ativos;
- misturar `EURUSD` e `EURUSD-OTC` como uma única série;
- introduzir martingale ilimitado, sem teto de etapas (*steps*), sem teto de stake ou que contorne a reserva atômica do Risk Ledger;
- adicionar autoatualização sem validação de assinatura e rollback;
- registrar payload bruto antes de aplicar redação de segredos;
- usar `pickle` ou desserialização arbitrária em IPC;
- criar fila em memória sem limite;
- adicionar retry genérico cego em métodos de submissão de ordens;
- embutir `client_secret`, segredo mestre de licença ou chave privada de assinatura no desktop;
- enviar credenciais de corretora ao serviço de identidade/licenciamento;
- silenciar exceção crítica e continuar em `READY`;
- alterar migração já publicada; crie uma nova migração;
- apagar histórico financeiro para “corrigir” inconsistência;
- afirmar rentabilidade, win rate garantido ou segurança absoluta.

## 5. Mudanças de alto risco

As mudanças abaixo exigem análise explícita, testes de falha e registro no `WORKLOG.md`:

| Área | Exemplos | Evidência mínima |
|---|---|---|
| Estado de ordens | novas transições, retry, reconciliação | testes de crash e sequência inválida |
| Risk Ledger | reserva, liberação, limites, moedas | testes de concorrência e propriedades |
| Persistência | schema, outbox, migrações | upgrade, rollback suportado e I/O failure |
| Autenticação | OTP, PKCE, token, sessão, dispositivo, challenge | revisão de segredo, rotação/revogação e logs |
| Licenciamento | lease, entitlement, revogação, limite de dispositivo | assinatura/adulteração, expiração, offline e ordem aberta |
| Estratégias distribuídas | manifesto, hash, assinatura, status | incompatibilidade, adulteração, suspensão e entitlement |
| feature flag, confirmação, limites | gate completo e autorização explícita |
| Workers | protocolo, comando financeiro | contract tests e compatibilidade |
| Atualizador | download, assinatura, substituição | adulteração, rollback e interrupção |
| Dados/relógio | timestamp, candle, gap, deadline | atraso, suspensão e eventos fora de ordem |

## 6. Política para chamadas externas

- Testes automatizados locais usam workers simulados por padrão.
- Testes de identidade/licenciamento usam provedor/servidor simulado por padrão; código OTP e tokens reais não entram em fixtures.
- Testes de integração com corretoras devem ser marcados e separados.
- O sistema suporta execução automatizada em **Practice (Demo)** e **Real** para Deriv e IQ Option, conforme configurado pelo operador.
- A comunicação com a IQ Option DEVE implementar a camada stealth anti-detecção com jitter e headers de navegador autênticos.
- Nunca transforme um teste externo em requisito bloqueante para a suíte unitária.
- Não grave respostas de autenticação como fixtures.
- Fixtures de mensagens devem ser minimizadas e redigidas.
- Uma mudança na API IQ deve ser tratada dentro do `iqoption_worker` e adapter correspondente, sem atalhos que comprometam a segurança.

## 7. Política para execução em Conta Demo (Practice) e Conta Real

O Trading Lab Desktop autoriza a operação automatizada em contas Demo/Practice e Conta Real conforme as seguintes diretrizes:

1. **Conta Demo / Practice:**
   - Habilitada para testes, validações e calibração de estratégias com saldo virtual da corretora.
   - Utiliza candles em tempo real, cálculos de indicadores e submissão automatizada de ordens.
   - Aplica integralmente o Risk Ledger local, persistência transacional e reconciliação.

2. **Conta Real (`REAL`):**
   - Habilitada mediante seleção deliberada do modo de conta e armamento manual pelo operador (**Ligar Bot**).
   - Execução operada sob proteção estrita do Risk Ledger (Stop Loss Diário, Take Profit, Teto de Stake, Limite de Trades Diários e Pausa por Perdas Consecutivas).
   - Execução com camada stealth anti-detecção para proteção da conta contra sinalização de automação.
   - Trilha de auditoria completa gravada no banco de dados local SQLite/WAL.

## 8. Checklist antes de editar

- [ ] Li os documentos obrigatórios relevantes.
- [ ] Identifiquei se a mudança toca ordem, risco, segredo, persistência ou conta real.
- [ ] Entendi qual processo é dono do estado alterado.
- [ ] Defini comportamento para timeout, crash, reinício e duplicidade.
- [ ] Confirmei que a outra corretora continua independente.
- [ ] Planejei testes e atualização do `WORKLOG.md`.
- [ ] Se a mudança toca identidade/licença, confirmei que nenhuma credencial de corretora cruza o plano de controle.
- [ ] Se a mudança toca estratégia, confirmei proveniência e a ordem Arbiter → Allocator → Risk Ledger.

## 9. Checklist antes de concluir

- [ ] Nenhum invariant foi enfraquecido.
- [ ] Testes relevantes passaram ou a limitação está declarada.
- [ ] Não há segredo em código, log, fixture ou documentação.
- [ ] A mudança falha fechado.
- [ ] Estados ambíguos permanecem ambíguos até reconciliação.
- [ ] Expiração/revogação não abandona ordens abertas.
- [ ] Camada stealth e de proteção anti-detecção foi preservada para a IQ Option.
- [ ] Documentação e contratos foram atualizados.
- [ ] `WORKLOG.md` registra mudança, decisões, validação e pendências.
- [ ] A entrega informa riscos residuais sem prometer ausência total de falhas.

## 10. Regra de parada

Se uma tarefa exigir violar um invariant, expor credenciais em texto claro, contornar controle de risco do Risk Ledger ou inferir resultado financeiro sem evidência, pare. Explique o conflito e solicite decisão explícita antes de continuar.
