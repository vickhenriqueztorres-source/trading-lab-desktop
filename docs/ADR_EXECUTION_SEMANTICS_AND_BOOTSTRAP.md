# ADR — Semântica de candle, decisão e bootstrap do catálogo incremental

**Data:** 2026-09-05
**Status:** aceita como contrato de implementação futura; ainda não implementada
**Escopo:** Trading Lab Desktop, Strategy Lab e manifesto público
**Fase:** CAT-00
**Requisitos relacionados:** R-DATA-001..005, R-STR-001..008, R-CAT-001..016,
R-ISO-1..6, R-PRIM-1..7, R-RES-1..11 e R-BOT-1..13.

## Contexto

O Strategy Lab e o bot possuem implementações isoladas, como exige I-14. A paridade atual dos
14 primitivos é necessária, mas não prova que a estratégia completa seja equivalente. O replay
do Lab mantém indicadores incrementais durante toda a série, enquanto o caminho atual do bot
recria a família e a alimenta novamente com uma janela limitada. Além disso, gates de composição,
horário, identidade temporal e liquidação precisam fazer parte do mesmo contrato.

Esta ADR fixa a semântica que os próximos prompts deverão transformar em modelos, vetores e
código. Ela não altera o comportamento financeiro existente e não torna o catálogo atual válido
para a nova semântica.

## Decisões

### 1. Três identidades de semântica durante a transição

- `legacy.bot.window-replay.v1`: comportamento observado no bot v1.9.11. A família é reiniciada
  por avaliação e recebe uma janela de até `min(120, warmup + 3)` candles fechados.
- `legacy.lab.incremental.v1`: comportamento observado no replay atual do Lab. Os indicadores são
  instanciados uma vez e atualizados por toda a lista ordenada.
- `tl.candle-close.v2`: contrato-alvo definido abaixo. Ele só poderá ser executado depois de
  ganhar campo versionado no manifesto, vetores públicos de estratégia completa e implementações
  compatíveis nos dois produtos.

As duas semânticas legadas não são declaradas equivalentes. Manifesto sem
`execution_semantics_version` não adquire automaticamente a semântica v2. Compatibilidade e
política de transição serão aditivas no CAT-02; bytes históricos não serão reescritos.

### 2. Significado de timestamp e vela fechada

- No Lab, `Candle.ts` é o epoch UTC inteiro do **início** da vela e é alinhado ao timeframe.
- No bot, `MarketCandle.open_time` é o início e `close_time` é o fim exclusivo do intervalo.
- A identidade do epoch de decisão é o epoch UTC de `close_time`, não o de abertura.
- Uma vela de timeframe `T` aberta em `ts` cobre `[ts, ts + T)`.
- O Lab preserva literalmente I-3: para M1, apenas `ts < floor(now, 60) - 60` é gravável ou
  utilizável. Exemplo: às 12:02:30 UTC, a última abertura permitida é 12:00:00; a abertura
  12:01:00 é excluída pelo limite estrito. O teste protegido de DST/vela corrente não muda.
- O bot só aceita `is_closed=True`, identidade exata da série e relógio de broker confiável.
  Dado parcial, atrasado, fora de ordem ou com gap crítico não gera entrada.

O limite conservador do Lab e o `is_closed` do bot são controles diferentes. O contrato público
deverá carregar o instante de abertura e fechamento sem convertê-los implicitamente.

### 3. Agregação de M1 para timeframes maiores

Para um timeframe `T`, o bucket inicia em `floor(ts / T) * T`. Um candle agregado só existe se
todos os `T / 60` filhos M1 esperados estiverem presentes, fechados, ordenados, com o mesmo ativo
exato e dentro de uma sessão válida:

- `open`: abertura do primeiro filho;
- `high`: máximo dos filhos;
- `low`: mínimo dos filhos;
- `close`: fechamento do último filho;
- `tick_volume`: soma somente quando todos os filhos têm volume conhecido; caso contrário `None`;
- `close_time`: início do bucket + `T`.

Bucket incompleto é gap, não candle de menor duração nem preenchimento sintético.

### 4. Horário de execução e aquecimento

`hours_utc=[start,end]` é intervalo UTC `[start,end)` avaliado no `close_time` da decisão.
Quando `start > end`, o intervalo cruza meia-noite. `start == end` exige definição explícita no
schema futuro e não será inferido. Sessão de mercado e janela da estratégia são gates distintos.

Indicadores continuam aquecendo com candles válidos fora da janela de entrada quando a receita
será elegível depois. Fora da janela, nenhuma ordem é criada, mas o estado incremental não é
reiniciado apenas por mudança de hora.

