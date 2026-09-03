# Strategy Lab — Briefing

## O problema

O bot `trading-lab-desktop` opera opções binárias e hoje decide "o que está bom" ao vivo, com
amostras de 10 operações. Isso gerou três defeitos que o operador sente como travamento,
falta de controle e resultado ruim:

1. Um gate de performance que aprende com 10 operações e trava o bot após um loss.
2. Estratégias rodando todas juntas, sem escolha individual efetiva.
3. Contratos de EV negativo executados em alta frequência, sem que o cliente veja o custo.

A causa comum: **validação misturada com operação, dentro do PC do cliente, com dado
insuficiente.**

## A solução

Tirar a validação do bot e colocá-la num laboratório separado — o **Strategy Lab** — que:

- coleta candles M1 e payout da IQ Option (pares reais e OTC) uma vez por dia, do seu PC;
- descobre combinações de filtros dentro de uma gramática restrita (Regime × Gatilho ×
  Confirmação) e as valida com portões estatísticos que uma série aleatória não atravessa;
- publica um **manifesto assinado** com só o que sobreviveu;
- o bot do cliente baixa o manifesto, verifica a assinatura e mostra cada estratégia como uma
  ficha de cinco números; o cliente liga o que quer;
- ao vivo, um detector estatístico (SPRT) rebaixa o que parou de funcionar, e o payout é
  checado antes de cada ordem.

O Strategy Lab é um aplicativo autônomo: possui processo, ambiente Python, configuração,
persistência, testes, agendamento e build próprios. Ele não é incluído no EXE do cliente e não
acessa o banco, IPC ou arquivos privados do `trading-lab-desktop`. A única integração operacional
é um manifesto JSON assinado e versionado, distribuído por HTTPS e validado pelo bot.

O cliente nunca vê "aquecendo", nunca vê backtest, nunca vê 40 métricas. Vê: taxa de acerto
validada com o mínimo ao lado, margem de segurança, operações por dia, pior sequência e
resultado em 1.000 operações.

## Para quem

- **Você (operador do lab)**: roda `collect` diariamente, `research` + `publish` mensalmente,
  revisa o ranking, decide o que expõe. Toca no sistema só quando um alerta pedir.
- **O cliente (usuário do bot)**: escolhe estratégias em 30 segundos com base em dados
  validados, sem entender estatística.

## O que não é

- Não é um sistema de previsão de preço. Não há ML, não há "dígito quente".
- Não promete lucro. Toda taxa de acerto aparece ao lado do break-even do payout.
- Não roda 24h em máquina sua. Coleta é job diário; distribuição é arquivo estático em CDN.
- Não depende de rede para o bot operar. Manifesto em cache vale 45 dias.

## Restrições de projeto

- Backend único: Supabase (free tier), espelho de storage em Cloudflare R2.
- IQ Option via `iqoptionapi` vendorizado; API não oficial — tratada como risco de terceiro
  com canário diário e adaptador isolado.
- Sem `float` em dinheiro ou probabilidade publicada (`Decimal`/`numeric`).
- Tempo em UTC epoch; `time.monotonic()` para prazos no bot.
- Implementações isoladas dos indicadores no lab e no bot, verificadas pelo mesmo vetor público;
  versão e hash de paridade fazem parte do manifesto e divergência falha fechado.
- Tudo reproduzível: cada número publicado pode ser regenerado do hash dos dados + seed +
  versão.

## Critério de sucesso

1. `research` sobre série embaralhada aprova **zero** candidatos.
2. Um manifesto publicado chega ao bot em ≤ 15 min, sem reiniciar, e uma estratégia nova
   aparece na UI sem release.
3. Rede fora por 30 dias → bot continua operando com o mesmo manifesto.
4. Payout cai abaixo do `payout_min` → a estratégia pausa sozinha com motivo legível.
5. Estratégia com p real 3pp abaixo do validado → SPRT rebaixa em < 120 operações.
6. Você passa um mês inteiro sem tocar no sistema além de `collect` agendado e um `publish`.

## Fases

| Fase | Entrega | Destrava |
|---|---|---|
| 0 | `primitives` + `manifest_schema` | o contrato |
| 1 | `collect` + Supabase (migrations, staging) + vendor | dado acumulando no dia 1 |
| 2 | Hub (Edge Functions, `pg_cron`, espelho R2) | distribuição |
| 3 | `research` com F1–F5 fixas | primeiro veredito real |
| 4 | Bot: `manifest_client`, catálogo dinâmico, `payout_gate`, SPRT, UI | cliente escolhe |
| 5 | `grammar` + `scorer` + holdout | descoberta de oportunidades |
| 6 | CI com os 5 testes, runbook, VPS opcional | operação sem toque |

Fases 0–1 não dependem de nada e devem começar imediatamente: o histórico OTC só existe no
feed da corretora e leva meses para acumular.
