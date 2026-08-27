# Manual do usuário — Trading Lab Desktop v1.9.11

## 1. Antes de começar

O Trading Lab v1.9.11 executa automaticamente apenas em conta Deriv Demo. Uma conta Real pode ser
conectada para monitoramento, mas o botão de execução permanece bloqueado. Use sempre valores de
teste e confirme a situação da conta no cabeçalho antes de ligar o bot.

Requisitos:

- Windows 10 ou Windows 11 de 64 bits;
- acesso à internet para conexão live com a Deriv;
- conta Deriv Options ativa;
- API Token/PAT com permissões de leitura e operação;
- apenas uma instância do aplicativo aberta.

## 2. Abrir o aplicativo

Execute o arquivo `TradingLab-Desktop-v1.9.11-DERIV-CONNECTION-FIXED.exe`.

O executável portátil:

1. garante que não exista outra instância do Trading Lab;
2. extrai o pacote interno para uma pasta temporária;
3. inicia a árvore de processos;
4. abre a janela principal;
5. mantém o bot pausado.

Se o aplicativo já estiver aberto, a segunda execução tenta trazer a janela existente para frente e
exibe uma mensagem informativa. Ela não cria outro Core nem outro banco.

## 3. Entender o cabeçalho

O cabeçalho mostra:

- versão do aplicativo;
- modo atual, como `MODO PRÁCTICA` ou `DINHEIRO REAL`;
- estado da conexão com o Core;
- seletor de idioma `ES`/`EN`.

Nunca ligue o bot se o modo, a conta ou o saldo mostrados não forem os esperados.

## 4. Abas principais

### Visão geral

Apresenta:

- exposição global atual e limite;
- P&L diário realizado;
- estado global do sistema;
- perdas consecutivas;
- card Deriv;
- card IQ Option;
- resumo das últimas operações confirmadas;
- Health Gates e operações não terminais.

### Deriv

É o centro operacional da integração Deriv. Contém:

- conexão, modo da conta, saldo e relógio;
- biblioteca das três estratégias;
- aba `Resumo` com resultados e gestão de risco;
- aba `Parâmetros e risco`;
- aba `Mercado ao vivo` com frequência de dígitos, sinais e radar de ativos;
- aba `Operações` com ordens Deriv.

### IQ Option

É uma área de arquitetura/projeção. Não existe conexão externa operacional com IQ Option nesta
versão.

### Atividade

Mostra Health Gates e histórico/projeções de operações. O estado exibido vem do Core; a UI não lê
o banco diretamente.

### Configuração

Resume escopos de conta, risco e configurações efetivas. A configuração operacional detalhada das
estratégias Deriv fica dentro da própria aba Deriv.

## 5. Criar um API Token/PAT na Deriv

Na área de API da Deriv:

1. crie um token pessoal;
2. habilite as permissões de leitura e operação/trade;
3. copie o token;
4. não publique o token em chat, e-mail, screenshot ou arquivo de texto.

O App ID do Trading Lab já está incorporado ao programa e não precisa ser digitado.

## 6. Conectar uma conta Deriv

1. Abra a aba `Deriv`.
2. Clique em `Conectar conta Deriv`.
3. Cole o API Token/PAT.
4. Clique em `Validar token e carregar contas`.
5. Aguarde a lista de contas Options ativas.
6. Escolha uma conta Demo ou Real.
7. Clique em `Conectar conta selecionada`.

Para uma conta Real, o diálogo exige:

- marcar a confirmação de dinheiro real;
- digitar `REAL`;
- selecionar explicitamente a conta.

Essas confirmações apenas permitem conectar a sessão Real read-only. Elas não liberam ordens reais
na v1.9.11.

Depois de uma conexão válida, o programa salva de forma protegida:

- ID da conta selecionada;
- tipo da conta, `demo` ou `real`;
- token de acesso.

O token fica criptografado com Windows DPAPI para o usuário atual. Em execuções futuras, o diálogo
pode mostrar `Usar conta DEMO salva` ou `Usar conta REAL salva`.

## 7. Estados de conexão Deriv

