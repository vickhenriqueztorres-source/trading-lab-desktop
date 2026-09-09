# Plano de evolução — catálogo amplo, execução incremental e Supabase
Data: 2026-09-05. Base inspecionada: commit `18d10bb` de `trading-lab-desktop`.
Estado: **CAT-00 executado; CAT-01 executado parcialmente em 2026-09-05, com inventário remoto
bloqueado por ausência de autenticação segura/staging confirmado; CAT-02 implementado localmente
em 2026-09-05, sem publicação remota; CAT-03 implementado localmente em 2026-09-05,
sem aplicação remota de migration; CAT-04 implementado localmente em 2026-09-05,
sem Supabase remoto/broker/build; CAT-05 implementado localmente em 2026-09-05,
com migration local não aplicada remotamente; CAT-06 implementado localmente em 2026-09-05,
sem Supabase remoto/broker/build; CAT-07 implementado localmente em 2026-09-05,
sem Supabase remoto/broker/build; CAT-08 implementado localmente em 2026-09-05,
sem Supabase remoto/broker/build; CAT-09 implementado localmente em 2026-09-05,
sem Supabase remoto/broker/build; CAT-10 iniciado localmente em 2026-09-05,
com flags seguras e RSI incremental explícito, sem Supabase remoto/broker/build;
CAT-11 implementado localmente em 2026-09-05, com replay executável de portfólio
no Strategy Lab, sem Supabase remoto/broker/build; CAT-12 implementado localmente em 2026-09-06;
CAT-13 aplicado e validado no Supabase staging em 2026-09-06; CAT-14 implementado localmente em
2026-09-06, com integração por fakes e endpoints reais alcançados sem manifesto corrente;
CAT-15 implementado e aplicado no Supabase staging em 2026-09-06; CAT-16..CAT-21 não executados.**

## 1. Decisão de arquitetura

O Strategy Lab pesquisa, valida e seleciona **receitas de estratégia**, não sinais.
O Hub distribui manifestos assinados/versionados e armazena dados e evidências.
O bot do cliente recebe receitas aprovadas, acompanha o mercado da sua própria conexão,
calcula sinais e executa pelo pipeline financeiro local existente.

Modelo proposto:

- Lab: coleta, datasets reproduzíveis, pesquisa, validação estatística e seleção de portfólio.
- Supabase: Postgres para controle e dados recentes; Storage privado para histórico compacto;
  Storage/CDN para manifestos públicos; Edge Functions para operações curtas.
- Bot: uma janela por série, indicadores incrementais compartilhados quando as entradas forem
  exatamente iguais, avaliação de estratégias no fechamento relevante e seleção local.
- Risco, ordens em andamento, reconciliação e monitoramento pertencem ao bot, não ao catalogador.
- Nada de pesquisa, otimização de parâmetros ou backtest pesado no computador do cliente.
- Nenhuma promessa de assertividade, lucro, ausência de perdas ou risco zero de regressão.

**Não exige uma VPS para cada cliente nem uma VPS para o compartilhamento de cálculos.**
O bot continua no computador do cliente. O Lab pode pesquisar e coletar no computador do operador.
Para coleta continuamente disponível, algum executor precisa permanecer ligado: computador
dedicado ou, opcionalmente, VPS/job gerenciado. Supabase não executa sozinho os processos Python
do Lab. Se o coletor ficar desligado, candles recuperáveis podem ser preenchidos depois; payout
que não foi observado não pode ser inventado retroativamente.

Mais estratégias não significam, por si, mais operações: a conexão, o número de séries,
os sinais simultâneos, os filtros e **uma ordem em voo por conta** limitam a frequência executável.
O objetivo é aumentar oportunidades complementares verificadas, não remover esses limites.

## 2. O que o código atual exige corrigir antes da expansão

Esta é uma inspeção estática/local; não é certificação externa ou uma nova execução da suíte inteira.
As quantidades abaixo correspondem à base inspecionada, não a uma garantia sobre commits futuros.

| Constatação | Evidência local | Consequência para o plano |
|---|---|---|
| O catálogo local tem 16 entradas, mas todas F1 e com o mesmo conjunto de parâmetros e estatísticas | `data/manifest.json`, `cache/manifest.json` | 16 ativos não comprovam 16 vantagens independentes. Auditar origem e contribuição marginal. |
| Há diferença de composição entre replay e execução | `strategy-lab/tools/strategy_lab/research/replay_simulator.py`; `apps/core/families/` | Paridade dos primitivos não basta. Validar a estratégia completa, incluindo limites de regime. |
| A avaliação do bot reinicia indicadores e percorre a janela novamente | `apps/core/families/base.py` | Criar caminho incremental versionado; não presumir equivalência com reinicializações curtas. |
| AUTO percorre um símbolo por ciclo e limita histórico solicitado a 120 velas | `apps/core/iqoption_auto_trader.py` | Compartilhar séries, dimensionar aquecimento e medir atraso; não multiplicar polling por estratégia. |
| Pesquisa Parquet usa o primeiro ativo na carga; há caminho padrão sintético sem opção explícita | `strategy-lab/tools/strategy_lab/cli.py` | Impedir atribuição de dados de um ativo a outro e aprovação de dados sintéticos como reais. |
| Holdout e relatórios têm caminhos permissivos/fixos | `research/runner.py`, `holdout.py`, `report.py` | Tornar evidência de aprovação calculada, persistida e rastreável. |
| Métodos de cliente remoto e uploader existem, mas não localizei sua instanciação em `apps/` | `apps/core/manifest_client.py`, `apps/core/outcomes_uploader.py` e busca pelos construtores | Verificar e ligar os componentes existentes, sem criar segundo cliente/segunda fila. |
| Arquivamento não está operacional | `strategy-lab/apps/hub/supabase/functions/archive/index.ts` retorna stub; migration 0004 | Não apagar velas contando com um arquivo que ainda não foi produzido e verificado. |
| Publicação atual escreve objetos antes de concluir o registro no banco | `functions/publish/index.ts` | Proteger concorrência e falhas parciais; Storage e Postgres não formam uma transação única. |
| A geração atual pode produzir muito mais candidatos do que o bot consegue aquecer | `research/grammar.py`, primitivos e limite de 120 | Usar capacidades do executor antes de pesquisar/publicar, sem reduzir janelas silenciosamente. |

Exemplo de inconsistência que requer auditoria, não correção silenciosa do manifesto:
o catálogo declara `p_hat=0.578`, `n=1000` e Wilson inferior `0.557`.
Aplicando a função Wilson do Lab a 578 vitórias em 1.000 observações, o resultado local é
`0.5471483340786791358059675999`. É necessário reconstruir contagem, método e origem.
Não é aceitável simplesmente trocar o número e assinar novamente mantendo a aprovação.

Também há divergência entre limites/composição das famílias: um regime ADX que o replay aceita
pode ser recusado pelo F1 do bot. Essas diferenças devem virar vetores públicos de regressão.

Na enumeração de grade inspecionada, 16 ativos × 3 TFs × 6 horários produzem 933.120 combinações
antes da amostragem; aproximadamente 62,5% excedem 120 velas de aquecimento. São números de
grade, **não estratégias aprovadas**, nem justificativa para executar esse universo inteiro.

## 3. Supabase: usar bem, não transformar o banco em motor de backtest

### Divisão proposta

| Informação/trabalho | Destino | Política |
|---|---|---|
| Candles recentes e watermarks duráveis | Postgres | Retenção quente adaptativa, inicialmente avaliar 7–30 dias; não fixar sem medir. |
| Histórico de pesquisa | Storage privado, Parquet por ativo/período, com hash e índice | Preservar períodos necessários à pesquisa/holdout; cache local no Lab. |
| Payout observado e qualidade da coleta | Postgres; histórico compacto quando necessário | Guardar tempo de observação; ausência permanece ausência. |
| Execuções de pesquisa, datasets, tentativas e holdouts usados | Postgres metadados; relatórios/TradeLogs no Storage privado | Preservar rastreabilidade inclusive de candidatos rejeitados. |
| Manifestos assinados | Storage público/CDN + registro/pointer autoritativo no Postgres | Objetos versionados imutáveis; cache e ETag. |
| Resultados enviados voluntariamente pelos clientes | Endpoint em lote + Postgres/arquivo agregado | Sem credenciais/PII; dados não confiáveis não aprovam estratégias sozinhos. |
| Backtests, PBO, replays, seleção de portfólio, geração de Parquet | Processo do Lab | Não executar em SQL nem em Edge Functions. |
| Candles ao vivo, indicadores, sinais e reservas do cliente | Bot local | Não transmitir ticks ao Supabase; zero consulta remota no ciclo financeiro. |

