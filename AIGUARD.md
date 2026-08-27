# AIGUARD — Guardrails para Desenvolvimento Assistido por IA

**Projeto:** DualTrade Desktop — Deriv + IQ Option  
**Status:** obrigatório  
**Escopo:** todo código, configuração, teste, documentação, build e release deste repositório

## 1. Finalidade

Este arquivo define limites de segurança para qualquer agente de IA ou automação que trabalhe no projeto. O objetivo é impedir que uma alteração aparentemente simples:

- ultrapasse limites de risco;
- perca o estado de uma operação;
- exponha credenciais;
- misture as semânticas de Deriv e IQ Option;
- esconda falhas para manter o bot operando.

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

Timeout ou perda de conexão depois de um possível envio produz estado `UNKNOWN`. O comando não pode ser repetido automaticamente.

### AG-INV-003 — Ambiguidade conta como exposição

Ordem `UNKNOWN` ou `SETTLEMENT_UNKNOWN` mantém reserva de risco conservadora até reconciliação ou revisão manual auditada.

### AG-INV-004 — Core é o único escritor financeiro

UI, estratégias e workers não podem gravar diretamente no banco transacional nem alterar o Risk Ledger.

### AG-INV-005 — Falhar fechado

Falha de banco, relógio, dados, catálogo, cotação, protocolo, versão ou reconciliação bloqueia novas entradas.

### AG-INV-006 — Conta real nunca é padrão

Build, perfil, instalação, teste ou atualização não pode selecionar conta real automaticamente.

### AG-INV-007 — Workers são isolados

Dependências e falhas da IQ Option não podem contaminar o Core ou o worker Deriv. O mesmo vale no sentido inverso.

### AG-INV-008 — Segredos não são observabilidade

Senha, token, cookie, cabeçalho de autenticação, código de desafio e conteúdo equivalente não podem aparecer em logs, traces, analytics, fixtures, screenshots ou relatórios.

### AG-INV-009 — Estratégia não executa

Estratégias geram sinais. Elas não escolhem stake final, não reservam risco e não chamam APIs de corretora.

### AG-INV-010 — Dinheiro não usa `float`

Saldo, stake, exposição, payout monetário e P&L usam moeda explícita e representação decimal/integer minor units.

### AG-INV-011 — Licença não abandona operação aberta

Expiração, revogação ou falha do serviço de identidade pode bloquear novas entradas, mas nunca pode matar worker, liberar reserva indevidamente ou interromper o acompanhamento/liquidação de uma ordem já aberta.

### AG-INV-012 — Identidade do produto não recebe credencial de corretora

O plano de controle de identidade/licenciamento não pode receber senha, cookie ou token de Deriv/IQ Option, nem estado financeiro operacional completo.

### AG-INV-013 — Desktop não contém segredo confiável

O executável é cliente público. Não pode conter `client_secret`, segredo mestre de licença ou chave que permita forjar entitlement/lease.

### AG-INV-014 — Estratégia executável tem proveniência

Uma estratégia só pode abrir caminho para nova entrada quando versão, hash, manifesto, compatibilidade, entitlement e status permitirem. Código arbitrário baixado remotamente não é executado no MVP.

### AG-INV-015 — Arbitragem precede risco

Signal Arbiter e Portfolio Allocator atuam antes do Risk Ledger. Sinais opostos cancelam a entrada no MVP e sinais iguais não multiplicam stake.

## 4. Ações proibidas

Um agente não deve:

- habilitar conta real para facilitar teste;
- usar credenciais reais fornecidas em texto, arquivos de exemplo ou variáveis improvisadas;
- executar ordens reais;
- remover ou contornar o Health Gate;
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
- adicionar retry genérico em métodos de submissão de ordens;
- embutir `client_secret`, segredo mestre de licença ou chave privada de assinatura no desktop;
- usar serial de disco, MAC address ou fingerprint de hardware como autenticação principal;
- registrar código de seis dígitos, access/refresh token, chave privada, cookie ou lease bruta;
- enviar credenciais de corretora ao serviço de identidade/licenciamento;
- matar worker ou abandonar ordem aberta porque licença/entitlement expirou;
- executar Python ou código arbitrário baixado como estratégia no MVP;
- ignorar manifesto/hash/status/entitlement para carregar estratégia;
- somar stakes automaticamente porque duas estratégias emitiram o mesmo sinal;
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

- Testes automatizados usam workers simulados por padrão.
- Testes de identidade/licenciamento usam provedor/servidor simulado por padrão; código OTP e tokens reais não entram em fixtures.
- Testes de integração com corretoras devem ser marcados e separados.
- Integrações externas só podem usar demo/practice durante desenvolvimento comum.
- Nunca transforme um teste externo em requisito para a suíte unitária.
- Não grave respostas de autenticação como fixtures.
- Fixtures de mensagens devem ser minimizadas e redigidas.
- Uma mudança na API IQ deve ser tratada dentro do `iqoption_worker`, sem atalhos no Core.

## 7. Política para conta real

Até o PRD e o `WORKLOG.md` registrarem a liberação formal da fase real:

- recursos reais permanecem desabilitados;
- nenhum teste usa dinheiro real;
- a UI não oferece caminho oculto para ativação;
- variáveis de ambiente não podem contornar a restrição;
- mocks não devem usar nomes ou valores que pareçam credenciais reais.

Quando a fase real for autorizada, continuam obrigatórios:

- confirmação explícita do usuário;
- entitlement explícito para modo real, lease real curta e autenticação reforçada conforme política;
- exibição inequívoca de corretora, conta, moeda e stake;
- limites conservadores;
- Health Gate integral;
- capacidade de desabilitar versão incompatível antes de novas entradas;
- trilha de auditoria.

## 8. Checklist antes de editar

- [ ] Li os documentos obrigatórios relevantes.
- [ ] Identifiquei se a mudança toca ordem, risco, segredo, persistência ou conta real.
- [ ] Entendi qual processo é dono do estado alterado.
- [ ] Defini comportamento para timeout, crash, reinício e duplicidade.
- [ ] Confirmei que a outra corretora continua independente.
- [ ] Planejei testes e atualização do `WORKLOG.md`.
- [ ] Se a mudança toca identidade/licença, confirmei que nenhuma credencial de corretora cruza o plano de controle.
- [ ] Se a mudança toca estratégia, confirmei manifesto/hash/status/entitlement e a ordem Arbiter → Allocator → Risk Ledger.

## 9. Checklist antes de concluir

- [ ] Nenhum invariant foi enfraquecido.
- [ ] Testes relevantes passaram ou a limitação está declarada.
- [ ] Não há segredo em código, log, fixture ou documentação.
- [ ] A mudança falha fechado.
- [ ] Estados ambíguos permanecem ambíguos até reconciliação.
- [ ] Expiração/revogação não abandona ordens abertas.
- [ ] Nenhuma estratégia executável contorna proveniência, arbitragem ou orçamento.
- [ ] Documentação e contratos foram atualizados.
- [ ] `WORKLOG.md` registra mudança, decisões, validação e pendências.
- [ ] A entrega informa riscos residuais sem prometer ausência total de falhas.

## 10. Regra de parada

Se uma tarefa exigir violar um invariant, usar credenciais reais, executar trade real, contornar controle de risco ou inferir resultado financeiro sem evidência, pare. Explique o conflito e solicite decisão explícita antes de continuar.