| Estado visual | Significado |
|---|---|
| Desconectado | O worker não comprovou uma sessão válida |
| Fake simulado | Aplicativo aberto sem conta autenticada |
| Prática/Demo | Conta Demo autenticada e monitorada |
| Real | Conta Real autenticada em modo somente leitura |
| Relógio sincronizado | A diferença observada está dentro do limite aceito |
| Relógio não confiável | Novas entradas ficam bloqueadas |

Ao conectar, o Core pode fazer até três tentativas iniciais. Depois de uma queda de sessão já
autenticada, a recuperação supervisionada usa atrasos de 0, 1, 2, 5, 10 e 30 segundos, repetindo o
último intervalo enquanto necessário. Nenhuma ordem ambígua é reenviada durante a reconexão.

## 8. Selecionar estratégia

Na biblioteca à esquerda da aba Deriv, escolha uma das três estratégias:

- Tail Probability Edge;
- Selective Differs Edge;
- Parity Regime Edge.

A seleção altera a estratégia operacional salva no Core. Se o bot estiver ligado, o aplicativo faz
Safe Stop antes de aplicar a mudança. O bot não volta a ligar sozinho.

Os estados mostrados nos cards são:

- `AQUECENDO`: ainda não há 500 ticks suficientes;
- `MONITORANDO`: dados suficientes, mas nenhum sinal passou nos filtros;
- `SINAL DEMO ELEGÍVEL`: existe candidato estatístico;
- `BLOQUEADA`: o contexto de dados é inválido ou não confiável.

## 9. Selecionar ativo

Existem dois modos.

### Seleção automática

Com `Seleção automática de ativo` marcada, o radar acompanha independentemente:

- R_10;
- R_25;
- R_50;
- R_75;
- R_100.

Somente ativos negociáveis que anunciam contratos de dígitos entram no radar. O motor classifica os
candidatos pelo excedente conservador da probabilidade estimada sobre a probabilidade exigida. Se
não houver candidato válido, ele se abstém.

### Seleção manual

Ao desmarcar a seleção automática, o ativo escolhido no campo `Índice Sintético Deriv` é usado para
análise e execução. A interface oferece R_10, R_25, R_50, R_75, R_100 e variantes 1HZ suportadas
pela configuração. A disponibilidade live ainda depende da resposta da Deriv.

## 10. Configurar gestão de risco

Campos disponíveis:

- `Montante por entrada`: stake base em USD;
- `Stop Loss diário`: perda acumulada máxima da estratégia de dígitos;
- `Meta de ganho`: Take Profit diário;
- `Perdas consecutivas máximas`: de 1 a 5;
- `Pausa pós-perda`: 10, 30 ou 60 segundos na UI;
- `Limiar de confiança`: controle conservador exibido entre 90,0% e 98,0%;
- ativo manual/automático;
- Martingale delimitado e seus limites.

O Core rejeita uma configuração quando:

- stake é inferior a USD 0,35;
- Stop Loss ou Take Profit não são positivos;
- perdas máximas estão fora de 1 a 5;
- cooldown não é positivo;
- confiança está fora do intervalo aceito pelo protocolo;
- ativo ou moeda não são permitidos;
- martingale ultrapassa seus limites;
- a perda máxima projetada da sequência excede o Stop Loss;
- existe uma sequência de martingale ativa que seria alterada.

Clique em `Aplicar parâmetros`. O painel mostra se a configuração foi aceita e persiste o resultado
em `digit_risk_config.json` por escrita atômica.

## 11. Martingale delimitado

O martingale é opcional e começa desligado. Quando ativado:

- multiplicador permitido no Core: 1,10× a 3,00×;
- opções da UI: 1,25×, 1,50×, 2,00×, 2,50× e 3,00×;
- passos de recuperação: 1 a 4;
- existe teto absoluto da stake;
- a sequência completa precisa caber no Stop Loss diário;
- o limite de perdas consecutivas precisa ser pelo menos `passos + 1`;
- cada stake ainda passa novamente pelo Risk Ledger;
- ganho ou resultado zero volta ao passo 0;
- perda avança um passo, até o máximo;
- atingir o passo máximo e perder faz o estado voltar ao passo 0;
- durante recuperação, o ativo fica preso ao ativo da perda anterior.

