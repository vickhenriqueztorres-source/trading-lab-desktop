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
  → worker tenta SSID cifrado no cofre DPAPI CurrentUser
  → somente se ausente/rejeitado: admissão persistente do Core + login HTTP
  → SSID validado salvo com TTL de 20 horas
  → WebSocket autenticado
  → perfil + saldo confirmados

queda do WebSocket
  → um novo WebSocket no mesmo worker
  → reutilização do SSID em memória ou no cofre DPAPI
  → perfil + saldo confirmados novamente
  → nenhum novo login HTTP
```

Worker morto também não prova sessão morta: o substituto lê o SSID diretamente do cofre, sem expor
o valor ao Core, IPC, argv ou ambiente. Somente uma rejeição explícita do SSID invalida a sessão.
Timeout ou falha de rede não disparam novo login HTTP. O envio WebSocket continua serializado e a
validação TLS permanece ativa.

## Limites internos

| Controle | Valor |
|---|---:|
| Reconexões WebSocket por worker em 15 minutos | 5 |
| Logins HTTP automáticos por profile em 15 minutos | 3 |
| Login HTTP manual | 1 a cada 2 minutos |
| Quarentena preventiva | 15 minutos |
| Tentativas de recovery automático | 5 |
| Leituras de mercado por minuto | 60 |
| Reserva operacional adicional | 30 mensagens/minuto |

Os valores de mensagens são tetos internos conservadores, não limites oficiais publicados pela
corretora. O contador persistente é por profile, mais restritivo que por conta, e não contém e-mail,
senha, SSID, token ou identificador de conta.

O orçamento automático é persistido em `core/iqoption-connection-safety.json`; o orçamento manual
fica somente em memória. Uma tentativa manual admitida limpa uma quarentena automática antiga, mas
não remove o limitador. Reiniciar o EXE não apaga o
histórico nem a quarentena. Se esse estado estiver corrompido ou não puder ser salvo, a conexão IQ
Option falha fechada com `IQOPTION_CONNECTION_SAFETY_STATE_INVALID`.

## Respostas e recuperação

- `401/403`, 2FA e `429`: interrompem a sequência e abrem quarentena imediatamente;
- falha transitória: permanece dentro do orçamento deslizante;
- queda WebSocket: tenta somente o SSID existente, inclusive após substituir o worker;
- limite atingido: `IQOPTION_CONNECTION_QUARANTINED` ou
  `IQOPTION_WEBSOCKET_RECONNECT_LIMIT_REACHED`;
- ordem ambígua: continua seguindo `UNKNOWN → reconciliação`, sem reenvio financeiro automático;
- ativo suspenso pelo broker: somente esse símbolo entra em cooldown de cinco minutos; no modo
  automático, o radar continua no próximo ativo e nunca reenvia o mesmo sinal rejeitado;
- intenção armada permanece `ARMED_DEGRADED` e só volta a executar após saúde e reconciliação;
- conta Real permanece somente leitura.

## Operação

A quarentena automática é exibida inline, com contagem regressiva e botão `Reconectar agora` usando
o orçamento manual independente. Reiniciar repetidamente o aplicativo não é um procedimento de
recuperação. O botão de diagnóstico pode ser usado sem expor credencial ou SSID; os eventos
registram apenas razão, origem, contagem e tempo restante.

## Cobertura automatizada

Os testes comprovam:

- reutilização do SSID cifrado após restart sem segundo login HTTP;
- falha transitória não inicia novo login;
- rejeição explícita permite somente um login novo no fluxo manual;
- 100 quedas simuladas não ultrapassam cinco reconexões externas;
- limite e cooldown sobrevivem à recriação do controlador;
- respostas de autenticação/rate limit bloqueiam imediatamente;
- falha ao iniciar worker/cache não consome o orçamento HTTP;
- consultas de mercado param antes de ultrapassar o teto;
- recovery armado repete o último backoff indefinidamente;
- nenhuma alteração foi feita no conector Deriv.

Um soak externo de 72 horas não é substituído por simulação e deve ser executado separadamente em
Practice, com observação operacional. Cenário não executado nunca deve ser reportado como aprovado.
