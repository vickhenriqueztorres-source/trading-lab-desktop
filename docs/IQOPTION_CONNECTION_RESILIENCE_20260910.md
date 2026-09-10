# Resiliência da conexão IQ Option — 2026-09-10

Este documento descreve o contrato implementado para eliminar o ciclo em que uma falha temporária
do relógio era tratada como queda total da sessão, provocava novos logins HTTP e terminava na
quarentena local de 15 minutos.

## Separação de estados

- A intenção do operador permanece em `ARMED` ou `ARMED_DEGRADED` durante falhas de transporte.
- A saúde do relógio é um bloqueio de entrada independente (`MD_CLOCK_UNTRUSTED`).
- Uma falha de relógio não substitui o worker, não encerra o WebSocket e não inicia login HTTP.
- A avaliação continua consultando a saúde. Uma amostra válida remove somente o bloqueio do
  relógio e permite a retomada automática.
- Parada manual e bloqueios de risco, estratégia e payout continuam autoritativos.

## Relógio e diagnóstico

O worker distingue ausência de amostra, amostra vencida, Pong expirado, salto do relógio do
Windows e WebSocket indisponível. Erros de relógio podem transportar somente os campos seguros:

- operação;
- duração;
- idade da amostra;
- tempo desde a última mensagem;
- geração da conexão.

Nenhuma credencial, cookie, SSID ou payload externo cru atravessa esse diagnóstico. Uma amostra
vencida solicita `timesync` no mesmo WebSocket, com espera limitada. Um Pong isolado ausente é
observável, mas não invalida uma amostra de broker que ainda esteja válida. Não existe fallback
para o relógio local.

## Timeouts e recuperação

- Leituras correlacionadas que expiram permanecem isoladas e não provocam login.
- A consulta global de payout continua single-flight e falha fechada. Timeout de payout não tem
  autoridade para substituir sessão nem iniciar login.
- Ordem com resultado desconhecido segue para reconciliação e nunca é reenviada automaticamente.
- Queda real usa primeiro o comando IPC de reconexão no mesmo worker. Se o worker morreu, o
  substituto recupera o SSID cifrado diretamente do cofre DPAPI CurrentUser.
- Login HTTP só é considerado quando não há sessão reutilizável ou quando a corretora rejeita
  explicitamente o SSID.

O orçamento WebSocket é independente da quarentena de login HTTP. Quando o limite WebSocket é
atingido, o worker informa o tempo restante; o Core espera de forma interrompível e acorda
automaticamente. O backoff progressivo recebe jitter de 10%. O histórico não é zerado por uma
conexão curta: ele expira naturalmente na janela de 15 minutos.

O Core nunca recebe o SSID. Todo start executa primeiro com política `deny` para login HTTP; apenas
ausência ou rejeição da sessão habilita a etapa seguinte, após admissão explícita `auto` ou
`manual`. Assim, reiniciar o EXE, reciclar o worker ou perder IPC custa zero logins quando a sessão
de 20 horas ainda é aceita pelo broker.

## Evidência automatizada

Os testes cobrem:

- 50 falhas consecutivas de relógio e retomada na amostra seguinte, sem `on_transport_up()`;
- amostra válida preservada após um Pong isolado;
- atualização `timesync` no mesmo WebSocket e um único login HTTP;
- diagnóstico estruturado com allowlist;
- dez quedas WebSocket, esgotamento do orçamento e retomada automática após a janela;
- bypass da quarentena HTTP durante recuperação com SSID;
- parada manual durante recovery sem reativação posterior;
- replay de 24 horas sem correlação duplicada.

A suíte local final concluiu com 1.393 testes aprovados e 4 pulados por dependência de plataforma
ou ambiente externo. O smoke do executável usa perfil isolado e não acessa a conta do operador.
A observação de duas horas contra a IQ Option Practice continua sendo evidência externa separada:
ela exige a sessão do operador e não é substituída pelos testes simulados.