Exemplo com stake USD 1,00, multiplicador 2×, dois passos e teto USD 4,00:

```text
Passo 0: USD 1,00
Passo 1: USD 2,00
Passo 2: USD 4,00
Perda máxima projetada da sequência: USD 7,00
```

## 12. Ligar o bot

Antes de ligar, confirme:

- conta Demo conectada;
- relógio sincronizado;
- saldo disponível;
- estratégia correta;
- configuração de risco aceita;
- sem ordem aberta ou reserva pendente;
- Health Gates sem bloqueio crítico.

Clique em `Ligar bot para testes`. O Core remove somente o Safe Stop. Qualquer outro bloqueio mantém
o bot incapaz de enviar ordens.

O bot pode mostrar:

| Motivo | Comportamento |
|---|---|
| `BOT_WARMING_UP_TICKS` | aguarda 500 ticks |
| `BOT_WAITING_FOR_STRATEGY_SIGNAL` | nenhum sinal conservador da estratégia ativa |
| `BOT_WAITING_FOR_NEW_TICK` | sinal anterior já foi consumido |
| `BOT_NO_POSITIVE_NET_EDGE` | edge não supera os filtros |
| `BOT_PERFORMANCE_COOLDOWN` | pausa temporária após desempenho recente negativo |
| `BOT_ORDER_IN_FLIGHT` | uma ordem ainda não terminou |
| `BOT_MARTINGALE_ASSET_PINNED` | recuperação espera sinal no mesmo ativo |
| `BOT_ORDER_SUBMITTED` | ordem persistida e enviada |

## 13. Pausar o bot

Clique no mesmo botão quando ele exibir a ação de parada. O Safe Stop:

- impede novas entradas;
- não fecha à força uma ordem aberta;
- não apaga sinal, ordem, P&L ou reserva;
- mantém o acompanhamento da liquidação;
- exige um sinal novo depois que o bot for ligado novamente.

## 14. Ler resultados

O resumo contabiliza somente liquidações confirmadas. Exibe:

- resultado líquido;
- total de ganhos e valor ganho;
- total de perdas e valor perdido;
- taxa observada;
- quantidade de operações decididas;
- exposição, Stop Loss, Take Profit, perdas consecutivas, cooldown e próxima stake.

Resultados em moedas diferentes não são somados. `Taxa observada` é descrição do histórico, não
previsão.

## 15. Mercado ao vivo

O painel mostra:

- frequência dos dígitos 0 a 9;
- total de ticks da janela;
- latência observada;
- estado e último sinal da estratégia selecionada;
- contrato, direção, barreira e evidência estatística;
- ranking multiativo.

Uma frequência alta ou baixa não é, isoladamente, autorização de entrada. O sinal precisa passar
por todas as janelas, pelo intervalo conservador, pelo edge mínimo, pelo desempenho recente e pelo
risco.

## 16. Exportar diagnóstico

Clique em `Exportar diagnóstico (.zip)`. O pacote contém apenas:

- `environment.json`;
- `health_gates.json`;
- `risk_summary.json`;
- `recent_events.json`;
- `manifest.json`.

O pacote não inclui banco, token, vault ou chave. Antes de publicar o ZIP, o sistema executa um
scanner local de segredos. São mantidos no máximo cinco diagnósticos e 50 MiB no diretório.

## 17. Encerrar com segurança

Use `Fechar seguro` ou feche a janela. O Launcher executa:

1. Safe Stop;
2. drenagem limitada de eventos já recebidos;
3. encerramento dos workers;
4. encerramento do Auth Agent;
5. fechamento do Core e dos bancos;
6. liberação dos locks.

Evite finalizar processos manualmente. Se ocorrer uma queda abrupta, o próximo startup executa
verificação, recovery e reconciliação antes de liberar novas entradas.

## 18. Segurança do token

- Não envie o token para suporte ou chat.
- Não coloque o token em variáveis, scripts ou arquivos de configuração improvisados.
- Se o token for exposto, revogue-o na Deriv e gere outro.
- O token precisa pertencer ao mesmo usuário Windows para ser reaberto pelo DPAPI.
- Copiar a pasta de credenciais para outro computador/usuário não deve descriptografá-la.
