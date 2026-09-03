Expert 1 — Pesquisador quantitativo: "o inimigo é a mineração de dados"
Um catalogador que testa milhares de combinações vai encontrar estratégias de 62% por puro acaso. Com 5.000 candidatos, esperamos ~50 com p < 0,01 mesmo em dados aleatórios. Se a ferramenta não tratar isso como problema central, ela é uma máquina de gerar ilusões bonitas. O que separa profissional de amador:

1. Espaço de busca restrito por gramática, não por força bruta. Cada candidato é obrigatoriamente Regime × Gatilho × Confirmação, um primitivo de cada categoria, nunca dois da mesma. Isso corta o espaço de milhões para ~2.000–5.000 combinações e garante que os filtros sejam pouco correlacionados (a única razão pela qual confluência adiciona probabilidade).

2. Validação em três portões sucessivos, todos fora da amostra:

Portão	O que mede	Critério
Walk-forward ancorado	
p
^
p
^
​
  em janelas nunca vistas (treino 6m → teste 2m, rolando)	Wilson inferior 95% 
>
p
min
⁡
+
1,5
>p 
min
​
 +1,5pp, 
n
≥
500
n≥500 agregado
Estabilidade	
p
^
p
^
​
  por janela do walk-forward	nenhuma janela abaixo de 
p
min
⁡
p 
min
​
 ; desvio-padrão entre janelas < 3pp
Correção de múltiplas comparações	quantos candidatos foram testados	FDR (Benjamini-Hochberg) a 5% sobre todos os candidatos da rodada; e teste de permutação: embaralhar os resultados W/L 1.000× e exigir que o 
p
^
p
^
​
  real esteja acima do percentil 99
3. Robustez de vizinhança. Perturbar cada parâmetro em ±15% (Bollinger 2,0 → 1,7 e 2,3; RSI 20 → 17 e 23). Se o 
p
^
p
^
​
  despencar, o candidato é um pico de overfitting, não uma vantagem. Aprovado só se a mediana da vizinhança também passar o break-even.

4. Probabilidade de overfitting do backtest (PBO, via CSCV). Dividir a série em 16 blocos, combinar in/out-of-sample de todas as formas, medir com que frequência o melhor in-sample fica abaixo da mediana out-of-sample. PBO > 20% = reprovado, independentemente do 
p
^
p
^
​
 .

5. Payout por (ativo, hora, dia da semana), nunca fixo. O break-even varia de 52% a 58% no mesmo par ao longo do dia. Uma estratégia de 56% é lucrativa às 08h UTC e perdedora às 21h — o catalogador tem de aprovar por faixa horária, não por ativo.

6. Sem lookahead, com atraso realista. Decisão com a vela 
t
t fechada; aposta no fechamento de 
t
+
1
t+1; entrada simulada com 15s de antecedência usando o preço em 
t
−
15
s
t−15s (ou o fechamento, com penalidade de 0,5pp por conservadorismo).

Expert 2 — Engenheiro de sistemas: "reproduzível ou não existe"
Arquitetura em quatro camadas, desacopladas:

┌─────────────────────────────────────────────────────────────────┐
│ 1. COLETA         IQ Option (candles M1 + payout por hora)      │
│                    → Parquet particionado por ativo/dia          │
│                    → checksum por partição, gaps registrados     │
├─────────────────────────────────────────────────────────────────┤
│ 2. PRIMITIVOS     biblioteca de indicadores incrementais,        │
│                    tipados por categoria (Regime|Gatilho|Conf.)  │
│                    → mesmos primitivos usados no bot (1 código)  │
├─────────────────────────────────────────────────────────────────┤
│ 3. MOTOR          gerador de candidatos (gramática) →            │
│                    simulador vetorizado fim-de-vela →            │
│                    portões estatísticos → score                  │
│                    → tudo determinístico (seed fixo, versão)     │
├─────────────────────────────────────────────────────────────────┤
│ 4. PUBLICAÇÃO     manifesto JSON assinado (Ed25519) por         │
│                    (família, ativo, TF, faixa horária)           │
│                    → o bot só aceita manifesto com assinatura    │
│                    → UI lê só o manifesto, nunca o motor         │
└─────────────────────────────────────────────────────────────────┘
Decisões que não são negociáveis:

