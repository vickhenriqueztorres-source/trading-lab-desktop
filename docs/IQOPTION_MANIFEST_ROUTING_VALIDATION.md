# Causa 2 — roteamento IQ Option pelo manifesto

Baseline Desktop: v1.9.11. Data: 2026-09-03.

## Responsabilidades

| Origem | Autoridade |
|---|---|
| Manifesto verificado pelo Core | ativo exato, timeframe, horários, parâmetros, warmup e status |
| Operador | SINGLE/AUTO, chave ativa, stake e limites de risco |
| Bot local | resolução, histórico por par ativo/TF, avaliação e arbitragem |
| Pipeline financeiro existente | Health Gate, risco, persistência, despacho e reconciliação |

O Hub não envia sinais. Nenhum import do Strategy Lab foi adicionado ao Desktop.
O prazo do contrato financeiro continua inalterado; timeframe das velas não muda a duração
da ordem implicitamente. Conta Real continua bloqueada pelo conector e pelo motor Practice.

## Fatos corrigidos antes da implementação

| Alegação original | Evidência encontrada | Correção desta etapa |
|---|---|---|
| Seleção ignora timeframe | Já comparava timeframe com o hint do operador | Resolver deriva TF da entrada |
| Receita M5 executa em M1 | Era descartada com configuração M1 | Receita M5 passa a receber M5 |
| AUTO aplica receita a qualquer ativo | Filtrava símbolo-base, equiparando spot e OTC | Igualdade exata, ASSET_MISMATCH |
| Horário retorna None silencioso | EvalResult já expunha OUTSIDE_HOURS | Preservado; resolver também filtra e informa próxima abertura |
| RSI executa inclusive em Real | Ramo RSI existia, conector já bloqueava Real | Sem fallback; receita local demo_only, SINGLE/Practice explícito |

## Implementação

- `resolve_candidates` é puro: não lê relógio, banco, rede ou arquivo. Recebe horário UTC
  com timezone e tipo de conta; tipo desconhecido falha fechado. AUTO admite todas as
  entradas aprovadas compatíveis e observação apenas em Demo/Practice.
- RSI local tem ID histórico `iqoption-rsi-demo`, nome “RSI 30/70 (não validado · apenas Demo)”
  e status `demo_only`. Não é inserido no manifesto assinado, não ganha validação fictícia
  e não participa de AUTO. Não encontrar a chave escolhida nunca seleciona a primeira receita.
- Cada par `(símbolo, timeframe)` consulta no máximo uma janela por intervalo UTC de vela.
  Candidatos do mesmo par compartilham a maior necessidade de warmup. Count continua
  `min(120, warmup + 3)`. O rate limiter continua monotônico e fail-closed.
- Cache distingue timeframes e gerações do worker. Expiração de uma janela M1 não elimina
  indevidamente a janela M5. Atualização do catálogo invalida histórico e warmup projetados.
- Arbitragem ordena a margem Decimal `wilson_lower - p_min_at_validation` descendente,
  depois chave ascendente. Sinais opostos no mesmo ativo/TF cancelam conforme AG-INV-015.
  Nenhuma soma de stake; consumo do epoch antes do despacho e uma ordem em voo preservados.
- Telemetria `iqoption_decision` é deduplicada por símbolo/chave/epoch, com memória limitada
  a 4096 identidades; inclui timeframe, motivo e próxima abertura quando aplicável.
- UI fixa ativo/TF da receita em SINGLE; AUTO usa TF por candidato. Radar tem detalhes
  de candidatos/rejeições no tooltip e motivo explícito para ausência de receita.
- `active_strategy_key` é o nome canônico no JSON persistido e uma propriedade do modelo.
  O argumento Python e o wire histórico `strategy_id` permanecem aliases compatíveis;
  leitores aceitam ambos, rejeitando conflito. O hint `timeframe_seconds` é depreciado,
  nunca manda no resolvedor. O Core normaliza configurações SINGLE pelo catálogo.