### Capacidade: números para dimensionar, não medição da conta

A página pública consultada informa no Free: 500 MB de banco por projeto, 1 GB de Storage,
5 GB de egress e 5 GB de cached egress; também informa pausa por inatividade.
O Pro lista capacidades maiores e custo a partir de USD 25/mês. **Não verifiquei o plano,
o consumo nem a cobrança do projeto do operador.** Confirmar tudo no CAT-01.
Fonte: [Supabase Pricing](https://supabase.com/pricing).

Hipótese conservadora: 16 séries M1 disponíveis 24 horas/dia:

- 23.040 candles/dia.
- 180 dias: 4.147.200 linhas.
- Com 200–400 bytes por linha **incluindo índices, hipótese a medir**: cerca de 0,83–1,66 GB.
- 30 dias: 691.200 linhas, aproximadamente 138–276 MB.
- 7 dias: 161.280 linhas, aproximadamente 32–65 MB.
- Isso não inclui outras tabelas; sessões fechadas podem diminuir o volume real.
- Armazenamento Parquet também consome quota. Medir compressão; não presumir que 1 GB
  manterá todo o histórico de pesquisa e backups indefinidamente.

O schema atual tem retenção de 180 dias antes de arquivar. Reduzir a **retenção quente**
exige ADR e migração/configuração compatível; não significa reduzir o histórico total usado
na validação. Se o volume necessário exceder a capacidade, escolher conscientemente entre
plano maior, menos ativos ou arquivo externo já suportado. Não destruir evidência para caber.

Exemplo de distribuição: polling de 900 s = 96 verificações/dia/cliente.
Com 1.000 clientes, são 2,88 milhões de verificações em 30 dias. Um corpo hipotético de 30 KB
em todas as respostas daria aproximadamente 86,4 GB; com ETag e uma versão nova/dia, os corpos
das versões baixadas seriam aproximadamente 0,9 GB, mais headers e verificações.
**304 não significa custo zero**, nem elimina limites de requisições.

Uploader a cada 5 minutos, se enviar mesmo vazio, chegaria a 8,64 milhões de chamadas/mês
com 1.000 clientes. Não enviar lote vazio; medir taxa efetiva de operações, lote e retenção.
Não criar conexão Realtime por cliente/ativo/indicador.

As Edge Functions têm limites de memória/CPU: a documentação consultada lista 256 MB e
2 segundos de CPU por requisição, além de limites de duração.
Fonte: [Edge Function Limits](https://supabase.com/docs/guides/functions/limits).
São adequadas para validar/publicar contratos e receber lotes pequenos, não para pesquisar estratégias.

### Limpeza: o que entra e o que não entra

Candidatos à limpeza **após inventário e restauração testada**:
exports temporários vencidos; objetos duplicados sem referências; execuções de staging com
TTL explícito; logs volumosos já resumidos/arquivados; candles quentes cujo arquivo frio foi
baixado, conferido e registrado com sucesso.

Não apagar: perfil do cliente; credenciais; state.db; strategy_data.db; ordens/reservas;
journal financeiro; manifestos históricos referenciados; chaves; registros de holdout queimado;
registro das tentativas de pesquisa; datasets necessários para reproduzir uma aprovação.

A autorização para melhorar o uso do banco não justifica apagar “o que parece velho”.
A remoção deverá listar alvo, tamanho, motivo, referências, backup e teste de restore.
No Storage, usar API de objetos: apagar apenas linhas SQL de metadados pode deixar arquivos órfãos.
Fonte: [Deleting Storage Objects](https://supabase.com/docs/guides/storage/management/delete-objects).
DELETE no Postgres não garante queda imediata do tamanho físico; espaço pode ficar reutilizável.
Não executar VACUUM FULL automaticamente, pois bloqueia tabelas.
Fonte: [Database Size](https://supabase.com/docs/guides/platform/database-size).

## 4. Contratos de segurança e implantação

1. Bot e Lab permanecem produtos isolados: ambientes, locks, builds, processos e bancos próprios.
   Sem imports cruzados, IPC novo ou arquivos privados compartilhados. Vetores públicos são
   copiados/versionados como artefatos de conformidade.
2. Manifesto descreve receita e evidência; nunca código Python executável nem comando de compra.
3. Opt-in de outcomes é telemetria upstream já prevista no Hub, não sinal operacional.
   Formalizar essa distinção nas invariantes sem ampliar acesso a dados privados.
4. Real não será habilitado por esta evolução. Preservar guards de Real e isolamento IQ/Deriv.
5. Uma ordem em voo; UNKNOWN não vira retry; reserva e reconciliação permanecem autoritativas.
6. Startup, reconnect e troca de estratégia não armam bot automaticamente.
7. Compartilhar apenas cálculos puros com entradas/identidade iguais. Estado de risco,
   SPRT, resultados, ordens e elegibilidade não é compartilhado entre estratégias/contas.
8. Biblioteca ampla não significa manter todo indicador de toda estratégia histórica ativo.
   Compilar plano apenas para catálogo aplicável, com limites de capacidade explícitos.
9. Filtrar horário de execução não pode interromper aquecimento necessário antes do horário.
10. Estratégia com indicador recursivo inicializado no histórico completo pode diferir daquela
    reinicializada nas últimas 120 velas. Definir versão e política de bootstrap; revalidar
    cientificamente, em vez de chamar diferença real de “otimização bit-idêntica”.
11. Alterações incompatíveis de contrato: leitor compatível primeiro; novo canal/schema depois;
    manifestos históricos preservados. Regressão de versão não serve como rollback de publicação:
    publicar nova versão monotônica com receitas anteriores ainda válidas.
12. Logs nunca contêm e-mail, senha, tokens, authorize, dados de conta ou chaves privadas.
    Credenciais administrativas anteriormente compartilhadas devem ser revisadas/rotacionadas
    pelo fluxo seguro de operação, sem copiá-las para código, documentos ou comandos visíveis.
13. Nunca contornar bloqueios da corretora por ocultação, mudança de identidade ou agressividade.
    Respeitar pacing, erros, cooldowns e indisponibilidade.
14. Sem resultado “PASS” para teste não executado; simulação não comprova operação externa.

## 5. Como executar os prompts

Os IDs CAT-00…CAT-21 são novos e não substituem os P01…P15 históricos.
Cada caixa abaixo é uma tarefa. **Cole o preâmbulo comum junto com o prompt escolhido.**
Um prompt = um PR/revisão; não executar a sequência inteira sem verificar o critério de saída.
“Novo módulo” é nome proposto: se houver equivalente, estender o existente, sem duplicá-lo.

### Preâmbulo comum obrigatório

~~~text
Trabalhe em trading-lab-desktop. Há dois produtos:
BOT = raiz do repositório; LAB = strategy-lab/; HUB = strategy-lab/apps/hub/.
Os caminhos do prompt são relativos ao produto explicitado.

Antes de editar, leia AGENTS.md, RULES.md, AIGUARD.md da raiz e as instruções aplicáveis.
No LAB/HUB leia também strategy-lab/04-AGENTS.md, 01-ARCHITECTURE.md e 03-PRD.md
integralmente, além da arquitetura/PRD do BOT quando ele for alterado.
Leia o plano docs/PLANO_CATALOGO_INCREMENTAL_SUPABASE_PROMPTS.md e os contratos
produzidos pelos prompts anteriores. Inspecione branch, diff, versão e arquivos reais.
Se estrutura ou instrução conflitar com invariante, pare e reporte opções antes de editar.

Preserve alterações do usuário. Não crie dependências novas, não altere versões
arbitrariamente e não edite migrations já aplicadas. Use ambientes/locks próprios.
Nenhuma operação financeira externa, login com segredo do histórico ou habilitação Real.
Nenhuma migração/limpeza de produção nesta tarefa por conveniência: staging primeiro;
operação em produção exige escopo e alvos verificados e pré-requisitos satisfeitos.

Mantenha Decimal no domínio numérico conforme invariantes, UTC com semântica documentada,
time.monotonic() para deadlines/idade e guards existentes. Sem imports cruzados BOT↔LAB.
Não remova filtros ou enfraqueça testes protegidos para obter aprovação.
Não refatore módulos financeiros alheios à tarefa.

Entregue diff revisável, testes de regressão, documentação e WORKLOG append-only do produto
alterado com IDs R-* reais cobertos, decisões, medidas e limitações. Novos requisitos
recebem IDs na ADR antes de serem citados como existentes.
Rode suíte inteira do produto alterado, Ruff check/format e mypy com a configuração própria;
no LAB use strict; no BOT inclua compileall e git diff --check. HUB inclui Deno check/test/fmt
e testes staging quando disponíveis. Mudança de contrato público exige as duas suítes,
sem importar código de um produto no outro.
Informe EXECUTED/PASS, EXECUTED/FAIL ou NOT EXECUTED/BLOCKED, com razão e evidência.
Se falta infraestrutura, não invente sucesso e não prossiga por cima do bloqueio.
Não faça commit/push/release/deploy fora do escopo explícito desta tarefa.
~~~

### Sequência e portas de saída

| Ordem | Produto | Resultado necessário para seguir |
|---|---|---|
| 00–02 | Ambos/Hub | Baseline, orçamento medido, contratos e compatibilidade definidos |
| 03–06 | Lab/Hub | Dados e aprovação científica reproduzíveis, sem aprovação artificial |
| 07 | Lab | Espaço de pesquisa compatível e limitado |
| 08–10 | Bot | Dados/indicadores compartilhados e execução segura em shadow |
| 11–12 | Lab | Frequência executável e complementaridade medidas fora da amostra |
| 13–16 | Hub/Bot | Publicação consistente, consumo integrado e UI/telemetria verdadeiros |
| 17–18 | Hub/Lab | Arquivo frio restaurável; somente então limpeza |
| 19–21 | Ambos | Benchmark, qualificação real dos dados e release controlado |

O trabalho pode ser organizado em trilhas depois dos contratos, mas esta ordem sequencial é
segura para execução manual. Nenhuma dependência autoriza pular critério de aceite.

## CAT-00 — Baseline e ADR dos contratos

~~~text
Produto: BOT + LAB, somente diagnóstico, testes e documentação.
Dependência: nenhuma.
Objetivo: congelar a base real e resolver ambiguidades antes de mudar execução.

1. Inventarie componentes existentes e identifique exatamente o fluxo do EXE e do CLI Lab.
   Registre commit, dirty tree, versões, Python efetivo, ambiente, locks e pipeline canônico.
   Esclareça divergência de Python requerido e utilizado, se ainda existir.
2. Execute validações completas separadas. Não use contagens históricas como resultado atual.
   Preserve regressões de warmup, candidatos, payout/monitor e falha não pegajosa.
3. Crie ADR de execução: significado de ts (abertura/fechamento), vela fechada,
   agregação M1→TF, horário, empate, instante de entrada/expiração e atraso.
   Resolva explicitamente a divergência documental do limite estrito de vela fechada (I-3).
   Não altere a invariante/teste DST sem decisão registrada e compatível com o usuário.
4. Defina execution_semantics_version, bootstrap de indicadores recursivos, revisões
   de receita, fingerprints de dataset, aritmética/ordem de operações e canonicalização.
   Defina tratamento de candle corrigido, gap, reconnect e dado atrasado.
5. Audite procedência do catálogo atual: contagens, Wilson, payout, relatórios e dataset.
   Classifique VERIFIED/UNVERIFIED/INCONSISTENT. Não re-assine nem delete entradas.
6. Registre requisitos novos e compatibilidade, matriz de risco, rollback e quais
   fases exigem novo resultado de pesquisa. Não prometa risco zero de regressão.

Aceite: baseline reproduzível; todas as falhas conhecidas explicitadas; ADR sem ambiguidades
de tempo/bootstrap; inventário do catálogo com evidência; nenhuma alteração operacional.
Se a base falhar, documente/reproduza; não certifique build nem passe ao rollout.
~~~

## CAT-01 — Inventário e orçamento do Supabase via CLI

~~~text
Produto: HUB/LAB. Dependência: CAT-00.
Objetivo: dimensionar uso real, somente leitura remota e diagnóstico local.

1. Verifique instalação/versão/help do Supabase CLI antes de usar comandos.
   Use login/configuração segura já disponível; não coloque segredo em saída/arquivo.
2. Confirme refs de produção/staging, plano/quota, extensões, migrations aplicadas,
   cron, funções, buckets, policies e grants. Não inferir staging pelo nome.
3. Meça pg_total_relation_size incluindo índices/TOAST; linhas/período/ativo;
   tamanho de manifests, logs, gaps, outcomes e holdouts; objetos Storage/órfãos;
   consumo de egress/invocações quando disponível. Não exponha conteúdo privado.
4. Avalie índices possivelmente duplicados com EXPLAIN e workload, sem removê-los.
5. Faça modelo de crescimento para 16/30 ativos e 100/1.000 clientes usando
   médias/p95 observadas. Separe carga do Lab, do Hub e do bot.
6. Proponha budgets nomeados: ocupação warning/critical, ingestão, requests,
   retenção quente/fria, pesquisa concorrente e tamanho de manifesto.
   Inicie com alertas em 70%/85% da quota útil como proposta, revisável pelos dados;
   não desligue reconciliação nem apague dados por pressão.
7. Registre ausência de pg_net/pg_cron ou credencial CLI como BLOCKED com alternativa
   job local; não instale serviço externo/pago nem altere produção.

Aceite: relatório com medidas/datashora, fórmulas e capacidade restante; plano de retenção
que preserve datasets; comandos reproduzíveis sem segredos; zero DDL/DELETE remoto.
~~~

## CAT-02 — Contrato público de receita, evidência e capacidades

~~~text
Produto: LAB packages/manifest_schema + contratos do BOT.
Dependências: CAT-00, CAT-01.
Objetivo: tornar o que é pesquisado, publicado e executado um contrato verificável.

1. Estenda modelos/schema de forma versionada para distinguir primitives_version,
   paridade, execution_semantics_version, revisão estável da receita, dataset/run
   de origem, evidência medida e capacidade exigida (TF, warmup, volume, produto).
   Reaproveite campos existentes. Não invente novas famílias executáveis.
2. Descreva receita F1..F5 como composição/params allowlisted, nunca código arbitrário.
   strategy_key/ids persistidos não mudam sem migração e contrato explícitos.
3. Status approved/observation/rejected e fallback demo_only local mantêm semânticas
   separadas. Não acrescente demo_only ao contrato remoto sem ADR/versionamento.
4. Defina vetores públicos para composição, outputs, bootstrap, elegibilidade,
   desempate, rejeição por recursos e canonicalização/assinatura.
   Primitive parity existente continua protegida; não regenere hash para esconder diferença.
5. Consumidores atuais precisam continuar aceitando versões históricas válidas.
   Se schema estrito impedir campo novo, use canal/schema novo e matriz de suporte:
   leitor compatível primeiro; produtor só depois. Bytes históricos são imutáveis.
6. Defina contrato de evidência de pesquisa e telemetria opt-in sem account_id,
   e-mail, credencial ou comando financeiro. Identifique resultados sintéticos.
7. Teste payload hostil, campo desconhecido, versão não suportada, params fora de faixa,
   warmup incompatível e alteração de assinatura. Dados sintéticos nunca viram
   approved de produção por simples troca de flag.

Aceite: modelos/schema/vetores sincronizados; duas suítes de contrato verdes; plano
de rollout com compatibilidade explícita; nenhuma publicação remota nesta fase.
~~~

## CAT-03 — Dataset real, identidade, tempo e payout sem vazamento

~~~text
Produto: LAB tools/strategy_lab/collect e research/dataset.py, payout_lookup.py, CLI.
Dependências: CAT-02.
Objetivo: dataset confiável antes de medir assertividade ou frequência.

1. Corrija carga multiativo: candles e payout de um ativo jamais alimentam outro.
   Use bundle tipado com asset exato, TF, origem e fingerprint; OTC != spot.
2. Pesquisa com dados reais deve exigir origem explícita e cobertura validada.
   Sintético somente com --synthetic e destino de pesquisa claramente sintético;
   ausência de --parquet/DB não pode escolher sintético silenciosamente.
3. Construa TF a partir de M1 conforme CAT-00: bucket completo, UTC, sessão correta,
   nada de vela parcial ou próximo fechamento visível. Recuse gaps in_session.
   Cobertura usa calendário/sessões, não penaliza intervalo fechado como falta.
4. Volume ausente é ausente: não converter None em zero/fabricar ticks.
   Primitivos dependentes dele ficam inelegíveis com motivo explícito.
5. Payout deve ser conhecido no instante do sinal: preserve observed_at e regra as-of.
   Média final de uma hora não pode fornecer informação futura para o início da hora.
   Dataset legado sem granularidade suficiente recebe limitação explícita; não inventar.
6. Gere snapshot imutável e fingerprint por origem/ativo/TF/intervalo/schema/qualidade.
   Prepare interfaces de leitura hot+cold sem duplicar linhas nem compartilhar DB do bot.
7. Teste dois ativos com séries opostas, TFs, DST, candles correntes, payout posterior,
   samples=0, volume ausente, sessões e três reexecuções idempotentes.

Aceite: coverage-report correto por ativo/TF; toda pesquisa referencia snapshot;
dados insuficientes bloqueiam aprovação; testes protegidos permanecem verdes.
~~~

## CAT-04 — Replay de referência equivalente à estratégia completa

~~~text
Produto: LAB research/replay_simulator.py e primitivas/composição; BOT testes de contrato.
Dependências: CAT-02, CAT-03.
Objetivo: mesma receita gera mesmas decisões nos dois produtos, sem imports cruzados.

1. Implemente composição conforme CAT-00, incluindo limites de regime por família:
   ADX máximo do F1, largura do F4 e demais gates reais. Não basta comparar indicadores.
2. Respeite candidate.tf e hours, bootstrap recursivo, sinal no fechamento e
   liquidação no próximo intervalo correto. Próxima linha após um gap não é t+1.
3. Distinga simulação close-to-close e execução por preço/expiração do broker.
   Resultado histórico aproximado não será apresentado como retorno externo comprovado.
4. Produza vetores públicos completos de cada família em vários ativos/TFs,
   fronteiras, falta de volume, gaps, empate, horários e reinicialização.
   Compare sinais, razões, valores e timestamps, não só hash de resultado final.
5. Reconcilie funções locais do BOT com a referência pela via de testes.
   Se corrigir matemática/composição mudar comportamento, versione e invalide
   somente evidência incompatível, sem editar histórico; exige nova pesquisa.
6. Mantenha teste sem lookahead nos 14 primitivos. Oráculo proposital deve ser
   realmente detectado como acesso futuro, não apenas ter p_hat > 0.95.
7. Triagem vetorizada nunca aprova; defina limites de cobertura suportada e compare
   timestamps com replay. Divergência de triagem tem medida, não aprovação automática.

Aceite: paridade exata da receita para mesma semântica/dataset/bootstrap;
testes explicam qualquer diferença da versão antiga; futuros dados não são acessíveis;
nenhuma estratégia nova aprovada nem publicada.
~~~

## CAT-05 — Evidência durável e holdout protegido no Hub

~~~text
Produto: HUB migrations novas + LAB Repository.
Dependências: CAT-02, CAT-03.
Objetivo: impedir aprovação sem rastreabilidade e reutilização oculta de holdout.

1. Inspecione migrations existentes (0001..0006 na base); crie próximas versões livres,
   nunca reescreva aplicadas. Amplie registros de datasets/runs/trials/evidência/artefatos.
2. Registre universo de tentativas, versão de método/código, params canônicos,
   fonte real/sintética, partições, hash, contagens e estado calculado da aprovação.
3. Torne registro de holdout persistente, consultado no startup, protegido por
   constraints/transação e fail-closed. Falha ao registrar/ler não pode virar except/pass.
   Concorrência de duas pesquisas não permite consumir a mesma reserva proibida.
4. Metadados pequenos no Postgres; TradeLogs/relatórios extensos em objetos privados
   hash-addressed. Evite duplicar candle JSON dentro de relatórios.
5. RLS e grants explícitos: cliente não lê datasets/holdouts; não pode aprovar run
   nem assinar manifesto. Segredo administrativo permanece no processo confiável.
6. Adapte interfaces FakeRepository e reais; imponha guard de staging.
   Teste restart, concorrência, rollback, falha de Storage/DB e JWT não autorizado.
7. Não apague tentativas rejeitadas: são necessárias ao controle de seleção múltipla.

Aceite: migrations do zero e upgrade em staging; constraints/RLS demonstradas;
holdout não é esquecido após restart; pipeline interrompe quando prova não está disponível.
~~~

## CAT-06 — Aprovação estatística sem atalhos ou relatórios fabricados

~~~text
Produto: LAB research/runner.py, report.py, holdout.py, gates/, scorer.py.
Dependências: CAT-04, CAT-05.
Objetivo: cada aprovado deriva de evidência real calculada fora da amostra.

1. Separe treino, validações temporais e holdout final conforme PRD, com números
   mínimos reais de OOS. Total do dataset não substitui tamanho fora da amostra.
   Elimine aprovação por fallback curto de datas e holdout desativado em caminho real.
2. Wilson, p_hat, windows_passed, holdout_passed, retorno e streak vêm de TradeLog
   auditável. Não usar "8/8", aprovado=>holdout ou payout fixo não observado.
3. Aplique penalidades determinísticas de atraso previstas, registre todos os gates,
   rejeitando quando payout necessário não existe no grid permitido.
4. FDR/Benjamini-Hochberg usa conjunto real de tentativas, não candidate_rank=1.
   Reconcile método denominado permutação com sua implementação; declare hipótese
   de independência e trate dependência temporal/entre candidatos com teste apropriado.
   Não mude método/limiar silenciosamente: ADR e regressões científicas.
5. PBO/estabilidade não podem converter valores monetários/probabilidades para float
   contra as invariantes. Corrija domínio ou reporte conflito antes de seguir;
   não enfraqueça scanner. Mesmas regras para numpy em cálculos de aprovação.
6. Holdout selado só é aberto depois de congelar seleção e método; pesquisa posterior
   que o consultou não pode continuar tratando-o como validação independente.
7. Sanidade: moeda justa realmente gera trades avaliáveis; payout ausente não pode
   fazer teste de falso positivo passar com zero observações. Sintético edge positivo
   valida encanamento, nunca produção.
8. Família desconhecida não é traduzida para F1 por fallback.

Aceite: reproduzir cada decisão a partir de snapshot+seed+versão; casos hostis
bloqueados; nenhuma evidência sintética ou insuficiente aprovada no canal real.
~~~

## CAT-07 — Pesquisa ampla, limitada e compatível com o executor

~~~text
Produto: LAB research/grammar.py, candidate.py e CLI de pesquisa.
Dependências: CAT-02, CAT-06.
Objetivo: aumentar diversidade testável, não explosão combinatória.

1. Consuma capabilities do contrato público do executor: famílias, TFs, params,
   dados exigidos, aquecimento e orçamento. Prune inelegíveis antes de materializar.
2. Gere candidatos em streaming/dedup determinístico; budget de trials nomeado e
   registrado. Comece com 300–500 candidatos elegíveis por experimento definido,
   sem considerar 500 aprovações desejadas e sem pesquisar novamente até "dar certo".
3. Respeite min/max/step; min/mid/max não pode criar parâmetro fora da grade.
4. Investigue incompatibilidade que elimina F5 embora ela esteja no contrato.
   Corrija apenas com justificativa e paridade; não habilite combinações proibidas.
   Considere horários cobertos/omitidos com base no calendário real.
5. Famílias que dependem de níveis absolutos/preço exigem parâmetros adequados ao
   ativo; valores sintéticos 99/101 não servem automaticamente para Forex.
6. Amplie dentro de famílias executáveis existentes primeiro. Nova família exige
   extensão de contrato, implementação dos dois lados e pesquisa própria.
7. Reporte universo teórico, elegíveis, descartados por motivo, amostrados e custo.
   Use sementes registradas; preserve registro de todas as tentativas.

Aceite: memória limitada; mesmos inputs=>mesma amostra; zero candidato fora de
capacidade; diversidade medida por família/ativo/TF/horário, sem quota de aprovados.
~~~

## CAT-08 — Hub local de séries e agendamento de mercado

~~~text
Produto: BOT; apps/core/iqoption_auto_trader.py e componente local de séries existente/novo.
Dependências: CAT-02, CAT-04. Executar após CAT-07 nesta sequência.
Objetivo: buscar cada série uma vez, sem multiplicar conexões ou consultas por estratégia.

1. Extraia gerenciamento de séries para componente próprio, mantendo cliente/worker
   e guarda de mensagens existentes. Chave inclui broker, conta, produto,
   session/generation, asset EXATO e TF. Nenhuma normalização OTC↔spot.
2. Para cada série, calcule maior warmup necessário e a política de bootstrap do contrato.
   Carregue lotes paginados fechados com limite e dedup. Não corte em 120 silenciosamente:
   suportar com budget ou rejeitar previamente por capacidade explicitamente.
3. Deduplicate fetch por série/fechamento; feche e valide buckets antes de publicar.
   Atualizações compartilhadas são imutáveis, ordenadas, processadas por single writer.
4. Crie scheduler justo por deadline de fechamento, prioridade de bootstrap e
   recuperação, evitando starvation de ativo menos frequente.
   Dados atrasados alimentam estado, mas não disparam ordem retroativa.
5. Continue aquecimento de receitas aplicáveis antes do seu horário. Descarte
   histórico/outputs só quando não houver dependentes nem referência necessária.
6. Preserve guarda de mensagens runtime. Na base: total interno 90/min, mercado
   60/min; são políticas locais, não limite oficial garantido da corretora.
   Exemplo ideal 16 M1+16 M5+16 M15 = 20.2667 fetches/min em regime estável;
   medir requests reais incluindo payout, bootstrap, relógio, ordens e reconciliação.
   Nunca consumir reserva financeira/recovery para acelerar aquecimento.
7. Reconnect/generation ou candle corrigido invalida plano de dados e reconstrói
   com fencing; dados velhos não contaminam conta/sessão nova.
8. Teste duplicata, chegada fora de ordem, candle parcial, gap, correção histórica,
   16 ativos/3 TFs, reconnect durante fetch e fila cheia.

Aceite: N receitas da mesma série não geram N fetches; warmup completo verificável;
fila limitada e justiça demonstrada; não existe buy nesta camada; bot não rearma.
~~~

## CAT-09 — Grafo incremental de indicadores compartilhados

~~~text
Produto: BOT apps/core/families/ e novo componente local de execução incremental.
Dependências: CAT-04, CAT-08.
Objetivo: eliminar recálculo repetido mantendo contrato numérico verificável.

1. Compile receitas allowlisted em um plano local de dados/indicadores/decisões.
   Nada de eval, import remoto ou bytecode vindo do manifesto.
2. Chave de indicador contém identidade da série do CAT-08, nome, params canônicos,
   primitives_version, execution_semantics_version, identidade do bootstrap e de
   entradas auxiliares injetadas (por exemplo níveis de outro TF e sua revisão).
   RSI14 de séries/versões/contas diferentes nunca compartilha estado.
3. Uma atualização por candle fechado novo e por nó; guardar output imutável do epoch.
   Família consome outputs, preservando seus próprios gates. Risk, SPRT e ordem
   não pertencem ao nó de indicador. Reference counting controla ciclo de vida.
4. Warmup não é só quantidade: validar continuidade, TF, origem, volume requerido
   e bootstrap. Expor WARMING_UP/READY/INVALID e razão.
5. Crie modo shadow que calcula e compara contra referência da MESMA semântica;
   shadow é incapaz de chamar submit. Estratégia antiga continua no caminho legado.
6. Compare valores Decimal, sinais, motivos e timestamps nos vetores públicos,
   séries longas, fronteiras e restart. Registre diferenças do legado de janela curta
   como mudança de semântica, não como regressão a esconder.
7. Memória e backlog limitados. Falha/discordância invalida receita e impede novas
   entradas; não troca automaticamente para algoritmo financeiro alternativo.
8. Teste cinco receitas RSI14 compartilhando nó, parâmetros diferentes isolados,
   remoção de uma receita mantendo outras, reload e reconnect sem vazamento.

Aceite: bit-identidade com referência CAT-04 para mesmos inputs/bootstrap;
contadores provam um cálculo/nó/candle; shadow zero submit; estado por receita isolado;
nenhuma execução financeira habilitada pelo novo engine nesta tarefa.
~~~

## CAT-10 — Integração segura com candidatura e pipeline financeiro

~~~text
Produto: BOT iqoption_auto_trader.py, lifecycle e projeções do Core.
Dependências: CAT-09.
Objetivo: usar o plano incremental sem burlar gates, persistência ou idempotência.

1. Integre saídas pelo resolve_candidates e pelo admission/ticket atuais.
   Preserve asset exato, TF governado pelo manifesto, horários, status, retiring,
   payout fresco, monitor/SPRT, generation fencing e checagem final sob lock.
2. Preserve SINGLE/Practice do fallback RSI explícito e bloqueios Real.
   Ligar IQ não pode ligar Deriv. Trocar modo/plano não arma trading.
3. Defina arbitragem determinística por epoch e limite de decisão:
   empate estável, sinais opostos conforme contrato e uma ordem em voo.
   Não espere indefinidamente ativo atrasado; registre missed deadline.
   Mudança em relação à ordem sequencial antiga exige replay de portfólio e versão.
4. Remova leituras de DB do ciclo de análise usando projeção autoritativa versionada,
   atualizada por eventos do writer e bootstrap/reconciliação.
   Não crie segunda fonte de risco. Snapshot desconhecido/stale bloqueia.
   Core mantém transação final que verifica/reserva/persiste antes do worker.
5. Intenção/reserva/outbox/reconciliação existentes continuam únicos.
   UNKNOWN não é REJECTED; não libera reserva sem evidência; nenhum retry financeiro.
6. Preserve falha temporária por escopo com recuperação, bloqueios críticos globais
   e os testes de falha pegajosa, cache terminal e restart. Não queimar sinal antes
   do ponto definido no contrato; não reenviar sinal financeiro consumido.
7. Gates imediatamente anteriores à submissão devem usar estado atual, mesmo se
   o indicador ficou READY antes de payout, catálogo ou sessão mudar.
8. Flags separadas: legado, shadow e novo engine. Default continua seguro.
   Desativação do novo engine deixa entradas desarmadas, mas acompanha ordens abertas.

Aceite: regressões Causa 2/3/5 e recuperação verdes; zero DB read no hot path medido;
writer autoritativo preservado; zero dupla submissão sob concorrência/crash;
replay de 24h simulado discrimina NO_CANDIDATE/ASSET_MISMATCH/OUTSIDE_HOURS e demais gates.
~~~

## CAT-11 — Replay da frequência realmente executável do portfólio

~~~text
Produto: LAB research/, sem imports do BOT.
Dependências: CAT-06, CAT-10.
Objetivo: medir oportunidades aproveitáveis, não somar sinais incompatíveis.

1. Implemente simulador de portfólio que reproduz contrato público de arbitragem,
   fechamento, atraso, expiração, capital, cooldown, limites e uma ordem em voo.
2. Para cada oportunidade, registre motivo: elegível, conflito, ordem em voo,
   falta de payout, fora de horário, warmup, stale, risco, prazo ou execução.
3. Use mesmo conjunto de vetores de decisão do BOT por artefato público.
   Não reimplemente versão "mais permissiva" para melhorar backtest.
4. Resultado financeiro usa payout observado aplicável, empate=perda e hipóteses
   explícitas de preço/latência. Nunca estimar execução externa só por candle próximo.
5. Meça sinais/dia, operações executáveis/dia, distribuição por hora, tempo ocioso,
   concentração, sobreposição e contribuição marginal por receita.
   Denominadores usam duração precisa/cobertura, não truncamento de dias.
6. Compare catálogo atual verificado com 10/20/30/50 receitas selecionadas.
   Biblioteca que só duplica mesmo evento não multiplica amostra independente.
7. Replays com falha são simulados e identificados: reconnect, payout faltante,
   candle atrasado e risco já ocupado. Não inventar preenchimento de ordens recusadas.

Aceite: somatório de sinais é separado de operações possíveis; paridade de arbitragem;
nenhuma violação de uma ordem em voo; relatório reproduzível por snapshot+seed.
~~~

## CAT-12 — Seleção offline por qualidade e complementaridade

~~~text
Produto: LAB pesquisa, ranking e relatório de portfólio.
Dependências: CAT-07, CAT-11.
Objetivo: entregar biblioteca útil, não escolher vencedores de ruído recente.

1. Primeiro aplique gates individuais; só depois componha biblioteca entre aprovadas.
   Não relaxe mínimos, payout ou amostra para atingir 30 estratégias.
2. Selecione em partição de desenvolvimento por contribuição marginal de
   oportunidades executáveis e robustez, com limites de correlação/concentração
   definidos antes de abrir holdout final.
3. Trate grupos que dependem do mesmo evento/ativo/horário; não contar duplicatas
   nem resultados de muitos clientes como novas observações independentes.
4. Declare algoritmo, desempate, budgets e número de portfólios tentados.
   Sem "top 3 últimas vitórias" no bot nem otimização contínua no cliente.
5. Congele seleção e parâmetros antes do holdout de portfólio. Se falhar, rejeite;
   não volte a ajustar usando o mesmo holdout como se continuasse virgem.
6. Compare incrementalmente 10/20/30/50 e, se capacidade permitir, 100 receitas.
   Pode terminar com menos receitas ou nenhuma: isso é resultado válido.
7. Reporte intervalos de confiança, payout de equilíbrio, EV estimado e sensibilidade,
   frequência fora da amostra, drawdown/streak e custo computacional.
   Separe pesquisa sintética, histórico real e validação externa.
8. Produza rascunho de manifesto referenciando evidência; não publique automaticamente.

Aceite: critérios predefinidos e seleção reproduzível; ganho de frequência medido
no simulador de portfólio, não soma bruta; nenhuma alegação de retorno garantido.
~~~

## CAT-13 — Publicação consistente, versionada e recuperável

~~~text
Produto: HUB functions/publish, mirror, contratos e migrations novas.
Dependências: CAT-02, CAT-05, CAT-12.
Objetivo: versão visível nunca representar publicação parcial/inconsistente.

1. Reaproveite publish atual. Exija schema/assinatura válida e run selado/evidência
   compatível para canal de produção. Sintético/test key somente staging.
2. Crie journal durável de publicação e objeto de versão imutável com hash verificado.
   Serialize/CAS versões no Postgres; SELECT max sozinho não impede duas publicações.
3. Defina pointer autoritativo que só referencia versão confirmada e acessível.
   Recomenda-se endpoint leve/cacheável que resolve pointer commitado para objeto
   imutável; current.json legado fica projeção derivada reparável, não autoridade.
4. Não alegue transação distribuída entre Storage e Postgres. Desenhe estados,
   recuperação e limpeza de órfãos para cada falha: upload, DB, pointer e mirror.
   Check-before-write sozinho não impede writer antigo de sobrescrever current.
5. Manifests vN não usam overwrite destrutivo. Retry de publicação é idempotente,
   nunca cria duas versões diferentes com mesmo número. Versão regressiva=>409.
6. Mirror usa outbox/job durável ou execução background suportada, com retry limitado,
   verificação de hash e telemetria. Não depender de Promise não aguardada.
7. Sirva ETag/Cache-Control e preserve fallback last-good. Clients atuais mantêm canal
   compatível; canal novo só recebe leitores prontos.
8. Testes de duas publicações concorrentes, interrupção em cada etapa, DB/Storage
   indisponível, object hash errado, chave A/B, test key prod, replay de versão e repair.

Aceite: staging serve versão coerente; nenhuma regressão de pointer em concorrência;
recovery idempotente; bytes assinados imutáveis; contratos Python/Deno verdes.
~~~

## CAT-14 — Consumo do manifesto ligado ao runtime do bot

~~~text
Produto: BOT ManifestClient existente, lifecycle e DynamicManifestCatalog.
Dependências: CAT-10, CAT-13.
Objetivo: catálogo publicado realmente chegar ao executor sem bloquear a UI/análise.

1. Confirme onde ManifestClient é ou não iniciado; use a implementação existente.
   Configure URL real verificada do canal e mirror, não endpoint placeholder.
2. Poll em background com ETag, jitter limitado, backoff e budgets.
   Não fazer GET nem leitura DB por sinal. Falha de rede mantém last-good somente
   dentro das regras mais restritivas de validade/offline e elegibilidade.
3. Valide assinatura, tamanho, schema, versão, compatibilidade de engine/primitivos,
   parâmetros e recursos antes de compilar. Incompatível=>motivo claro e sem execução.
4. Prepare plano/caches fora do caminho financeiro e aplique swap atômico.
   Nova receita pode aquecer sem mudar a identidade das ordens já abertas.
5. Ordem/reserva/monitor em andamento fica vinculada à revisão original até terminal.
   Receita removida/retiring não aceita novas entradas, mas não perde reconciliação.
6. Catálogo novo não arma bot. Mudanças que exigem desarme seguem política existente.
   Preservar escolha explícita SINGLE e compatibilidade de AUTO.
7. Cache local atômico, separado de credenciais; assinatura conferida no restart.
   Não assine localmente payload alterado para simular publicação aprovada.
8. Teste 304, timeout, CDN/mirror divergentes, expiry, relógio inválido, arquivo truncado,
   rollback monotônico, reconnect durante swap e estratégia removida com ordem aberta.

Aceite: integração demonstrada com staging/fakes; UI/análise responsivas;
zero acesso remoto no ciclo financeiro; nenhuma ordem órfã ou autoarm.
~~~

## CAT-15 — Outcomes úteis, baratos e não confiáveis por padrão

~~~text
Produto: HUB functions/outcomes, client_token e migrations novas.
Dependências: CAT-05, CAT-13.
Objetivo: coletar telemetria opt-in sem transformar clientes em fonte de aprovação automática.

1. Versione payload para identificar receita/revisão, manifesto/engine, ativo/TF/produto,
   ambiente e evento terminal deduplicável. Reuse campos atuais; histórico preservado.
   Nada de broker account_id, login, saldo, credencial ou PII.
2. Valide schema, tamanho, lote <=500, timestamps conforme janela de 7 dias vigente,
   origem/ambiente e idempotência. Client retry do MESMO evento não duplica.
3. Auditoria de JWT real: vínculo client_id e assinatura aceitos pelo endpoint/RLS.
   UUID escolhido pelo cliente não é identidade forte; emissão ilimitada de UUIDs
   contorna quota individual. Implemente limites globais/por endpoint e tamanho,
   sem fingerprint invasivo ou alteração comercial/licenciamento.
4. Revise grants de insert direto: não permitir contornar quotas/gateway via REST.
   Preserve contrato legado com migração e teste; não enfraquecer RLS.
5. Agregue com idempotência e retenção por bytes/tempo; raw vai para arquivo privado
   quando necessário. Resultados de um mesmo sinal replicado entre clientes formam
   grupo dependente, não multiplicam n de evidência.
6. Outcomes servem monitoramento/detecção de divergência. Aprovação exige dados e
   pesquisa qualificada; cliente malicioso não consegue promover receita.
7. Teste lote duplicado, UUID rotativo, assinatura inválida, timestamp futuro,
   payload excessivo, quota global, divisão prod/staging e acesso anon não permitido.
8. Documente custos: só lotes não vazios; medir chamadas/mês por cenário.

Aceite: dados mínimos, RLS e limites comprovados; ingestão/aggregate exactly-once;
nenhum caminho aprova estratégia a partir de relato não verificado.
~~~

## CAT-16 — UI verdadeira e telemetria local da execução

~~~text
Produto: BOT dashboard IQ, snapshots do Core e OutcomesUploader existente.
Dependências: CAT-10, CAT-14, CAT-15.
Objetivo: mostrar o que está ocorrendo e por que uma entrada não aconteceu.

1. Exponha por receita/ativo/TF: fonte real, modo, revisão, WARMING_UP,
   pronto, sinal observado, candidato elegível, ordem submetida, aceita e terminal.
   Neutro não é dado ausente. Não escrever "SINAL DISPARADO" como sinônimo de compra.
2. Mostre motivos e duração de espera: market/clock, payout, janela, warmup,
   capacidade, risco, ativo incompatível, deadline, ordem em voo e rejeição remota.
   Sem contadores artificiais nem resultado de rejeição contado como loss/win.
3. Métricas locais: séries, nós únicos, reuso, fetches, update/decisão p50/p95/p99,
   fila, atraso de fechamento, cache, memória e eventos descartados por fencing.
4. Mostre n/OOS, validade da evidência e hipóteses sem sugerir lucro garantido.
   Status de catálogo/assinatura e modo legado/shadow/incremental são verificáveis.
5. Ligue uploader existente somente com opt-in explícito e evento financeiro
   terminal persistido. Nenhum envio vazio. Fila limitada, ACK idempotente,
   expiração compatível com os 7 dias do Hub e tratamento explícito de vencidos.
6. Rede/Hub indisponível não trava trading local nem shutdown. Publicação de métricas
   não cria polling do banco na UI; snapshots/eventos autoritativos.
7. Teste botões IQ e Deriv independentes, reload de catálogo, milhares de linhas
   sem congelar UI, erro recuperável, disable opt-in e fechamento seguro.
8. Telemetria de ordens nunca dispara novas ordens ou altera risco.

Aceite: cada indicador visual tem evidência de estado; headless/UI regressões verdes;
zero segredos; separação entre sinal, envio e execução; uploader não bloqueante.
~~~

## CAT-17 — Arquivamento real, verificável e restaurável

~~~text
Produto: HUB migrations/functions/archive + LAB job local de arquivo/Repository.
Dependências: CAT-01, CAT-03, CAT-05, CAT-13.
Objetivo: mover histórico quente para arquivo frio sem perda ou reaparecimento no backfill.

1. Substitua o stub por protocolo operacional. Edge/cron registra jobs e acompanha
   estado; conversão pesada para Parquet acontece no processo Lab, não na Edge.
   Executor local indisponível deixa backlog limitado/alertado, nunca "arquivo concluído".
2. Inspecione archive_old_candles/complete_archive_job existentes. Hoje contagem
   declarada não comprova arquivo. Crie migration corretiva nova com grants
   restritos; desabilite conclusão insegura antes de permitir qualquer remoção.
3. Congele recorte estável por ativo/período com PKs e versões/checksums de conteúdo.
   Jobs sobrepostos têm exclusão/claim, idempotência e estado durável.
4. Gere Parquet usando stack já autorizada, com schema e representação decimal
   preservados, compressão medida e particionamento sem milhares de microarquivos.
5. Upload privado imutável; baixe novamente; valide hash, schema, contagem, PKs,
   OHLC/payout e conteúdo/fingerprint. Confirme permissão de leitura do processo Lab.
   Mesmo número de linhas com conteúdo diferente deve falhar.
6. Só então autorize remoção das linhas EXATAS ainda iguais às arquivadas.
   Linha alterada após export aborta/replaneja: não DELETE por faixa ampla baseado
   apenas em contagem. Atualização tardia não pode ser perdida.
7. Mantenha watermark durável separado de MAX(candles.ts) da camada quente.
   Arquivar tudo de um ativo não deve induzir backfill a baixar toda sua história.
8. Dataset lê hot+cold sem duplicar/omitir; índices de objetos e snapshots asseguram
   que histórico usado em holdout continua reproduzível.
9. Backup/restore inclui DB e objetos Storage: backup do DB não equivale a backup
   dos arquivos. Teste restore em destino isolado, com checksum e contagem.
10. Teste crash antes/depois de upload, verificação, registro e delete; alteração tardia;
    objeto corrompido; quota Storage; executor desligado e job duplicado.

Aceite: round-trip completo staging, incluindo consulta research hot+cold idêntica;
zero remoção sem prova; recovery idempotente; nenhuma ação destrutiva em produção
antes de inventário, retenção aprovada e restore verificado.
~~~

## CAT-18 — Retenção, limpeza e proteção de quota

~~~text
Produto: HUB/LAB operações de manutenção.
Dependências: CAT-01, CAT-17.
Objetivo: liberar apenas o que é dispensável, preservando prova e recuperação.

1. Implemente comando dry-run padrão: liste cada alvo, tamanho, idade, owner,
   referências, regra de retenção, hash e backup/restore relacionado.
   A lista deve ser revisável sem abrir conteúdo sensível.
2. Categorias separadas: temporários, órfãos de publicação, staging expirado,
   logs já arquivados e candles com arquivo frio verificado.
   Não confundir arquivo não indexado com arquivo sem dono: reconcilie inventários.
3. Mantenha referências de manifesto/run/holdout/dataset e eventos financeiros.
   Resumos estatísticos não substituem evidência necessária para reproduzir aprovação.
4. Aplicação usa plano fechado identificado por hash, projeto/ref e objetos/PKs
   explícitos. Se inventário mudou, aborta; nunca wildcard recursivo ou DROP CASCADE.
   Produção somente com alvos/escopo confirmados e política documentada.
5. Storage via API, não DELETE apenas de metadados SQL. Postgres em batches pequenos.
   Não executar VACUUM FULL/reindex/drop de índice sem análise separada.
6. Reavaliar índices duplicados com EXPLAIN e carga, se comprovado; migration nova
   reversível, restore e benchmark antes de qualquer remoção.
7. Alertas por quota útil, crescimento e backlog; reduzir trabalho de pesquisa/ingestão
   conforme política quando sem espaço. Não sacrificar reserva/reconciliação nem
   inventar dados faltantes. Não apagar holdout/trials para melhorar métricas.
8. Reporte bytes de objetos removidos, linhas, espaço lógico reutilizável e ocupação
   física real separadamente. Não declarar redução física sem medir.
9. Se retenção necessária não couber, reporte alternativas numeradas com volume/custo:
   plano maior, universo menor ou arquivo externo opcional. Nenhuma contratação automática.

Aceite: dry-run idempotente, planos obsoletos recusados, referências protegidas;
limpeza staging seguida de restore/replay idêntico; produção permanece intacta até
verificação explícita dos alvos; dados do cliente e perfis nunca são alvos.
~~~

## CAT-19 — Benchmark de catálogo no EXE, não só no pytest

~~~text
Produto: BOT, harness de benchmark e build canônico.
Dependências: CAT-10, CAT-14, CAT-16.
Objetivo: definir quantidade suportada por medidas e estabelecer budget de regressão.

1. Congele hardware/Windows/build/commit/dataset/cenário; registre CPU e definição
   de percentual, RAM, disco e event-loop. Use perfil temporário sem credenciais.
2. Cenários 10/30/50/100 receitas: alto reuso, nenhum reuso, muitos TFs e aquecimento
   máximo. Não medir só 100 cópias baratas de uma mesma receita.
3. Compare legado/referência/shadow/incremental com mesma semântica aplicável.
   Registro separado quando referência foi corrigida; velocidade não valida matemática.
4. Meça CPU média/p95, RSS/working set inicial/pico/estável, fetches, nós, filas,
   startup/warmup e atraso fechamento→decisão p50/p95/p99/máximo.
   Separe latência broker/rede de processamento local.
5. Proponha SLOs antes do ensaio e registre sua base: exemplo preliminar para máquina
   de 2 núcleos/8 GB, p95 local<=100 ms e p99<=500 ms após warmup.
   Esses valores são alvos a validar, não garantias. Defina teto de memória e
   deadlines reais a partir do baseline e do TTL de admissão já existente.
6. Não aumentar timeout para tornar ensaio verde. Se falhar, reduzir capacidade
   admitida e mostrar limitação; nenhum descarte silencioso para esconder atraso.
7. Faça carga prolongada real de processo (ex.: 2h) e replay de 24h simuladas.
   Não chame replay acelerado de 24h de disponibilidade externa.
8. Falhas: reconnect, janela de suspensão/gap monotônico, swap de catálogo,
   mercado lento, queue cheia e shutdown durante bootstrap.
9. Build windowed/onedir TradingLab.exe pelo pipeline atual; scanners, manifesto,
   portable e installer quando suportados. Nada de spec legado, console=True ou
   inclusão de strategy-lab/env/dados/segredos no EXE.
10. Teste startup/restart/shutdown e ausência de processos órfãos com perfil isolado.

Aceite: tabela por cenário e hardware; capacidade máxima ADMITIDA sustentada por
medida; decisões corretas e nenhuma duplicação/perda financeira; UI responsiva;
resultado do EXE comprovado, não inferido dos testes Python.
~~~

## CAT-20 — Qualificação com dados reais e shadow controlado

~~~text
Produto: LAB + BOT validação, sem habilitação financeira automática.
Dependências: CAT-06, CAT-12, CAT-14, CAT-19.
Objetivo: comprovar validade dos candidatos no universo que o cliente consegue executar.

1. Use coleta real autorizada, somente leitura, com cobertura/payout/sessão/volume
   suficientes. Se dados não bastarem, resultado é BLOCKED, não substituição sintética.
2. Congele dataset, versão do engine e bibliotecas antes dos testes finais.
   Reuse a evidência selada do CAT-12 para a seleção já congelada. Reproduzir uma
   execução com os mesmos inputs serve auditoria, não cria novo teste independente.
   Alteração de seleção/método exige nova partição ainda não examinada conforme PRD;
   não reutilize holdout consumido para escolher receitas ou declarar validação nova.
3. Reavalie entradas inconsistentes do catálogo histórico. Quarentena/retiring,
   se necessária, preserva histórico, ordem aberta e last-good ainda válido.
   Não atualize evidência por edição manual de JSON.
4. Publique inicialmente no canal staging/observation permitido, com leitor compatível.
   Bot em shadow registra decisões reais de mercado, payout e possíveis entradas;
   shadow nunca submete. Não copiar sinais do Lab para cliente.
5. Compare decisões com replay do MESMO fluxo capturado e semântica. Dados do
   cliente para análise exigem export diagnóstico sanitizado/consentido, não DB compartilhado.
6. Teste financeiro Practice é etapa separada: conta explicitamente confirmada,
   operador autoriza janela/limites e arma manualmente. Nenhuma conta Real.
   Sem autorização atual, marque financeiro NOT EXECUTED e siga só read-only/shadow.
7. Se autorizado, meça aceitação, latência, expiração, settlement, P&L e reservas pelo
   estado persistido e evidência do broker; UNKNOWN bloqueia/reconcilia, nunca retry.
8. Relatório distingue histórico real, simulação, shadow e Demo financeira.
   Frequência e resultado estimados não são promessa para cliente.

Aceite: lista efetivamente qualificada, ainda que pequena/vazia; paridade de execução;
causas de oportunidades perdidas quantificadas; nenhuma aprovação sem evidência.
~~~

## CAT-21 — Release incremental, rollback e operação

~~~text
Produto: BOT + LAB + HUB; documentação operacional e release.
Dependências: todos os anteriores; bloqueios críticos resolvidos.
Objetivo: disponibilizar sem converter incerteza em risco para clientes.

1. Execute matriz final de regressão dos dois produtos e Deno/staging, scanners,
   mypy/Ruff/format, compileall do BOT, diff-check e build/smoke canônicos.
   Registre somente versões e testes efetivamente executados.
2. Ordem de implantação: EXPAND compatível no Hub; leitores BOT compatíveis;
   novo canal/escritores; observação/shadow; ativação incremental Practice autorizada.
   CONTRACT só depois da janela de segurança e comprovação de ausência de leitor antigo.
3. Novo engine inicialmente desligado para execução. Faça coorte pequena explícita,
   depois amplie apenas com CPU/memória/latência/erros e decisões dentro dos budgets.
   Operador arma; flags/reconnect/update não armam automaticamente.
4. Critérios de parada: divergência numérica/sinal, duplicação, lease/generation inválida,
   risco inconsistente, dado stale, atraso acima do prazo, processo órfão ou consumo
   acima do budget. Falha de quote/rede segue bloqueio/recovery existente.
5. Rollback do engine deixa novas entradas desarmadas e acompanha ordens abertas.
   Não abandona revisão antiga de monitor/reserva. Manifesto volta por nova versão
   monotônica assinada, não downgrade ou alteração de objeto histórico.
6. Supabase indisponível não provoca reotimização/execução remota; last-good respeita
   validade e segurança. Documente operação com cache, quota cheia e coleta parada.
7. Runbooks: catálogo inválido, falta de dados/warmup, payout degradado, throughput,
   publicação parcial, restore, quota, UNKNOWN e rollback.
8. Preserve v1.9.11 salvo política/versionamento explicitamente aprovado; distinguir
   versão de aplicativo, schema, primitives e semântica de execução.
9. Entregue artefatos/hashes, matriz de compatibilidade, capacidade por hardware,
   relatório de frequência executável, evidência científica e limitações.
   Merge/tag/publicação externa somente no escopo de release autorizado.

Aceite: rollout reversível demonstrado em staging; backup/restauração e shutdown
verificados; bloqueios Real preservados; nenhuma certificação de lucro ou risco zero;
itens externos não executados permanecem claramente pendentes.
~~~

## 6. Provas transversais obrigatórias

| Prova | Onde nasce | Onde precisa permanecer |
|---|---|---|
| Mesmo dado/mesma semântica => mesma decisão | CAT-04 | CAT-09, CAT-11, CAT-19, CAT-20 |
| Não vê futuro, não mistura ativos/TFs | CAT-03/04 | Replay, triagem, bot e portfólio |
| Um fetch por série; um update por indicador idêntico | CAT-08/09 | Benchmark do EXE |
| Não compartilhar risco/conta/revisão | CAT-09/10 | Reload/reconnect/ordem terminal |
| Mesma falha não deixa bot parado sem razão/recuperação | Regressões existentes + CAT-10 | Soak/falhas/UI |
| Aprovação reproduzível, holdout não reutilizado | CAT-05/06 | CAT-12/13/20 |
| Publicação/consumo íntegros sob falha | CAT-13/14 | Rollout e rollback |
| Arquivo baixado e restaurado antes de remoção | CAT-17 | Toda aplicação do CAT-18 |
| Capacidade não presumida por número de estratégias | CAT-19 | Admission e docs do cliente |
| Sem Real, autoarm ou retry de UNKNOWN | Toda a sequência | Toda suíte/release |

Os testes existentes de primitivos, canário, manifesto hostil, moeda justa e DST/vela fechada
continuam protegidos. Não substituir evidência de corretora por fake; fakes comprovam contrato,
não disponibilidade ou execução externa.

## 7. Prioridade, decisão de capacidade e definição de pronto

Prioridade imediata: CAT-00…06. Sem dados/evidência corretos, acelerar o bot apenas executaria
mais depressa um catálogo de qualidade não demonstrada.

Prioridade de desempenho: CAT-08…10. Compartilhar dados e indicadores primeiro; medir CPU é
insuficiente se o scheduler deixa um ativo esperando enquanto seu sinal expira.

Prioridade de espaço: CAT-01 já mede e alerta; qualquer exclusão aguarda CAT-17/18.
Se o banco estiver perto do teto antes disso, limitar novas pesquisas/coletas conforme a política
e apresentar alternativa de capacidade. Não apagar evidência sem arquivo verificado.

Definição de pronto:

- Lab seleciona biblioteca a partir de dados rastreáveis e teste fora da amostra, sem sinais remotos.
- Bot executa a receita compatível, com dados e indicadores compartilhados e risco preservado.
- Número de receitas admitidas depende de hardware, séries únicas, warmup e budget da conexão.
- Frequência é a de operações executáveis do portfólio, com bloqueios reais simulados.
- Supabase tem orçamento medido, retenção, arquivo/restore e distribuição cacheável.
- Atualização, falha e rollback não duplicam ordens, não perdem reservas e não armam trading.
- Limitações estatísticas, operacionais e externas são visíveis ao operador.

**Não é critério de pronto:** chegar obrigatoriamente a 30 estratégias, garantir taxa de acerto,
operar a qualquer custo ou aumentar limites financeiros para melhorar a frequência.

## 8. Registro desta elaboração

- Realizado: inspeção local de código/documentação/manifestos e desenho da sequência de tarefas;
  consulta às páginas oficiais do Supabase citadas para limites e cuidados operacionais.
- Não realizado nesta elaboração: edição de lógica, migração, deploy, conexão financeira,
  exclusão, benchmark novo, build novo ou execução integral das suítes.
- Medições numéricas derivadas do catálogo/grade são diagnósticos locais da base citada;
  não constituem prova de rentabilidade nem inventário remoto do Supabase.
- Este documento é o entregável de planejamento. Os prompts só passam a implementados
  quando seus próprios diffs, testes e WORKLOG registrarem a execução e o resultado.

## 9. Comandos-base de validação para os prompts de implementação

Confirmar primeiro os comandos e versões nos arquivos atuais de cada produto. Os exemplos
abaixo não autorizam instalar dependências nem reutilizar o ambiente de outro projeto.

BOT, no diretório raiz de trading-lab-desktop, PowerShell:

~~~powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check apps packages tests
.\.venv\Scripts\python.exe -m ruff format --check apps packages tests
.\.venv\Scripts\python.exe -m mypy apps packages
.\.venv\Scripts\python.exe -m compileall apps packages
git diff --check
~~~

LAB, no diretório strategy-lab, com seu próprio ambiente:

~~~powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy --strict
git diff --check
~~~

Usar alvos/exclusões configurados no pyproject do LAB; não incluir o vendor como código
autoral nem alterá-lo para satisfazer lint. Se o mypy não declarar alvos em configuração,
usar os alvos autorais prescritos pelo projeto, registrando o comando completo.
No HUB, usar as tasks Deno reais do projeto (check/test/fmt) e a suíte staging com ref guard.
Se não houver tasks, documentar os comandos explícitos equivalentes antes de acrescentá-las.
Scanner de segredo/release existente também é obrigatório antes de artefatos/publicação.
Não considerar teste staging pulado como prova de RLS, migração ou restore.
