# Desktop Debug Session v1.9.11

Data/hora: 2026-08-28 BRT.

## Objetivo

Abrir o aplicativo desktop compilado, navegar pela aba Deriv, testar botões, trocar estratégias e
validar por evidência local se o bot volta a operar depois de reset, pausa, troca de estratégia e
rearme.

Nenhuma conta Real foi usada. Os testes financeiros executados foram exclusivamente em Demo.

## Testes manuais executados no desktop

- Aplicativo aberto pelo EXE portátil.
- Aba Deriv selecionada.
- Cards de estratégia inspecionados:
  - Tail Probability Edge;
  - Selective Differs Edge;
  - Parity Regime Edge;
  - Sessão Differs.
- Abas internas inspecionadas:
  - Resumen;
  - Parámetros y riesgo;
  - Mercado en vivo;
  - Operaciones.
- Botão de reset de resultados acionado pela UI, com confirmação positiva.
- Botão de ligar/desligar bot acionado com clique físico.
- Bot desligado novamente após o teste.

## Problemas encontrados

### 1. Ordem rejeitada podia travar o bot como se houvesse ordem em andamento

Foi observado um caso em que uma ordem Demo foi marcada como `REJECTED` no banco, sem exposição
ativa, mas o auto trader continuava tratando a ordem como em voo na memória. O efeito prático era
o bot ficar aguardando "operação em andamento" e não abrir novas operações.

Correção aplicada:

- após submissão financeira, o auto trader recarrega a projeção persistida;
- a ordem só é removida do cache em memória quando o banco comprova estado terminal;
- se o banco não comprovar estado terminal, o comportamento permanece fail-closed.

Teste de regressão adicionado:

- `test_synchronous_rejected_submit_does_not_leave_inflight_cache`.

### 2. Reset de sessão Demo podia não executar depois da confirmação

O diálogo de confirmação do reset comparava o botão escolhido por identidade de enum. Em algumas
execuções do PySide, isso podia retornar sem chamar o Core mesmo após o operador clicar "Sim".

Correção aplicada:

- comparação alterada para igualdade de valor.

### 3. Reset podia ser recusado quando o Core e o runtime divergiam sobre Safe Stop

O reset dependia apenas do estado interno do serviço de ciclo de vida. Se o runtime já estivesse em
Safe Stop, mas o serviço ainda estivesse com flag antiga, o reset era recusado.

Correção aplicada:

- o reset Demo agora aceita Safe Stop vindo do serviço ou do runtime;
- após reset aceito, o auto trader recarrega caches e o serviço volta explicitamente para Safe Stop.

Teste de regressão adicionado:

- `test_demo_result_reset_accepts_runtime_safe_stop_if_service_flag_is_stale`.

### 4. Botão inferior podia ficar parcialmente atrás da barra do Windows

Em tela baixa, a janela abria com altura maior que a área útil, deixando o botão de ligar/desligar
próximo demais da borda inferior e difícil de clicar.

Correção aplicada:

- a janela agora ajusta automaticamente sua geometria à área útil do monitor no startup.

### 5. Launcher interno podia ficar vivo depois do fechamento seguro

Após o "Cerrar Seguro", UI, Core e workers fechavam, mas o processo launcher interno permanecia vivo.
O supervisor já chegava a `STOPPED`, porém o loop principal não encerrava nesse estado.

Correção aplicada:

- o launcher agora finaliza com sucesso quando o supervisor reporta `STOPPED`;
- teste de regressão adicionado para esse estado.
- smoke final confirmou que todos os processos encerraram sem intervenção após o fechamento seguro.

### 6. Alteração de parâmetros com bot ligado podia trocar regra em runtime ativo

Para evitar que o operador mude risco/parâmetros enquanto o motor está armado, a UI agora aciona
Safe Stop antes de aplicar qualquer configuração de risco quando o bot estiver ligado.

Correção aplicada:

- aplicação de parâmetros de risco desarma o bot primeiro;
- teste de regressão adicionado na camada de janela PySide.

## Resultado do teste Demo após correções

Após reset da sessão:

- reservas ativas: `0`;
- ordens Deriv não terminais: `0`;
- Martingale step: `0`;
- símbolo pinado: nenhum;
- perdas consecutivas: `0`.

Depois de religar o bot em Demo, o sistema abriu e liquidou operações. Ao final da janela de teste:

- resultado diário da sessão: `+USD 0.99`;
- reservas ativas: `0`;
- ordens Deriv não terminais: `0`;
- bot desligado novamente por Safe Stop.

## Validação automatizada

- `pytest`: 836 passed, 4 skipped.
- `ruff check`: aprovado.
- `ruff format --check`: aprovado.
- `mypy`: aprovado.
- `compileall`: aprovado.
- `git diff --check`: aprovado.
- Testes focados de launcher/shutdown após a correção final: 23 passed.
- Smoke final do EXE: startup completo e fechamento seguro sem processo órfão.
- Teste de UI garante Safe Stop antes de aplicar parâmetros com bot ligado.

## Artefato gerado

- `dist/TradingLab/TradingLab.exe`: distribuição onedir verificada pelo pipeline canônico.
- `dist/TradingLab.payload.zip`: payload portátil.
- `dist/TradingLab-Desktop-v1.9.11-DESKTOP-DEBUG-FIX.exe`: EXE portátil final.

SHA-256 do EXE portátil:

`98521BFF381678C41B2505DB1874E6522CC993C5861EE63462777F4C670C8026`