## Decisões de escopo

A opção E foi adiada: as famílias ainda verificam o horário da última vela e alguns
primitivos são intencionalmente dependentes da sessão. Remover isso requer revisar o
contrato de paridade Lab/bot; nenhuma fórmula, parâmetro ou hash numérico foi alterado.
Os gates de payout e o fluxo financeiro não foram reimplementados nesta Causa 2.
O roteamento e o cache não introduzem leituras SQL; consultas financeiras já existentes
no guard de ordem em voo e no despacho não foram removidas nem substituídas por suposição.

## Replay sintético de 24 horas em AUTO

Teste: `tests/replay/test_iqoption_candidate_routing_24h.py`. São 1440 epochs de um minuto,
16 símbolos por epoch, 23040 resoluções. Início: 2026-09-03 00:00 UTC.
Receitas sintéticas F5: EURUSD-OTC M1 e M5, 08–16 UTC; EURUSD M1, 12–20 UTC.
É replay de **roteamento**, sem preços externos, sinais financeiros, ordens ou lucro simulado.

| Símbolo | NO_CANDIDATE | ASSET_MISMATCH | OUTSIDE_HOURS |
|---|---:|---:|---:|
| EURUSD-OTC | 960 | 1440 | 960 |
| GBPUSD-OTC | 1440 | 1440 | 0 |
| USDJPY-OTC | 1440 | 1440 | 0 |
| EURJPY-OTC | 1440 | 1440 | 0 |
| GBPJPY-OTC | 1440 | 1440 | 0 |
| AUDCAD-OTC | 1440 | 1440 | 0 |
| NZDUSD-OTC | 1440 | 1440 | 0 |
| USDCHF-OTC | 1440 | 1440 | 0 |
| EURUSD | 960 | 1440 | 960 |
| GBPUSD | 1440 | 1440 | 0 |
| USDJPY | 1440 | 1440 | 0 |
| EURJPY | 1440 | 1440 | 0 |
| USDCHF | 1440 | 1440 | 0 |
| AUDCAD | 1440 | 1440 | 0 |
| NZDUSD | 1440 | 1440 | 0 |
| AUDUSD | 1440 | 1440 | 0 |

Contagens são por presença do motivo em cada epoch, não mutuamente exclusivas:
um símbolo pode ter uma receita admitida e rejeitar receitas de outros ativos no mesmo minuto.
EURUSD e EURUSD-OTC têm 480 epochs com candidatos cada; os outros têm zero.

## Validação e limitações

- Regressão ampliada inicial: 91 testes aprovados, incluindo UI offscreen, warmup, catálogo,
  protocolo do radar, isolamento e hash de paridade dos primitivos.
- `mypy apps packages`: aprovado, 303 arquivos. Corrigidos nomes de variáveis sobrepostos
  no callback de seleção da UI, sem mudar gestão financeira.
- Suíte global e Ruff global não devem ser tratados como aprovados sem o fechamento
  registrado no WORKLOG. Não houve teste autenticado, ordem externa, build/EXE, deploy ou push.

Fechamento: 91 testes da regressão ampliada aprovados novamente; 20 do complemento final
do resolvedor/UI aprovados (há sobreposição). `mypy apps packages` repetido: 303 arquivos
sem erros. Ruff dos arquivos da etapa, compileall, scanner de segredos e diff-check aprovados.

A tentativa global `pytest -q --tb=short --maxfail=10` terminou com **146 passed, 1 skipped,
7 failed, 3 errors** (468,85 s). Os problemas foram readiness do ator de crash, handshakes
de subprocessos Deriv/simulado e um teste de UI esperando cinco abas em vez das seis atuais.
Não foi executada a parte restante depois do limite de dez problemas. Nenhum deadline foi
aumentado. Ruff/format globais ainda têm pendências em arquivos preexistentes fora da etapa.
Portanto, **a suíte inteira não está verde e não há homologação de release/EXE**.
