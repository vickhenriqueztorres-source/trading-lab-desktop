# Solução de problemas — Trading Lab Desktop v1.9.11

## 1. Aplicativo não abre

### Verifique se já existe uma instância

O lançador portátil permite uma única instância. Procure a janela na barra de tarefas. Ao abrir o
EXE de novo, a janela existente deve ser restaurada.

### Se a janela não existir

1. aguarde alguns segundos: o pacote portátil precisa ser extraído;
2. confirme que o Windows não bloqueou o arquivo baixado;
3. não mova/apague a pasta temporária enquanto o processo estiver aberto;
4. tente `Fechar tarefa` apenas se não houver operação aberta e o app estiver comprovadamente
   travado;
5. abra novamente para permitir recovery.

Se ocorrer repetidamente, exporte um diagnóstico quando a interface abrir. Não envie o banco ou o
token.

## 2. Mensagem de segunda instância

Significa que o mutex do lançador encontrou outra sessão. Use a sessão existente. Não renomeie o
EXE nem apague locks para contornar o bloqueio.

## 3. `DERIV_ACCOUNT_CONNECT_FAILED`

Na v1.9.11, a conexão inicial tenta até três vezes e limpa workers incompletos entre tentativas.
Ainda assim, a falha pode ocorrer por:

- token revogado/expirado;
- token sem permissão trade;
- internet indisponível;
- conta Options inativa;
- tipo de conta diferente do selecionado;
- indisponibilidade da API Deriv;
- schema externo incompatível.

Ações:

1. mantenha o bot pausado;
2. confirme internet e data/hora do Windows;
3. gere um token novo na Deriv com leitura e operação;
4. clique em `Conectar conta Deriv` e valide novamente;
5. selecione explicitamente a conta correta;
6. se persistir, exporte diagnóstico.

## 4. `DERIV_CONNECTION_TIMEOUT`

A Deriv não completou REST/OTP/websocket no prazo mesmo após repetição. Não significa que uma ordem
foi rejeitada. Durante login, tente novamente quando a rede estabilizar. Durante uma sessão já
operacional, o Core bloqueia novas entradas e executa reconexão supervisionada.

## 5. `DERIV_AUTH_FAILED`

O token foi recusado. Revogue tokens expostos, gere um novo e confirme as permissões. O App ID é
interno; não substitua o token pelo App ID.

## 6. Conta conecta e depois cai

O monitor pode detectar perda no websocket, relógio ou subscription. O Core:

- marca a telemetria desconectada;
- fecha novas entradas;
- para o auto trader;
- encerra o worker antigo;
- pede OTP novo em um worker novo;
- restaura subscriptions;
- reconcilia ordens não terminais;
- só retoma entrada se o operador já havia armado e os gates estiverem abertos.

Se a queda virar ciclo contínuo, deixe o bot pausado e investigue rede, antivírus/proxy e status da
Deriv.

## 7. Saldo indisponível

Possíveis motivos:

- modo público/fake sem autenticação;
- sessão caiu;
- resposta de saldo inválida;
- moeda/precisão não suportada;
- worker ainda aquecendo.

O aplicativo mostra `indisponível`, não zero, quando não existe prova autoritativa.

## 8. Frequência de dígitos zerada

O painel depende da subscription do ativo selecionado. Verifique:

- conta/worker conectado;
- ativo disponível;
- relógio confiável;
- pelo menos um tick recebido;
- radar/ativo não em retry de subscription.

O histórico de 500 ticks é carregado em páginas de até 100. Durante aquecimento, o total cresce até
o limite.

## 9. Bot ligado, mas não abre operações

Isso pode ser comportamento correto. Consulte o tooltip/status do bot:

- aquecimento abaixo de 500 ticks;
- estratégia sem sinal;
- janelas discordam;
- edge conservador insuficiente;
- filtro de payout/desempenho;
- cooldown temporário de desempenho;
- sinal antigo já consumido;
- ordem em andamento;
- martingale preso a outro ativo;
- Stop Loss, Take Profit ou cooldown;
- Health Gate fechado;
- conta Real somente leitura.

Não reduza filtros ou aumente stake apenas para forçar operações.

## 10. Bot não volta após pausar/trocar estratégia

A troca de estratégia aplica Safe Stop. Ao ligar novamente, o auto trader registra o epoch atual e
exige um sinal novo. Isso evita executar um sinal antigo de um tick. Aguarde novo tick e um novo
sinal válido.

