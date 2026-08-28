# Estratégias Deriv e gestão de risco — v1.9.11

## 1. Escopo

Este documento descreve exatamente as três estratégias de dígitos implementadas, o radar de ativos,
os filtros de execução e a gestão de risco usada pelo Core. As estratégias são experimentais e não
representam promessa de rentabilidade.

Desde a Fase 1 multi-estratégia da v1.9.11, as implementações são descobertas por um registry local
de manifests no `strategy_catalog`. O enum dos três IDs históricos continua preservado como
contrato de persistência, mas o motor não contém mais uma tupla literal: uma quarta implementação
empacotada pode ser registrada com manifest e factory sem editar o engine.

Cada símbolo possui sua própria instância do motor e seu próprio warm-up. O `EnginePool` aceita no
máximo 12 símbolos, cria a engine quando os primeiros dados chegam e a descarta ao encerrar a
subscrição. Ticks de outro símbolo são erro de roteamento; nunca apagam silenciosamente o buffer.

Na conta Demo, o operador pode habilitar uma ou várias estratégias. Todas continuam produzindo
`SHADOW_SIGNAL`; somente as habilitadas viram candidatas executáveis. O Signal Arbiter escolhe uma
única candidata por maior margem conservadora, depois maior amostra condicional e, no empate final,
ID lexicográfico. Os descartes ficam no journal. A conta permanece limitada a uma ordem em voo.
Seleção vazia bloqueia com `BOT_NO_STRATEGY_SELECTED`. O modo estresse com todas habilitadas é
exclusivo da Demo.

Registros históricos não são reescritos, inclusive os que já possuem
`entry_mode=EXECUTABLE_DEMO` ou `entry_mode=SHADOW_ONLY`. A partir da correção pós-Fase 1 de
27/08/2026 na v1.9.11, a trilha operacional separa os conceitos: usa
`entry_mode=EXECUTABLE_SIGNAL` para a vencedora, `entry_mode=SHADOW_ONLY` para as demais e grava o
ambiente em `execution_environment` (`DEMO` ou `REAL`). O auto trader financeiro permanece restrito
à Demo, portanto seus eventos atuais registram `execution_environment=DEMO`.

## 2. Dados utilizados

Cada estratégia recebe uma sequência ordenada de `MarketTick` da Deriv. O último dígito é extraído
do `Decimal` da cotação, preservando a escala informada pelo broker.

Um contexto é rejeitado quando:

- algum tick não pertence à Deriv;
- os ticks misturam símbolos;
- o epoch regride;
- existe tick duplicado pela identidade completa.

Todos os motores exigem 500 ticks. A análise usa três janelas simultâneas:

```text
200 ticks
350 ticks
500 ticks
```

O contexto condicional considera os resultados que vieram depois de um dígito anterior com a mesma
paridade do último dígito observado. Cada janela precisa produzir pelo menos 70 observações desse
contexto.

Para reduzir ilusões de amostra pequena, o motor calcula limites do intervalo de Wilson com 99% de
confiança. As contas rápidas não monetárias podem usar ponto flutuante internamente; limites,
probabilidades apresentadas e todo dinheiro usam `Decimal` ou unidades inteiras.

## 3. Tail Probability Edge

Identificador:

```text
tail-probability-edge
```

Família de contratos:

```text
DIGITOVER
DIGITUNDER
```

Candidatos avaliados:

| Contrato | Direção | Barreira | Probabilidade mínima base |
|---|---:|---:|---:|
| DIGITOVER | OVER | 2 | 72,00% |
| DIGITUNDER | UNDER | 7 | 72,00% |
| DIGITOVER | OVER | 3 | 62,00% |
| DIGITUNDER | UNDER | 6 | 62,00% |
| DIGITOVER | OVER | 4 | 52,00% |
| DIGITUNDER | UNDER | 5 | 52,00% |

Para cada janela, a estratégia:

1. conta os acertos do candidato no contexto condicional;
2. usa o limite inferior de Wilson;
3. calcula a margem acima da probabilidade mínima;
4. seleciona o candidato com maior margem;
5. exige margem estritamente positiva;
6. exige que as três janelas escolham o mesmo contrato, direção e barreira.

