# Segurança de conexão IQ Option

## Objetivo

Este controle reduz bloqueios acidentais causados por tempestades de login, reconexões sem limite e
consultas excessivas. Ele não garante que a IQ Option manterá uma sessão disponível: o protocolo é
comunitário e pode mudar sem aviso.

Nenhum mecanismo de evasão é usado. O aplicativo não troca IP, não falsifica navegador, não resolve
CAPTCHA, não desativa TLS e não alterna contas para contornar limites.

## Fluxo de sessão

```text
primeira conexão
  → admissão persistente do Core
  → login HTTP
  → SSID somente em memória do worker
  → WebSocket autenticado
  → perfil + saldo confirmados

queda do WebSocket
  → um novo WebSocket no mesmo worker
  → reutilização do SSID em memória
  → perfil + saldo confirmados novamente
  → nenhum novo login HTTP
```

Somente uma rejeição explícita do SSID invalida a sessão. Timeout ou falha de rede não disparam um
novo login HTTP. O envio WebSocket continua serializado e a validação TLS permanece ativa.

## Limites internos

| Controle | Valor |
|---|---:|
| Reconexões WebSocket por worker em 15 minutos | 5 |
| Inícios de sessão externa por profile em 15 minutos | 3 |
| Quarentena preventiva | 15 minutos |
| Tentativas de recovery automático | 5 |
| Leituras de mercado por minuto | 60 |
| Reserva operacional adicional | 30 mensagens/minuto |

Os valores de mensagens são tetos internos conservadores, não limites oficiais publicados pela
corretora. O contador persistente é por profile, mais restritivo que por conta, e não contém e-mail,
senha, SSID, token ou identificador de conta.

O arquivo `core/iqoption-connection-safety.json` é escrito atomicamente. Reiniciar o EXE não apaga o
histórico nem a quarentena. Se esse estado estiver corrompido ou não puder ser salvo, a conexão IQ
Option falha fechada com `IQOPTION_CONNECTION_SAFETY_STATE_INVALID`.

## Respostas e recuperação

- `401/403`, 2FA e `429`: interrompem a sequência e abrem quarentena imediatamente;
- falha transitória: permanece dentro do orçamento deslizante;
- queda WebSocket: tenta somente o SSID existente, no máximo cinco vezes;
- limite atingido: `IQOPTION_CONNECTION_QUARANTINED` ou
  `IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED`;
- ordem ambígua: continua seguindo `UNKNOWN → reconciliação`, sem reenvio financeiro automático;
- ativo suspenso pelo broker: somente esse símbolo entra em cooldown de cinco minutos; no modo
  automático, o radar continua no próximo ativo e nunca reenvia o mesmo sinal rejeitado;
- toda reconexão permanece com o bot desarmado;
- conta Real permanece somente leitura.

## Operação

Ao receber quarentena, o operador deve aguardar o cooldown e investigar conectividade, 2FA ou
credenciais. Reiniciar repetidamente o aplicativo não é um procedimento de recuperação. O botão de
diagnóstico pode ser usado sem expor a credencial; os eventos registram apenas razão, contagem e
tempo restante.

## Cobertura automatizada

Os testes comprovam:

- reutilização do SSID sem segundo login HTTP;
- falha transitória não inicia novo login;
- rejeição explícita permite somente um login novo no fluxo manual;
- 100 quedas simuladas não ultrapassam cinco reconexões externas;
- limite e cooldown sobrevivem à recriação do controlador;
- respostas de autenticação/rate limit bloqueiam imediatamente;
- o quarto início externo é barrado antes de criar o worker;
- consultas de mercado param antes de ultrapassar o teto;
- recovery de startup termina depois de cinco tentativas;
- nenhuma alteração foi feita no conector Deriv.

Um soak externo de 72 horas não é substituído por simulação e deve ser executado separadamente em
Practice, com observação operacional. Cenário não executado nunca deve ser reportado como aprovado.
