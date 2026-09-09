# Implementação — IQ Option sem operações e com desconexões

Data de fechamento local: 08/09/2026 BRT. Classificação: **LOCAL_FIX_VALIDATED**.

Este documento registra a execução do
[plano de resolução](IQ_RESOLUTION_PLAN_20260908.md). A validação foi local, com workers
simulados e artefato compilado. Nenhuma conta Real foi usada e nenhuma ordem externa foi enviada.

## Resultado por problema

| ID | Correção aplicada | Evidência local |
| --- | --- | --- |
| R1 | A trilha de decisão agora separa candidatura, avaliação, arbitragem, admissão, risco, payout e submissão, com `decision_id` estável e correlação financeira. | Sinal aceito seguido por bloqueio de payout mantém os dois eventos e cria zero intenção/reserva/outbox; bot desarmado e orçamento esgotado também terminam com razão explícita. |
| R2 | Safe Stop, rearme e recovery usam escopo de broker/conta. Recovery Deriv não revoga IQ e recovery IQ não revoga Deriv. | Matriz de autorização por broker, parada global e conclusão de reconciliação aprovada. |
| R3 | `IQOPTION` do wire é convertido para `IQ_OPTION` na fronteira de health; limpeza ocorre somente no escopo alvo. | Testes de alias, ownership, generation fencing e revogação entre persistência e despacho aprovados. |
| R4 | PING comprova somente IPC; não faz login/reconnect. Lifecycle é o único dono do recovery, com supervisor/cliente novos, limite de tentativas, backoff, fence por geração e rearme manual obrigatório. | Heartbeat tolera duas falhas transitórias, notifica uma vez no limiar, recovery converge ou termina de forma limitada; PING produz PONG e zero login/buy. |
| R5 | Mercado possui teto de 60 mensagens/minuto dentro do teto total local de 90; 30 ficam reservadas para operação. Saldo/relógio usam TTL de 10 s e consomem a faixa operacional; falta de orçamento bloqueia antes da intenção financeira. | Testes de janela deslizante, reserva operacional e saturação aprovados; no bloqueio há zero intenção. |
| R6 | Falhas HTTP do manifesto agora preservam somente causas estáveis e sanitizadas; o refresh distribui as tentativas pela janela horária. | Origem respondeu 503 com `HUB_MANIFEST_LAST_GOOD_UNAVAILABLE`; espelho respondeu objeto ausente. Nenhum manifesto foi fabricado ou publicado. |
| R7 | A UI projeta separadamente conexão, sincronização de saldo/relógio, armamento e autorização efetiva. | Worker READY sem saldo aparece conectado, porém com `IQOPTION_BALANCE_SYNC_REQUIRED`; armado com gate fechado mostra “entradas bloqueadas”, não “bot ativo”. |

## Garantias preservadas

- persistência ocorre antes do possível envio financeiro;
- uma revogação entre claim e dispatch impede a chamada ao worker;
- submissão ambígua continua `UNKNOWN` e não recebe retry financeiro;
- recovery e reconciliação não rearmam trading;
- ordem existente continua sendo acompanhada quando novas entradas estão pausadas;
- nenhum payload irrestrito de autenticação, cookie, token, e-mail ou segredo entra na telemetria;
- scanner integral de segredos encontrou zero ocorrências.

## Validação automatizada

- pytest: **1.318 passed, 4 skipped, 0 failed**;
- replay IQ de 24 h: 48 aceitações simuladas, 24 rejeições simuladas, 1.368 épocas sem submissão e zero correlações duplicadas;
- Ruff check: aprovado;
- Ruff format check: aprovado em 515 arquivos;
- mypy: aprovado em 310 fontes de `apps` e `packages`;
- compileall: aprovado;
- scanner integral: aprovado, zero segredos;
- `git diff --check`: aprovado no fechamento.

A primeira execução integral encontrou uma condição intermitente no teste de crash: o processo
era aguardado, mas seus pipes não eram colhidos antes da nova tentativa de byte-lock do Windows.
O helper passou a executar `communicate()` após o kill. Antes da correção houve uma reprodução
em três execuções; depois, o cenário passou dez vezes consecutivas e voltou a passar na suíte
integral. Nenhum timeout foi aumentado.

O pytest ainda imprime, depois do resumo de sucesso, o aviso conhecido de permissão ao limpar o
atalho temporário `pytest-current` no Windows. Isso não altera o exit code nem os resultados.

## Artefato Windows

- onedir: `dist/iq-resolution-release-20260908/TradingLab/TradingLab.exe`;
- payload: `dist/iq-resolution-release-20260908/TradingLab.payload.zip`;
- portátil: `dist/iq-resolution-release-20260908/TradingLab-Desktop-v1.9.11-IQ-RESOLUTION.exe`;
- SHA-256 onedir: `55987330D8D19B5261B7C8601C3BC768F2335A6402883FD0167B54F4982B765D`;
- SHA-256 payload: `175F402DBE86620F68E8B15370D464ADC5E673BA9418FFB7E5570C5F539EE1CE`;
- SHA-256 portátil: `DB60D2F57A189E960B8293E147755D8204EE96BE524DA7C0434BD67FA94A5A75`.

O pipeline canônico do onedir passou integridade, manifesto de 441 arquivos, scanner e health
check. O payload tem 855 entradas e contém `TradingLab/TradingLab.exe`. O portátil contém
exatamente o recurso `TradingLab.payload.zip`, reporta ProductVersion 1.9.11 e passou smoke
headless com perfil novo e worker simulado: exit 0, stdout/stderr vazios, `quick_check=ok`, zero
intenções/reservas/outbox/ordens e zero processos residuais.

A primeira compilação no caminho longo do workspace falhou no `COLLECT` ao copiar um `.pyc` por
limite de caminho do Windows. O mesmo pipeline foi repetido em `C:\tlb\iqr-final`, passou, e sua
distribuição foi copiada para o caminho de entrega acima. A primeira tentativa de smoke omitiu
aspas no argumento de perfil com espaços e retornou 2; a repetição com o argumento corretamente
delimitado passou. O instalador não foi gerado porque `ISCC.exe` não está instalado.

## Limites da validação

Não foi executado soak contínuo de duas horas nem ensaio externo IQ Practice nesta execução.
Também não existe manifesto elegível no Hub remoto observado; indisponibilidade explícita é o
comportamento correto até uma publicação legítima. Portanto, este fechamento não comprova
aceitação/settlement externo, disponibilidade contínua da API comunitária ou resultado financeiro.
Assertividade e lucro não decorrem destas correções operacionais.

Qualquer validação externa futura deve confirmar conta Practice, usar limites explícitos, nunca
ativar Real, observar uma ordem em voo e cruzar ID do broker com intenção, reserva, outbox, evento
e settlement persistidos. Até isso ocorrer, a classificação permanece `LOCAL_FIX_VALIDATED`.