Se todas as condições passarem, gera um sinal de duração de um tick. Caso contrário, continua
monitorando.

Principais reason codes:

- `TAIL_EDGE_WARMING_UP`;
- `TAIL_EDGE_TICK_CONTEXT_INVALID`;
- `TAIL_EDGE_CONTEXT_INSUFFICIENT`;
- `TAIL_EDGE_NO_CONSERVATIVE_ADVANTAGE`;
- `TAIL_EDGE_WINDOWS_DISAGREE`;
- `TAIL_EDGE_CONSERVATIVE_SIGNAL`.

## 4. Selective Differs Edge

Identificador:

```text
selective-differs-edge
```

Contrato:

```text
DIGITDIFF
```

Funcionamento:

1. em cada janela, conta quantas vezes aparece cada dígito de 0 a 9;
2. escolhe o dígito menos frequente; em empate, escolhe o menor dígito;
3. calcula o limite superior de Wilson para a chance desse dígito aparecer;
4. converte isso no limite inferior conservador da chance de o próximo dígito ser diferente;
5. exige valor acima de 92,25%;
6. exige que as três janelas escolham o mesmo dígito.

O dígito escolhido vira a barreira/predição do contrato. O prazo é de um tick.

Principais reason codes:

- `DIFFERS_EDGE_WARMING_UP`;
- `DIFFERS_EDGE_TICK_CONTEXT_INVALID`;
- `DIFFERS_EDGE_CONTEXT_INSUFFICIENT`;
- `DIFFERS_EDGE_NO_CONSERVATIVE_ADVANTAGE`;
- `DIFFERS_EDGE_WINDOWS_DISAGREE`;
- `DIFFERS_EDGE_CONSERVATIVE_SIGNAL`.

## 5. Parity Regime Edge

Identificador:

```text
parity-regime-edge
```

Família de contratos:

```text
DIGITEVEN
DIGITODD
```

Funcionamento:

1. conta pares e ímpares no contexto condicional de cada janela;
2. escolhe `DIGITEVEN` quando pares são maioria ou há empate;
3. escolhe `DIGITODD` quando ímpares são maioria;
4. calcula o limite inferior de Wilson para a classe escolhida;
5. exige resultado acima de 52,00%;
6. exige que as três janelas escolham o mesmo contrato.

Principais reason codes:

- `PARITY_EDGE_WARMING_UP`;
- `PARITY_EDGE_TICK_CONTEXT_INVALID`;
- `PARITY_EDGE_CONTEXT_INSUFFICIENT`;
- `PARITY_EDGE_NO_CONSERVATIVE_ADVANTAGE`;
- `PARITY_EDGE_WINDOWS_DISAGREE`;
- `PARITY_EDGE_CONSERVATIVE_SIGNAL`.

## 6. Estados do motor estatístico

| Estado | Significado |
|---|---|
| `WARMING_UP` | menos de 500 ticks válidos |
| `MONITORING` | dados válidos, sem candidato aprovado |
| `SHADOW_SIGNAL` | candidato estatístico completo |
| `DATA_BLOCKED` | contexto inválido |

O termo `SHADOW_SIGNAL` indica que a estratégia só produz evidência. Ela não define stake, não
reserva risco e não chama a Deriv. O `DerivDigitAutoTrader` transforma um sinal elegível da
estratégia ativa em uma solicitação ao pipeline financeiro do Core.

## 7. Radar automático multiativo

O radar mantém um `DerivDigitShadowEngine` separado por símbolo. Cada ativo conserva sua própria
janela, aquecimento, última identidade e decisões. Adicionar ou remover outros ativos não reinicia o
histórico dos que continuam no universo.

O universo live é redescoberto a cada 300 segundos e considera somente:

```text
R_10
R_25
R_50
R_75
R_100
```

O ativo precisa estar negociável e anunciar ao menos um contrato dentre:

```text
DIGITOVER, DIGITUNDER, DIGITDIFF, DIGITEVEN, DIGITODD
```

O ranking ordena:

1. candidatos antes dos demais estados;
2. maior margem conservadora primeiro;
3. símbolo em ordem lexical para desempate determinístico.

Somente o primeiro candidato recebe `selected=True`. Se nenhum ativo tiver sinal conservador, o
radar se abstém. Uma falha em uma assinatura secundária não desconecta automaticamente o ativo
selecionado pelo operador.