Coleta própria da IQ Option, inclusive OTC. Dados públicos (Dukascopy) servem para bootstrap dos pares reais, mas o payout e o OTC só existem no feed da corretora. Coletor roda 24/7 num processo separado do bot, com reconexão, e registra cada gap — vela faltante é dado ausente, nunca interpolado.
Um único código de indicadores compartilhado entre catalogador e bot (pacote Python instalável). Se o Bollinger do backtest e o do bot divergirem em uma casa decimal, toda a validação é falsa. Teste de paridade obrigatório: mesma série → mesmos sinais, bit a bit.
Simulador vetorizado (NumPy/Polars) para a busca — 5.000 candidatos × 9 ativos × 2 anos M1 precisa fechar em horas, não dias. Mas o candidato aprovado é re-simulado no motor incremental do bot antes de publicar (segundo teste de paridade). Velocidade na busca, exatidão na promoção.
Manifesto assinado é o contrato. O bot recusa manifesto sem assinatura válida, com versão de primitivos diferente da instalada, ou mais velho que 45 dias. Isso impede que alguém "aprove na mão" uma estratégia editando JSON, e força a revalidação mensal.
Reprodutibilidade total: cada relatório carrega hash dos dados, versão dos primitivos, seed, parâmetros. Qualquer número da UI pode ser regenerado do zero.
Monitoramento pós-publicação dentro do bot: teste sequencial (SPRT ou CUSUM) do 
p
^
p
^
​
  ao vivo contra o validado. Desvio significativo → estratégia rebaixada para "Em observação" automaticamente, com evento. É isso que substitui a catraca do gate atual — um detector calibrado em 500+ operações, não um juiz de 10.
Stack: Python 3.12, Polars + NumPy para o simulador, DuckDB sobre Parquet para consultas, scipy.stats para Wilson/permutação, numba só se precisar. Sem framework de backtest genérico (VectorBT etc.) — a semântica "fim de vela binária com payout variável" é específica o bastante para que um motor próprio de ~800 linhas seja mais confiável que adaptar um genérico.

Expert 3 — Trader/produto: "o cliente decide em 30 segundos ou não decide"
O cliente não quer 40 métricas. Ele quer responder uma pergunta: "essa estratégia ganha dinheiro, com que frequência, e o quanto posso perder numa maré ruim?" Tudo além disso é ruído que gera desconfiança, não confiança.

A ficha de estratégia — exatamente cinco números:

┌──────────────────────────────────────────────────────────────┐
│  Reversão de Extremo · EUR/USD · M1 · 00h–06h UTC            │
│  ● APROVADA  — validada em 07/2026                           │
│                                                              │
│  Taxa de acerto validada     57,8%   (mínimo necessário 54,1%)│
│  Margem de segurança         +3,7 pp                          │
│  Operações por dia           ~11                             │
│  Pior sequência de perdas    6 seguidas (em 1.240 operações) │
│  Resultado em 1.000 ops      +$182 com stake $10, sem MG     │
│                                                              │
│  [ Ligar no bot ]        [ Ver detalhes ▸ ]                  │
└──────────────────────────────────────────────────────────────┘
Por que esses cinco: a taxa com o mínimo ao lado responde "ganha?"; a margem diz quão seguro; operações/dia responde "vou ver rodando?"; a pior sequência calibra o stop e mata a fantasia do Martingale ilimitado; o resultado em 1.000 ops traduz tudo em dinheiro na moeda do cliente. Nada de Sharpe, nada de profit factor, nada de curva de equity na primeira tela — isso fica em "Ver detalhes" para quem quiser (e você, que filtra, decide o que entra lá).

