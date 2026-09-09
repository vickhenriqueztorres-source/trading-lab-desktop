# CAT-12 — Seleção offline por qualidade e complementaridade

Data: 2026-09-06.

Produto alterado: somente `strategy-lab/`, no núcleo offline de pesquisa.

Escopo: composição e avaliação simulada de uma biblioteca. Nenhum broker foi acessado, nenhuma
ordem foi enviada, nenhum manifesto foi assinado/publicado e nenhum dado Real foi usado.

## Objetivo

Escolher uma biblioteca entre receitas que já passaram pelos portões individuais, medindo a
contribuição marginal no replay executável do CAT-11. O algoritmo não soma sinais brutos, não
premia vitórias recentes e não relaxa payout, amostra ou critérios para atingir uma quantidade
predeterminada.

Uma biblioteca menor — ou vazia — é um resultado válido.

## Implementação

Arquivo principal:

- `strategy-lab/tools/strategy_lab/research/portfolio_selection.py`

Contratos:

- `DevelopmentRecipe`: recebe veredito individual, referência de evidência, robustez e somente as
  oportunidades da partição de desenvolvimento;
- `PortfolioSelectionConfig`: congela seed, snapshot, limites, budgets e critérios do holdout;
- `select_portfolio(...)`: seleção gulosa determinística por ganho marginal executável;
- `FrozenPortfolioSelection`: seleção imutável, hashes, tentativas, exclusões e comparações de
  tamanho;
- `HoldoutLedger` + `evaluate_frozen_holdout(...)`: abertura única do snapshot de holdout;
- `PortfolioManifestDraft`: rascunho local, sem assinatura e com
  `publish_automatically=false`;
- `save_selection_artifacts(...)`: grava somente `portfolio_selection.md` e
  `manifest_draft.json` em diretório local explícito.

O teste de fronteira de imports impede dependência do publisher, Supabase, coleta ou broker.

## Algoritmo e desempate

1. Rejeitar antes da composição toda receita sem aprovação individual.
2. Em cada rodada, acrescentar temporariamente cada receita restante e executar o simulador de
   portfólio do CAT-11 com `compute_marginal=False`.
3. Rejeitar adições que excedam sobreposição pareada, concentração por ativo, concentração por
   hora UTC ou ganho marginal mínimo.
4. Ordenar as adições viáveis por:
   - maior número de novas operações executáveis;
   - maior robustez individual previamente calculada;
   - menor sobreposição de eventos;
   - `recipe_key` em ordem lexical, como desempate final estável.
5. Recusar a mistura de pesquisa sintética com histórico real na mesma seleção.
6. Congelar chaves, referências de evidência, configuração e `selection_hash` antes de abrir o
   holdout.
7. Exigir o intervalo temporal explícito do holdout e abrir cada `holdout_snapshot_id` uma única
   vez. Falha resulta em rejeição; o mesmo snapshot não
   pode ser reaberto para ajustar parâmetros.

A sobreposição usa o conjunto `(asset, signal_ts)`: várias receitas dependentes do mesmo evento
não viram observações independentes. Resultado de vários clientes não entra nesse cálculo.

## Budgets predefinidos

Defaults da versão `tl.portfolio-selection.v1`:

| Controle | Valor |
|---|---:|
| Receitas máximas | 100 |
| Tamanhos comparados | 10 / 20 / 30 / 50 / 100 |
| Sobreposição pareada máxima | 0,60 |
| Concentração máxima por ativo | 0,75 |
| Concentração máxima por hora UTC | 0,60 |
| Ganho marginal mínimo | 1 operação |
| Portfólios tentados | 6.000 |
| Reserva do budget para comparações | 5 |
| Amostra mínima no holdout de portfólio | 100 operações |
| Wilson inferior mínimo no holdout | 0,50 |
| EV mínimo por stake no holdout | 0 |

Os limites fazem parte do `config_hash`. O primeiro componente serve como bootstrap; limites de
concentração passam a ser aplicados quando uma segunda receita é proposta. Isso permite medir uma
receita isolada sem declarar que uma biblioteca concentrada foi aprovada.

## Métricas produzidas

- operações executáveis e frequência por dia;
- intervalo Wilson 95%;
- payout médio observado e payout de equilíbrio;
- EV estimado por stake;
- sensibilidade a −0,5 pp e −1,0 pp na taxa de acerto;
- drawdown máximo e pior sequência de perdas;
- concentração e sobreposição;
- número de portfólios tentados e oportunidades processadas;
- hashes reprodutíveis de configuração, seleção e replay.

Pesquisa sintética, histórico real e validação externa aparecem como proveniências distintas. O
texto do relatório afirma expressamente que evidência histórica não garante resultado futuro.

## Evidência de capacidade

O teste automatizado diário compara os alvos 10/20/30/50/100 com uma biblioteca de 12 receitas e
prova que alvos indisponíveis reportam o tamanho real, sem inventar receitas.

Também foi executado um ensaio focado com 100 receitas independentes:

- resultado: **9 testes aprovados**;
- comparação final: 100 receitas / 100 operações executáveis;
- tempo observado no ambiente Windows: **141,61 s**.

O custo confirma que a seleção é adequada para o LAB offline, não para o ciclo quente do cliente.
A otimização/benchmark sistemático permanece no CAT-19; o ensaio de 100 não fica na suíte diária
para não adicionar mais de dois minutos a cada regressão.

## Limitações honestas

- Nenhum dataset real foi aberto nesta implementação; os testes são determinísticos e sintéticos.
- Nenhum holdout real foi consumido.
- Não há alegação de que já existem 10, 30 ou 100 estratégias aprovadas.
- Persistência durável da queima de ranges continua sendo responsabilidade do contrato CAT-05;
  `HoldoutLedger` é uma segunda barreira local do processo.
- A assinatura, publicação transacional e rollback pertencem ao CAT-13.