## 8. Filtros adicionais do auto trader

Mesmo um `SHADOW_SIGNAL` não vira ordem automaticamente. A execução exige:

1. operador ter ligado o bot;
2. dispatcher do Core ativo;
3. fonte `DEMO_LIVE` conectada;
4. ao menos 500 ticks;
5. nenhuma ordem Deriv não terminal;
6. sinal pertencer à estratégia ativa;
7. sinal ser novo e posterior ao último rearm;
8. regra de martingale permitir o ativo;
9. desempenho recente não estar bloqueado;
10. margem estatística superar o edge mínimo configurado;
11. Risk Ledger fornecer uma stake válida;
12. Health Gate permanecer aberto;
13. autorização do token/lease aceitar nova entrada.

### Edge mínimo configurado

O controle de confiança é transformado em um piso adicional:

```text
edge_floor = 1,00 + (confiança - 90,0) / 4
```

Exemplos:

| Confiança | Piso adicional |
|---:|---:|
| 90,0% | 1,00 ponto percentual |
| 92,5% | 1,625 ponto percentual |
| 94,0% | 2,00 pontos percentuais |
| 98,0% | 3,00 pontos percentuais |

O candidato precisa ter probabilidade estimada maior ou igual à probabilidade exigida mais esse
piso.

### Filtro de desempenho recente

Para cada combinação estratégia + símbolo, o Core consulta até 30 operações liquidadas.

- Com pelo menos 10 liquidações e P&L total não positivo, aplica cooldown de desempenho de 10
  minutos desde a última liquidação.
- Depois do cooldown, libera um lote de prova de até 10 ordens.
- Quando médias de ganho e perda estão disponíveis, calcula o break-even do payout e aumenta a
  probabilidade mínima se necessário.
- Esse bloqueio não é permanente; ele alterna cooldown e prova limitada.

## 9. Construção da ordem

Uma ordem candidata usa:

| Campo | Valor |
|---|---|
| broker | DERIV |
| conta | conta Demo selecionada |
| produto | tipo do contrato da estratégia |
| símbolo | ativo selecionado |
| direção canônica | CALL |
| duração | 1 tick |
| prediction_digit | barreira para contratos que exigem dígito |
| versão da estratégia | `1.9.11-resilient-connection-and-performance` |
| deadline | 10 segundos após criação |

A direção canônica `CALL` é um detalhe do modelo interno. O contrato Deriv real é definido pelo
campo de produto (`DIGITOVER`, `DIGITUNDER`, `DIGITDIFF`, `DIGITEVEN` ou `DIGITODD`).

## 10. Uma ordem por vez e sinal sem retry

O auto trader bloqueia nova entrada enquanto existir ordem Deriv em estado não terminal. Chaves de
sinal já analisadas são mantidas em um conjunto limitado a 512 entradas.

Uma falha de risco, autorização ou transporte consome aquele sinal. O sistema espera um novo tick e
um novo sinal; ele não tenta reutilizar automaticamente uma oportunidade de um tick.

## 11. Gestão de risco especializada

### Valores padrão

| Parâmetro | Padrão |
|---|---:|
| Stake | USD 1,00 |
| Stop Loss diário | USD 50,00 |
| Take Profit diário | USD 30,00 |
| Perdas consecutivas máximas | 1 |
| Cooldown pós-perda | 30 s |
| Confiança | 92,5% |
| Ativo | R_100 |
| Seleção automática | ligada |
| Estratégia | Tail Probability Edge |
| Martingale | desligado |
| Multiplicador | 2,00× |
| Passos | 2 |
| Teto de stake | USD 4,00 |

### Limites globais padrão

| Limite | Valor |
|---|---:|
| Exposição global | USD 500,00 |
| Exposição por símbolo | USD 200,00 |
| Stop Loss consolidado | USD 100,00 |
| Perdas consecutivas consolidadas | 3 |
| Moeda de referência | USD |

As regras especializadas e globais são cumulativas. Passar em uma não contorna a outra.

## 12. Stop Loss, Take Profit e cooldown

O P&L de dígitos é atualizado somente a partir de settlement confirmado.

