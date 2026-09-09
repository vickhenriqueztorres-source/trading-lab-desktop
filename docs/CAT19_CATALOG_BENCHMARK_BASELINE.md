# CAT-19 — Baseline local do catálogo incremental

Data: 2026-09-06. Esta é uma medição de processo Python local, não uma prova do EXE nem de
disponibilidade externa. Não houve broker, rede, credencial, ordem ou ação financeira.

## Método

O harness `apps.core.catalog_benchmark` usa as identidades exatas do `IQOptionSeriesHub` e o
`IndicatorCache` incremental. Ele gera apenas candles locais fechados, mede o trecho
fechamento→indicador com `perf_counter_ns`, CPU do processo, working set Windows e os nós ativos.
O limite preliminar é p95 local <= 100 ms, p99 <= 500 ms e RSS <= 512 MiB. Esses limites são
orçamentos de admissão, não promessa de desempenho em qualquer máquina.

Máquina medida: Windows 10 19045, 4 CPUs lógicas, 8.070 MiB de RAM, Python 3.12.14, 64 épocas.

| Cenário | Receitas / nós / séries | Reuso | p95 / p99 ms | RSS pico MiB | Resultado |
|---|---:|---:|---:|---:|---|
| 10 alto reuso | 10 / 1 / 1 | 10,0x | 1,6091 / 2,2452 | 33,1055 | Admitido |
| 30 alto reuso | 30 / 3 / 3 | 10,0x | 12,0998 / 12,4824 | 36,6016 | Admitido |
| 50 baixo reuso | 50 / 50 / 10 | 1,0x | 116,7712 / 148,0261 | 41,5391 | Não admitido: p95 |
| 100 vários TFs | 100 / 100 / 24 | 1,0x | 331,8323 / 352,5763 | 47,0195 | Não admitido: p95 |

O benchmark registrou `network_calls=0` e `financial_actions=0` em todos os cenários. A métrica
`synthetic_series_updates` representa atualizações locais de série; não é contagem de requisições ao
broker.

## Decisão provisória

Nesta máquina, o microbenchmark admite o cenário de 30 referências a três nós RSI compartilhados.
Isso não estabelece capacidade para 30 estratégias completas: combinação de regime/trigger/confirm,
arbitragem, UI e pipeline financeiro não foram medidos por esse harness. Os cenários de 50 e 100
referências sem reuso excederam o orçamento. A capacidade comercial permanece por qualificar no
runtime completo, sem aumentar timeout.

## O que falta para concluir CAT-19

- Integrar os ensaios de falha ao lifecycle/trader completo; a matriz de componentes abaixo
  cobre invalidação, referências e deduplicação, sem comprovar recuperação automática do aplicativo.
- Carga contínua de 2 h com o mesmo grafo vivo. O replay acelerado de 24 h e os lotes curtos
  abaixo já foram executados, mas não comprovam disponibilidade externa nem ausência de vazamento
  de um cache mantido vivo por duas horas.
- Portátil/installer e smoke de UI visível no `TradingLab.exe` com perfil isolado, verificando
  restart, responsividade da UI e ausência de processo órfão.
- Benchmark de estratégias completas, comparação referência/shadow/incremental e hardware
  adicional antes de elevar o limite de admissão. O microbenchmark no EXE consta abaixo.

## Validação onedir do EXE — 2026-09-07

O bloqueio transitório da árvore de processos foi corrigido antes desta validação. A suíte Desktop
completa terminou em **1.295 passed, 4 skipped, 0 failed**; em particular,
`test_launcher_process_tree` passou 9/9.

O pipeline canônico PyInstaller 6.22.2 gerou o onedir isolado em
`artifacts/cat19-exe-20260907/TradingLab/`. O health check de integridade retornou `0`, o smoke
headless com perfil novo, worker `simulated` e auto-shutdown retornou `0`, e não permaneceu nenhum
processo `TradingLab`/Python após o encerramento. O SHA-256 do executável é:

```text
1C8ED9A84195B158F90918337345BFA5E8358EC77789847C046D64259F63E3EB
```

O mesmo comando de benchmark local foi executado pelo EXE congelado (`-m
apps.core.catalog_benchmark_cli --epochs 64`). Não iniciou UI, worker ou broker e registrou
`network_calls=0` e `financial_actions=0`.

| Cenário | p95 / p99 ms no EXE | RSS pico MiB | Resultado |
|---|---:|---:|---|
| 10 alto reuso | 6,9419 / 7,6042 | 44,9102 | Admitido |
| 30 alto reuso | 13,5714 / 13,9398 | 48,5508 | Admitido |
| 50 baixo reuso | 103,6278 / 129,2951 | 53,6289 | Não admitido: p95 |
| 100 vários TFs | 701,3166 / 881,3460 | 58,7227 | Não admitido: p95 e p99 |

O processo do benchmark retornou `1` somente porque a política de admissão recusa os cenários de
50 e 100 receitas; o JSON de resultado foi produzido integralmente e os cenários admitidos foram
10 e 30. Isso é o comportamento esperado do gate, não uma falha do EXE.