## 11. Martingale não avançou

O passo avança somente depois de settlement confirmado com P&L negativo. Ele não avança em:

- ordem ainda aberta;
- ordem rejeitada;
- timeout ambíguo;
- resultado sem P&L confirmado;
- martingale desativado;
- ganho ou empate;
- cooldown que resetou a sequência.

Também verifique se a configuração foi aplicada e se o painel mostra `Mpasso/máximo`.

## 12. Martingale bloqueado

Reason codes comuns:

- `DIGIT_MARTINGALE_SEQUENCE_ACTIVE`: tentativa de alterar configuração no meio da sequência;
- `DIGIT_MARTINGALE_LOSS_LIMIT_TOO_LOW`: perdas máximas menores que passos + 1;
- `DIGIT_MARTINGALE_SEQUENCE_EXCEEDS_STOP_LOSS`: soma projetada excede Stop Loss;
- `DIGIT_MARTINGALE_MAX_STAKE_INVALID`: teto inválido;
- `HG_DAILY_STOP_REACHED`: próxima stake não cabe no orçamento restante.

## 13. Resultados só atualizam ao pausar

Na v1.9.11, a UI mantém polling a cada 500 ms e continua após timeout transitório. Se o painel não
atualizar:

1. veja se o Core aparece conectado;
2. confira se a ordem já recebeu `SETTLED`;
3. aguarde até um segundo para a próxima projeção;
4. troque de aba e volte apenas para confirmar renderização;
5. exporte diagnóstico se o Core estiver conectado e o estado permanecer antigo.

## 14. `HG_SAFE_STOP`

É o bloqueio do operador e o estado inicial normal. Clique em ligar o bot para pedir ao Core que
limpe somente esse blocker. Se outro gate existir, a retomada é recusada.

## 15. `HG_ORDER_UNKNOWN`

O envio pode ter sido aceito, mas a resposta não foi comprovada. Não reinicie repetidamente, não
apague banco e não tente reenviar manualmente. O sistema mantém a reserva e reconcilia por
evidência.

## 16. `HG_COOLDOWN_ACTIVE`

O limite de perdas consecutivas foi atingido. Aguarde o contador monotônico. Quando expira, o Core
zera perdas consecutivas especializadas e o passo do martingale.

## 17. `HG_DAILY_STOP_REACHED` ou `HG_DAILY_TAKE_PROFIT_REACHED`

O limite diário foi atingido. A versão atual não oferece reset manual de produção na UI. Encerrar e
abrir não deve ser usado como técnica para contornar risco; preserve a evidência e revise os
resultados.

## 18. Relógio não confiável

Confira sincronização do Windows e estabilidade da conexão. Suspender/retomar o computador invalida
o estado temporal e exige ressincronização. Não force entradas enquanto esse bloqueio existir.

## 19. Ordem presa em aberto

Não feche o processo à força. Pause novas entradas e mantenha o aplicativo conectado para receber o
settlement. Se houver queda, o recovery consulta contrato/statement/profit table. Estado só muda com
evidência compatível de conta, símbolo, valor e contrato.

## 20. Diagnóstico falhou

Possíveis motivos:

- scanner detectou padrão sensível;
- diretório sem permissão;
- retenção não conseguiu remover arquivo antigo;
- serialização contém valor inválido;
- disco cheio.

O sistema falha fechado e não publica ZIP parcial. Resolva o problema sem desativar o scanner.

## 21. Localizar os diagnósticos

Perfil padrão:

```text
%LOCALAPPDATA%\TradingLab\profiles\default\core\reports\diagnostics
```

Compartilhe apenas o ZIP gerado pelo botão. Não compartilhe `state.db`, `broker_credentials` ou a
pasta inteira do perfil.

## 22. Token exposto

1. desligue o bot;
2. revogue o token na Deriv;
3. crie um token novo;
4. conecte novamente e substitua a credencial salva;
5. remova screenshots/mensagens públicas quando possível;
6. preserve apenas evidência redigida do incidente.

## 23. Queda total ou término forçado

Abra novamente uma única vez. O startup verifica banco, restaura reservas e reconcilia candidatos.
Se o app não abrir depois disso, não apague o perfil. Faça uma cópia consistente apenas com o
aplicativo fechado e investigue o diagnóstico/erro.
