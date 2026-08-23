# Briefing Executivo — DualTrade Desktop

**Versão documental:** 1.1  
**Fase:** Fase 0 executável  
**Plataforma-alvo:** Windows 10/11 64 bits

## Visão

O DualTrade Desktop pretende automatizar estratégias nas corretoras Deriv e IQ Option sem tratar
falha parcial como exceção rara. O produto é desenhado para preservar estado, risco e evidência
quando worker, rede, banco, relógio, corretora, UI ou serviço de identidade falham.

A arquitetura não promete rentabilidade. Seu objetivo é tornar decisões e operações auditáveis,
recuperáveis e isoladas por corretora antes de qualquer liberação comercial.

## Problema que o produto resolve

Automação de trading combina integrações instáveis, estado financeiro, decisões temporais e risco.
As falhas mais perigosas não são apenas rejeições explícitas; são resultados ambíguos, como uma
conexão perdida depois de uma submissão possivelmente aceita. O DualTrade trata essa ambiguidade
como exposição ativa e bloqueia novas entradas até obter evidência.

## Princípios inegociáveis

1. O Core é a única autoridade financeira local.
2. Intenção, reserva de risco e outbox são persistidas na mesma transação antes do dispatch.
3. Timeout potencialmente aceito vira `UNKNOWN`; não existe retry automático de submissão.
4. `UNKNOWN` continua contando como exposição.
5. Workers traduzem protocolos; não executam estratégia, não escolhem stake e não escrevem estado
   financeiro.
6. UI apresenta projeções e envia comandos; não acessa broker nem banco crítico diretamente.
7. Dinheiro usa minor units/`Decimal`, nunca `float`.
8. Strategy Runtime → Signal Arbiter → Portfolio Allocator → Risk Ledger é a ordem obrigatória.
9. Sinais opostos cancelam a entrada; sinais iguais não somam stake no MVP.
10. Licença expirada/revogada bloqueia novas entradas, mas não abandona ordens abertas.
11. Credenciais de broker nunca transitam pelo serviço de identidade DualTrade.
12. Conta real permanece proibida nesta fase.

## Estado executável atual

| Área | Estado |
|---|---|
| Core financeiro | executável local, Single Database Writer e recovery conservador |
| Worker financeiro | exclusivamente simulado em subprocesso |
| Deriv | market data pública/demo read-only; transporte fake padrão |
| IQ Option | arquitetura prevista, sem worker executável |
| Persistência | `state.db` financeiro e `strategy_data.db` de evidência separados |
| IPC | v1, TCP loopback, framed JSON, handshake/capability e limites |
| Estratégias | catálogo/runtime/arbiter/allocator simulados; nenhuma estratégia comercial |
| Replay | determinístico com journal/checkpoint e provas de crash |
| Auth/licença | serviço, vault, PKCE, device e lease apenas simulados |
| UI/launcher | não implementados |
| Conta real | proibida |

## Fluxo essencial

```text
Candle fechado e validado
→ Strategy Runtime
→ Signal Arbiter
→ Portfolio Allocator
→ Risk Ledger
→ commit de TradeIntent + RiskReservation + Outbox
→ worker simulado
→ eventos normalizados
→ persistência/reconciliação
```

O caminho de market data/shadow é separado e opera em `DECISION_ONLY`, com `dispatch=False` e
capability read-only.

## Evidências já existentes

- transações e constraints SQLite para intenção/reserva/outbox;
- crash antes/depois de commit por subprocesso real;
- recovery de outbox interrompida para estado ambíguo;
- reconciliação por evidência, idempotência e conflito;
- eventos aceito/aberto/liquidado, duplicados e fora de ordem;
- lease expirada/revogada bloqueando somente novas entradas;
- manifesto/hash/status/entitlement e ordem arbiter/allocator/risk;
- 500 candles com replay e checkpoint determinísticos;
- backfill paginado, overlap, gap, reconnect e suspensão;
- worker Deriv read-only morto/reiniciado com restauração de subscriptions;
- soak bounded/temporal/matriz com recursos do Core e subprocesso.

As evidências são locais e simuladas, salvo o smoke Deriv público explicitamente opt-in. Elas não
autorizam conta real e não demonstram retorno financeiro.

## Riscos ainda abertos

- API e política comercial da IQ Option;
- vault Windows e processo Auth Agent de produção;
- identidade remota, rotação de chaves e revogação distribuída;
- política de retenção, suporte e pacote de diagnóstico;
- UI operacional, instalador e atualização assinada;
- soak prolongado em Windows real com jitter/suspensão;
- limites de risco e critérios quantitativos finais;
- validação estatística e promoção de estratégias;
- requisitos regulatórios e regiões de distribuição;
- critérios formais para qualquer piloto real futuro.

## Próximo marco

Criar um executável local explicitamente opt-in para a matriz de soak prolongada, com publicação
atômica do relatório e retenção bounded. A fatia permanece com transportes fake, Deriv read-only,
`DECISION_ONLY` e zero dispatch financeiro.

## Critério de avanço

Uma fase só avança quando a fatia atual possui testes reproduzíveis, falha fechado, documentação
atualizada e riscos residuais explícitos. Integração externa começa por simulador/contract test,
depois Deriv demo ou IQ practice opt-in. Conta real exige decisão formal e todos os gates do PRD;
não pode ser habilitada por variável de ambiente ou conveniência de teste.

## Fontes de verdade

- produto e requisitos: [PRD](PRD_Trading_Desktop_Deriv_IQOption.md);
- arquitetura e estados: [Arquitetura](Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md);
- invariantes: [AIGUARD.md](AIGUARD.md) e [RULES.md](RULES.md);
- status e decisões: [WORKLOG.md](WORKLOG.md);
- plano de avanço: [ROADMAP.md](ROADMAP.md);
- validação: [TEST_PLAN.md](TEST_PLAN.md).