## Replay e falhas locais no EXE — 2026-09-07

Adicionados `apps.core.catalog_soak` e `apps.core.catalog_soak_cli`. O comando padrão executa
1.440 fechamentos M1 sintéticos, três nós RSI compartilhados por 30 referências, uma matriz de
componentes e um lote de benchmark. `--wall-seconds` repete lotes pelo tempo solicitado; cada lote
recria o cache. Não é ainda um ensaio do mesmo cache/SeriesHub continuamente residente.

| Medida | Python local | EXE congelado |
|---|---:|---:|
| Minutos simulados | 1.440 | 1.440 |
| Saídas locais / identidades distintas | 4.320 / 4.320 | 4.320 / 4.320 |
| Cálculos, incluindo aquecimento | 4.380 | 4.380 |
| Shadow final: matches / mismatches | 3 / 0 | 3 / 0 |
| Tempo do replay | 10,5393 s | 16,1406 s |
| CPU do processo durante replay (100% = um núcleo) | 93,1041% | 86,6409% |
| Duração medida de lotes repetidos | 30,0407 s | 10,2392 s |
| Lotes executados / recusados | 111 / 0 | 38 / 0 |
| Working set pico durante os lotes | 91,6172 MiB | 66,8125 MiB |

As 4.320 saídas são outputs de indicadores sobre dados sintéticos; não são operações nem
estimativa de frequência/resultado. A comparação shadow ocorre no final das três séries. Não
comprova paridade em cada época nem exactly-once financeiro. A identidade de saída do harness
inclui valor/direção; esta contagem isolada não substitui o dedupe do trader.

Matriz de componentes, aprovada em Python e EXE:

- Reconnect: invalidação explícita de três nós antigos; três saídas antigas INVALID, três novas
  READY e três nós restantes após release. Não simula falha de rede nem callback concorrente.
- Gap: retirada de uma vela produz SERIES_GAP; novo nó com histórico completo chega a READY.
  Não injeta suspensão do Windows nem avanço do relógio monotônico do lifecycle.
- Swap: aquisição de nós novos e release das referências antigas deixa um nó novo e zero
  referências antigas. Apesar do nome `atomic_catalog_swap_retirement` no JSON, esse ensaio é
  sequencial e não prova atomicidade do `DynamicManifestCatalog` em concorrência.
- Mercado lento: 30 notificações repetidas acrescentam zero cálculos; novo fechamento acrescenta
  três. Não mede timeout de fetch nem readiness de mercado stale no trader.
- Fila: cinco pedidos, dois aceitos, três recusas contadas por `queue_full` no scheduler real.
- Bootstrap: cinco velas mantêm WARMING_UP; invalidação explícita deixa zero séries e zero nós.
  Cancelamento de task/fetch em voo durante shutdown permanece fora desse ensaio.

Artefato canônico: `artifacts/c19/TradingLab/TradingLab.exe`, SHA-256:

```text
2FFAF200ED3A324798051439E2C35C7F80EB6F0CE0BAC896AC576ED347648188
```

Scanner do build: zero segredos; manifesto de integridade: 441 arquivos; health check: exit 0.
Smoke headless com perfil temporário novo, `--workers simulated --auto-shutdown-after 0.5`:
exit 0, stderr vazio e nenhum processo TradingLab residual. O relatório JSON foi movido para
`reports/cat19/catalog-soak-exe-20260907.json` e a integridade conferida novamente depois disso.
O relatório Python está em `reports/cat19/catalog-soak-20260907.json` (relatórios locais ignorados
pelo Git). Esta distribuição onedir requer a pasta inteira, incluindo `_internal`.

O primeiro destino de build, mais longo, falhou em COLLECT ao copiar um `.pyc` em caminho Windows
extenso. O mesmo pipeline concluiu no destino curto `artifacts/c19`; a limitação de caminhos e a
inclusão de `__pycache__` no empacotamento ainda precisam de correção própria. A primeira invocação
do soak congelado também foi recusada pelo argparse por falta de aspas no caminho de saída;
foi repetida com nome relativo e produziu o resultado acima.

Validação da implementação do harness: 30 testes focados aprovados (benchmark/soak/SeriesHub/cache
e replays AUTO), Ruff e formatação dos cinco arquivos aprovados, mypy dos três módulos aprovado,
compileall aprovado. Houve aviso de limpeza de temporários do pytest (WinError 5 no symlink
`pytest-current`) após os testes, sem falha nos casos. A suíte completa anterior de 1.295 testes
precede o novo harness; não foi repetida nesta rodada. CAT-19 continua **parcial** pelos itens
pendentes acima. Nenhuma conta, conexão de corretora ou operação financeira foi utilizada.

Verificação de fechamento desta rodada: restart do mesmo perfil temporário no EXE retornou 0,
com stderr vazio; mypy global aprovou 310 arquivos em `apps packages`; Ruff/format focados
reconfirmados e diff-check com `core.whitespace=cr-at-eol` aprovado (avisos LF/CRLF do Git).
