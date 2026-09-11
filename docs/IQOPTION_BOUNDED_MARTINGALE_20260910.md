# Martingale delimitado G1/G2 da IQ Option

## Escopo

Esta implementação acrescenta uma opção de recuperação martingale somente ao executor atual da
IQ Option. O executor Deriv, suas estratégias, seu protocolo e sua persistência não participam do
ciclo.

O recurso nasce desligado. Na configuração da estratégia IQ, o operador pode selecionar:

- `Desligado`: preserva o comportamento anterior, com uma única entrada;
- `Até G1`: permite no máximo uma recuperação após a entrada base;
- `Até G2`: permite no máximo duas recuperações após a entrada base.

O multiplicador é configurável entre `1,10x` e `3,00x`, com padrão `2,00x`. Também existe um teto
explícito por entrada. A tela mostra os valores projetados de G0, G1 e G2 e a perda máxima da
sequência antes de aceitar a configuração.

## Semântica do ciclo

Uma entrada base admitida com martingale habilitado abre um ciclo durável. Somente a cor do candle
M1 fechado exato agenda o próximo nível: CALL vence em `close > open`, PUT vence em
`close < open` e `close == open` encerra como empate. O resultado financeiro informado pela IQ
atualiza P&L e histórico, mas nunca decide G1/G2. A recuperação mantém o ativo, a direção, o
produto e a estratégia efetivamente usados na entrada original; em seleção automática, isso
significa a estratégia que venceu a seleção daquele ciclo, não uma nova escolha.

A entrada é admitida somente até o segundo 24 do minuto e fixa antecipadamente o fechamento-alvo.
Após esse fechamento, o candle exato é buscado com prioridade e a recuperação deve ser enviada na
janela de 20 segundos seguinte. Ela passa novamente por payout fresco, manifesto, relógio, sessão,
limites diários e HealthGate do Core. O cooldown ordinário de perda financeira não posterga essa
sequência delimitada: a própria janela do ciclo a governa; todos os demais gates continuam válidos.
Uma vitória ou empate técnico encerra o ciclo. Uma perda em G2 também o encerra. Rejeição
confirmada não avança o nível, e submissão ou liquidação ambígua fica sob reconciliação sem repetir
a ordem. Candle parcial, ausente ou divergente cancela o gale em vez de inventar um resultado.

O limite de perdas consecutivas deve comportar G0 mais os níveis selecionados, o limite diário de
operações deve comportar a sequência, e a perda máxima projetada não pode superar o stop loss
diário. Qualquer gate financeiro continua tendo precedência sobre a recuperação.

## Persistência e parada

O estado do ciclo, o fechamento-alvo, a evidência técnica e a correlação da tentativa são gravados
antes da submissão. Após encerramento inesperado, o Core restaura a fase exata sem deduzir o nível
do P&L da corretora. Estados antigos ligados ao resultado financeiro são descartados na migração;
as respectivas ordens continuam sob acompanhamento financeiro normal. Evidência duplicada do
mesmo candle não cria uma segunda recuperação.

Desarmar o bot cancela uma recuperação ainda não enviada. Uma ordem já enviada não é apagada: ela
continua sob acompanhamento e reconciliação financeira normal.

## Limites operacionais

- Disponível somente no fluxo atualmente executável da IQ Option Practice/Demo.
- Não existe garantia de recuperação ou lucro; o recurso pode ampliar perdas rapidamente.
- Alterações de risco continuam permitidas somente com o bot IQ desarmado.
- Validação externa com login e ordem real não faz parte do build automatizado.