- P&L menor ou igual a `-Stop Loss` bloqueia com `HG_DAILY_STOP_REACHED`.
- P&L maior ou igual ao Take Profit bloqueia com `HG_DAILY_TAKE_PROFIT_REACHED`.
- Cada perda incrementa perdas consecutivas.
- Ao atingir o limite, começa o cooldown monotônico e bloqueia com `HG_COOLDOWN_ACTIVE`.
- Quando o cooldown expira, o próximo tick atualiza o Health Gate, persiste a expiração, reseta
  perdas consecutivas/passo do Martingale e retoma a análise automaticamente.
- Um ganho reseta perdas consecutivas.

Existe ainda uma pausa de desempenho independente: depois de pelo menos 10 liquidações de uma
estratégia/ativo com resultado líquido não positivo, esse escopo aguarda até 10 minutos e então
libera um lote limitado de novas tentativas. Essa pausa não é `HG_COOLDOWN_ACTIVE`, não desarma o
bot e não impede outras estratégias/ativos elegíveis.

## 13. Bounded Martingale por retorno líquido

O multiplicador geométrico deixou de determinar a stake. Antes de cada recuperação, o Core pede
uma proposta somente de leitura ao worker Deriv e calcula o retorno líquido real:

```text
retorno_líquido = (payout - ask_price) / ask_price
stake_recuperação = ceil(prejuízo_pendente / retorno_líquido)
stake_final = max(stake_base_válida, stake_recuperação)
```

Exemplo: uma perda de USD 1 em `DIGITDIFF` requer USD 10 quando a proposta paga 10% líquidos, ou
USD 11,12 quando paga 9%. A cotação não compra o contrato. A ordem só é criada depois que a stake
calculada passa pelo Risk Ledger.

A stake de recuperação nunca fica abaixo da stake base já validada para o broker. Isso cobre o
caso de uma recuperação parcial deixar poucos centavos pendentes: o sistema usa a base válida em
vez de gerar uma ordem abaixo do mínimo da Deriv e entrar em ciclo de rejeições.

O Core prefere recuperar todo o prejuízo pendente. Se isso não couber, divide o prejuízo entre as
tentativas restantes e calcula a stake de cada parcela pela mesma cotação. Se nem a parcela couber,
a recuperação falha fechada com `DIGIT_MARTINGALE_RECOVERY_UNAFFORDABLE`; nunca existe clamp
silencioso.

Restrições:

- 1 a 4 passos;
- teto de stake não pode ser menor que a base;
- teto não pode ultrapassar o Stop Loss diário;
- limite de perdas precisa acomodar toda a sequência;
- próxima stake precisa caber no orçamento de perda restante;
- proposta precisa possuir `payout > ask_price` e retorno líquido finito e positivo;
- restart exige cotação nova; payout anterior não é reutilizado;
- configuração não pode mudar no meio de uma sequência ativa.

Transição:

```text
settlement com perda e passo < máximo                  → prejuízo pendente + perda; passo + 1
ganho menor que o prejuízo pendente e tentativa livre  → reduz prejuízo; passo + 1
ganho que cobre o prejuízo pendente                     → passo 0 e prejuízo 0
settlement no passo máximo                              → encerra sequência delimitada
```

O martingale não é uma estratégia e não aumenta a probabilidade de acerto. Ele somente altera a
stake depois de uma liquidação confirmada, sob limites explícitos. Ele não garante recuperação:
uma cotação ou orçamento inviável bloqueia a entrada.

## 14. Persistência da configuração

O Core salva a configuração em:

```text
%LOCALAPPDATA%\TradingLab\profiles\default\core\digit_risk_config.json
```

O documento possui `schema_version: 1`. A gravação usa arquivo temporário, flush, `fsync` e
`os.replace`. Conteúdo ausente, corrompido ou inválido volta aos padrões seguros.

## 15. Latência

O auto trader mede:

- sinal/tick até início da análise;
- duração da análise;
- duração da submissão ao pipeline do Core.

O buffer de ticks é limitado e os cálculos são incrementais onde aplicável. Ainda assim, latência
baixa não garante melhor resultado: contratos de um tick continuam sujeitos a rede, processamento
da Deriv, payout e aleatoriedade do fluxo observado.