Três estados, nenhum a mais:

Aprovada — passou os três portões; pode ser ligada.
Em observação — passou nos históricos mas o ao vivo desviou, ou aprovada há menos de 30 dias; pode ser ligada em Demo.
Reprovada — não aparece na lista principal. Fica num painel secundário "por que não" com uma frase: "Acerto de 53,2% não cobre o mínimo de 54,1% neste horário."
A descoberta apresentada como oportunidade, não como estatística:

Quando o catalogador encontra uma combinação nova que passa nos portões, a UI mostra:

Nova oportunidade encontrada — Rejeição em nível · GBP/JPY · M5 · 07h–10h. 59,1% em 612 operações fora da amostra. Comportamento estável nas 8 janelas testadas. [Adicionar ao meu catálogo]

O cliente não precisa entender walk-forward. Ele vê que a máquina encontrou, testou de 8 formas diferentes, e o número ficou de pé.

Ranking padrão: por margem de segurança × raiz da frequência — premia quem é seguro e aparece; uma estratégia de 63% que dispara 1 vez por semana fica abaixo de uma de 57% que dispara 11 vezes por dia, o que é exatamente como um trader profissional prioriza.

O que a ficha proíbe: "lucro garantido", taxa de acerto sem o mínimo ao lado, resultado de Martingale apresentado como acerto (a última perna vencida não é "uma operação"), e qualquer número sem o 
n
n.

Convergência — o blueprint do Catalogador
Nome de trabalho: strategy-lab (pacote separado do bot, mesmo monorepo).

Módulos:

Módulo	Função	Saída
collector	coleta contínua de candles M1 e payout/hora da IQ Option, todos os ativos + OTC	Parquet particionado, log de gaps
primitives	indicadores incrementais tipados (Regime / Gatilho / Confirmação), compartilhado com o bot	pacote versionado
grammar	gera candidatos válidos (1 de cada categoria + faixas de parâmetro + TF + faixa horária)	lista de candidatos com hash
simulator	fim-de-vela, payout por hora, sem lookahead, atraso de 15s, stake fixo (Martingale simulado à parte)	W/L por operação
gates	walk-forward → estabilidade → FDR + permutação → vizinhança → PBO	veredito + métricas
scorer	margem × √frequência; pior sequência; resultado em 1.000 ops	ranking
publisher	manifesto JSON assinado, um por (família, ativo, TF, horário)	arquivo para o bot
monitor (no bot)	SPRT do ao vivo vs. validado; rebaixamento automático	eventos
Ciclo operacional:

Coleta roda sempre. Base cresce ~13k velas/dia/ativo.
Rodada de pesquisa mensal (ou sob demanda): gramática gera candidatos → simulador → portões → ranking. Horas de CPU, fora do bot, fora da tela do cliente.
Você revisa o ranking, decide o que expõe (aqui entra o seu filtro manual), assina o manifesto.
Bot importa; UI mostra fichas aprovadas; cliente liga o que quiser.
Monitor compara ao vivo com validado; desvio → "Em observação" + alerta para você.
Mês seguinte, rodada nova; manifesto antigo expira; o que caiu sai da lista.
Ordem de construção (cada etapa entrega algo usável sozinha):

collector — começa a acumular dados hoje; tudo o resto depende de ter meses de OTC.
primitives + teste de paridade com o bot.
simulator + gates com as 5 famílias já definidas como candidatos fixos (sem gramática ainda) — primeiro veredito real.
publisher + importador no bot + ficha de 5 números na UI.
grammar + scorer — a descoberta de oportunidades novas.
monitor no bot, substituindo a catraca do gate de performance.
O que decide se ele é profissional: as etapas 3 e 5 podem rodar sobre dados aleatórios (série embaralhada) e devem aprovar zero candidatos. Se aprovar algum, os portões estão furados. Esse é o teste de sanidade que nenhuma ferramenta amadora faz — e é o primeiro teste que eu colocaria no prompt.

