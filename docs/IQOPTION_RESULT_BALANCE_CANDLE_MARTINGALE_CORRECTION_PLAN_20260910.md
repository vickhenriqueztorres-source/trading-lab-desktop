# Plano de correção — resultado, saldo e martingale por candle da IQ Option

## Evidência do incidente

A inspeção somente leitura do perfil local encontrou 115 ordens IQ Option em `SETTLED`: 114 com
P&L zero e apenas uma com P&L diferente de zero. As 13 liquidações zero mais recentes foram
confirmadas entre 3,48 e 7,41 segundos após a criação da ordem, embora o produto seja M1. A ordem
`14250937747`, visível na captura, foi criada às `00:02:06.314641Z` e marcada `SETTLED` às
`00:02:10.002510Z`.

O código confirma três causas:

1. o reconciliador aceita campos `win/status` sem provar que o contrato expirou e pode interpretar
   um item ainda aberto como liquidação;
2. os parsers escolhem campos monetários diferentes sem considerar a rota e usam zero quando o
   campo está ausente; a UI classifica `P&L >= 0` como vitória;
3. `broker_balance()` devolve o snapshot recebido no login, mas cria um horário de observação novo a
   cada leitura. O Core lê esse cache a cada dez segundos, portanto o saldo antigo parece atual.

O martingale implementado também está ligado diretamente ao `result_minor` financeiro da IQ e usa
o horário de chegada da liquidação para escolher o minuto seguinte. Isso não atende à regra de
produto de decidir G1/G2 pelo candle fechado.

## Contrato corrigido

Três fatos passam a permanecer separados:

- **liquidação financeira:** vem de evidência final da IQ Option e atualiza ordem, reserva, P&L e
  histórico;
- **saldo da conta:** vem de snapshot/evento de saldo identificado e conserva o horário real em que
  o worker recebeu o dado;
- **resultado técnico:** vem do candle M1 fechado e validado do ativo exato e decide apenas a
  sequência G1/G2.

Resultado técnico nunca escreve P&L, nunca corrige saldo, nunca libera reserva e nunca converte uma
ordem ambígua em liquidada. A recuperação só pode ser enviada quando a exposição anterior já não
estiver aberta/ambígua e todos os gates do Core permitirem.

Para a regra por candle, CALL vence quando `close > open`; PUT vence quando `close < open`;
`close == open` é empate e encerra o ciclo. Entradas ficam restritas ao início do minuto para que a
operação M1 corresponda a um candle completo. O ciclo fixa previamente o fechamento-alvo, o ativo,
a direção, a estratégia e o nível. O G seguinte deve entrar na janela imediatamente posterior ao
fechamento; se o candle ou a liquidação financeira não ficarem disponíveis antes do fim da janela,
a recuperação é cancelada como `WINDOW_MISSED`, sem entrada atrasada.

## Implementação

### 1. Resultado financeiro

- normalizar separadamente `option-closed`, `get_betinfo` e `get-options`;
- exigir prova de finalidade (`game_state`/expiração/estado terminal) antes de produzir `SETTLED`;
- calcular P&L com os pares de campos documentados para cada rota, sem fallback silencioso para
  zero;
- tratar campo ausente, valor não finito e conflito como evidência indisponível;
- manter o event pump vivo e tornar falhas/overflow observáveis;
- exibir zero confirmado como empate/reembolso, nunca como vitória.

### 2. Saldo ao vivo

- solicitar `get-balances` correlacionado de forma limitada, além do snapshot de login;
- aceitar atualizações somente para o balance ID, modo, moeda e geração ativos;
- conservar `observed_at` da recepção real; reler cache não renova freshness;
- desacoplar observação de saldo do gate de relógio/estratégia;
- atualizar após eventos de ordem e manter polling bounded como fallback;
- projetar idade e estado `FRESH/STALE/UNAVAILABLE` até a UI.

### 3. Martingale por candle

- persistir no ciclo o fechamento M1-alvo, nível, ordem-pai e janela de entrada;
- buscar com prioridade o candle exato após o fechamento, recusando parcial, gap, série errada,
  cache anterior e candle ausente;
- gerar evidência técnica determinística/idempotente a partir de ordem, candle, OHLC e versão da
  regra;
- retirar a decisão de G1/G2 do callback de liquidação financeira;
- manter mesma estratégia, ativo e direção e revalidar payout, relógio, catálogo, risco e saldo;
- governar a recuperação pela janela própria de 20 segundos, sem deixar o cooldown financeiro
  ordinário empurrá-la para outro minuto; os demais bloqueios financeiros continuam fail-closed;
- cancelar em empate, vitória, último nível, desarme, risco bloqueado ou janela perdida.

### 4. Histórico já afetado

- não reescrever os 114 zeros por suposição;
- selecionar somente liquidações IQ zero cuja evidência foi `STATUS_QUERY` e é temporalmente
  impossível/inconsistente;
- consultar novamente cada contrato por ID após correção do parser;
- aplicar correção financeira somente com nova evidência terminal, em transação auditada, mantendo
  evidência antiga e nova; conflitos permanecem em revisão;
- enquanto não reconciliadas, apresentá-las como resultado questionável em vez de vitória.

## Provas de aceitação

- contrato ainda aberto nunca vira `SETTLED`, mesmo contendo `win="win"`;
- schemas finais de vitória, perda e empate calculam minor units exatos; ausência monetária falha
  fechada;
- saldo muda sem novo login, leitura repetida não altera sua idade e falha de relógio não congela o
  observador;
- CALL/PUT e empate usam o candle fechado exato; candle parcial/ausente/OTC divergente não decide;
- G0→G1→G2 sobrevive a restart sem ordem duplicada e não entra depois da janela;
- divergência entre resultado técnico e financeiro fica auditável e não mistura os dois valores;
- testes unitários, contrato, integração, replay/crash, lint, tipos, build e smoke local passam sem
  chamada financeira externa.

## Implementação concluída em 2026-09-11

- O normalizador financeiro agora é específico por origem (`betinfo`, `option-closed` e histórico),
  exige finalidade comprovada e falha fechado quando stake/retorno não estão presentes ou
  divergem.
- O observador de saldo roda independentemente do bot e do relógio, consulta a IQ a cada cinco
  segundos dentro do orçamento, conserva o timestamp de recepção e bloqueia novas entradas após
  15 segundos sem confirmação. A UI mostra atualizado/desatualizado e o horário observado.
- G1/G2 usa exclusivamente a vela M1 fechada exata, com evidência técnica determinística. O P&L da
  IQ não avança o ciclo. Entrada base após o segundo 24 e recuperação fora da janela de 20 segundos
  são canceladas.
- Liquidações IQ zero históricas vindas de `STATUS_QUERY` aparecem como resultado sob revisão. A
  migração 11 cria apenas estruturas aditivas de auditoria; nenhuma linha financeira antiga é
  reescrita automaticamente.
- Validação: Ruff e formatação em 540 arquivos, mypy em 321 módulos, compileall e 1.443 testes
  aprovados (4 externos/plataforma ignorados). O build onedir passou scanner de segredos,
  manifesto de 554 arquivos, verificação de integridade e health-check interno.
- Artefato portátil: `dist/iqfix/TradingLab-Desktop-v1.9.11-IQ-RESULT-BALANCE-CANDLE-MG-FIX.exe`,
  59.332.608 bytes, SHA-256
  `626F4AC2AA2E7775D6E68F7B2DC5B2410B7F22C267E512DDBE6A7355154E1A4F`.