### 5. Bootstrap e indicadores recursivos

Na semântica v2, cada nó inicia com um snapshot fechado, contínuo e identificado. O bootstrap é
processado em ordem exatamente uma vez e possui fingerprint contendo, no mínimo:

- broker, conta/sessão e generation;
- produto, símbolo exato e timeframe;
- primitivo, parâmetros canônicos e versão;
- primeiro/último candle, quantidade, origem e revisão dos dados;
- entradas auxiliares, como níveis de timeframe maior, e suas revisões.

Após restart, o mesmo snapshot e sequência produzem o mesmo estado. Um indicador recursivo não
pode alternar silenciosamente entre “histórico completo” e “últimas 120 velas”. Se a capacidade
não comportar o warmup necessário, a receita fica `CAPABILITY_UNAVAILABLE`; a janela não é
encurtada para fazê-la operar.

### 6. Atualizações, correções e gaps

Um candle fechado novo atualiza cada nó elegível uma vez. Duplicata idêntica é idempotente.
Candle conflitante, correção histórica, gap, reconnect ou troca de generation invalida o nó e
cria reconstrução cercada por nova identidade. Reconstrução pode restaurar aquecimento e decisões
futuras, mas nunca envia ordem retroativa para sinais ocorridos durante o gap.

### 7. Composição da família

Uma decisão inclui os três primitivos e todos os gates da família: parâmetros de composição,
horário, dados requeridos, warmup, payout e elegibilidade do manifesto. F1 inclui seu limite ADX;
F4 inclui seu limite de largura; as demais famílias deverão ter seus gates enumerados nos vetores.

Paridade de primitivo isolado não substitui paridade da composição completa. O vetor público da
semântica v2 compara timestamp, direção, estágio/motivo, valores Decimal e evidência.

### 8. Decisão, entrada e liquidação

- A decisão acontece depois do fechamento comprovado da vela `t`.
- A entrada live usa cotação, payout, admissão e deadline atuais depois da decisão. Preço de
  fechamento do candle não é prova do preço de compra.
- No replay de pesquisa, a aproximação de uma operação de um candle compara o fechamento da
  próxima vela **adjacente e completa** com o fechamento de `t`; empate é perda. Ausência do
  intervalo adjacente exclui a operação, mesmo que exista uma linha posterior.
- Resultado live é exclusivamente o evento terminal da corretora aplicado pelo Core.
- O replay registra claramente que close-to-close é aproximação e não evidência de execução.

### 9. Identidade e compartilhamento

`EURUSD` e `EURUSD-OTC` são séries diferentes. A chave de série inclui broker, conta/sessão,
generation, produto, símbolo exato e timeframe. Um nó puro só é compartilhável quando série,
primitivo, parâmetros, versões, bootstrap e entradas auxiliares são idênticos.

Estado de risco, SPRT, cooldown, ordens, reservas, resultado e ciclo de vida permanece isolado por
receita/versão/conta. Sinais iguais não somam stake; sinais opostos no mesmo contexto não entram.

### 10. Aritmética, canonicalização e reprodução

Dinheiro, payout, probabilidades, indicadores e parâmetros publicados usam `Decimal` com precisão
28 e ordem documentada. O manifesto usa strings decimais. Vetores canônicos registram versão,
hash dos dados, ordem de alimentação, parâmetros e resultado serializado. Mudança matemática,
ordem de operações ou bootstrap exige nova versão e nova validação; não se atualiza apenas o hash.

### 11. Rollout e falha fechada

A ordem futura é: leitor compatível, vetores/replay, modo shadow incapaz de submeter, benchmark,
validação Practice autorizada e só então opção de execução. Atualização/reconnect/troca de catálogo
não arma o bot. Divergência entre referência e incremental bloqueia a receita. Ordens abertas
continuam ligadas à revisão original até estado terminal ou revisão manual.

## Consequências

- O catálogo atual não pode ser promovido automaticamente para v2.
- A equivalência deverá ser provada no nível de receita, não apenas de primitivo.
- A frequência poderá mudar quando gaps, horários e gates forem aplicados corretamente; essa
  mudança é resultado mensurável, não falha a ser escondida.
- O compartilhamento reduz trabalho repetido sem compartilhar autoridade financeira.
- A maior necessidade de histórico será tratada por capacidade e bootstrap, não por timeout maior.

## Fora desta ADR

Não foram alterados código, manifesto, banco, assinatura, estratégia, limiar, risco, worker,
build ou conta. CAT-02 deverá materializar o campo/versionamento; CAT-03/04 implementarão dados e
replay; CAT-08/09 implementarão o caminho incremental no bot.
