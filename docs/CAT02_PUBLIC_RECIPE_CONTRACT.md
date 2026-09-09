# CAT-02 — Contrato público de receita, evidência e capacidades

Data: **2026-09-05**
Produtos: Strategy Lab + Trading Lab Desktop
Resultado: **IMPLEMENTADO LOCALMENTE; publicação remota não executada**

## Resultado

Foi criada a revisão aditiva `schema_revision="1.2"` do manifesto. Ela materializa o contrato
`tl.candle-close.v2` definido no CAT-00, sem alterar famílias, fórmulas, parâmetros, paridade dos
primitivos, IDs persistidos, risco ou execução financeira.

O publicador do Lab continua emitindo `1.1`. O bot entende e valida a estrutura `1.2`, mas sua
capability padrão declara somente `legacy.bot.window-replay.v1`; por isso recusa `1.2` com
`MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED`. Essa barreira impede executar uma receita v2 no engine
legado antes de CAT-08/CAT-09.

## Campos aditivos da revisão 1.2

Na raiz:

| Campo | Contrato |
|---|---|
| `execution_semantics_version` | exatamente `tl.candle-close.v2` |
| `dataset_evidence` | id, SHA-256, origem real/sintética, intervalo UTC e cobertura Decimal |
| `telemetry` | schema v1, suporte declarado e `opt_in_required=true` |

Em cada estratégia:

| Campo | Contrato |
|---|---|
| `recipe_revision` | inteiro positivo; a chave persistida não muda |
| `recipe_fingerprint` | SHA-256 canônico dos campos que mudam comportamento |
| `composition` | um REGIME, um TRIGGER e um CONFIRM exatos da família F1..F5 |
| `capabilities.product` | `binary_option` |
| `capabilities.tick_volume` | derivado da composição; obrigatório somente para F4 atual |

`timeframe` e `warmup_required` existentes são reaproveitados como requisitos de capacidade; não
há cópia paralela. `research_run_id` existente permanece a identidade do run. As métricas medidas
continuam em `validated`, em string Decimal.

O fingerprint da receita cobre `family`, `asset`, `timeframe`, `hours_utc`, `params`, composição,
capacidades e warmup. Nome de exibição, status, gestão e estatísticas não mudam identidade
matemática. Alterar qualquer campo coberto exige novo fingerprint e revisão governada; a checagem
entre versões será adicionada na publicação transacional do CAT-13.

## Segurança e evidência

- `synthetic` pode existir para teste/observação, mas `approved` é rejeitado com
  `MANIFEST_SYNTHETIC_APPROVAL`.
- `demo_only` continua exclusivamente local e não foi acrescentado ao contrato remoto.
- Status remotos continuam `approved`, `observation` e `rejected`.
- Telemetria exige opt-in local. O vetor permite somente `client_id`, estratégia/revisão,
  manifesto, timestamp, resultado e payout; proíbe account id, e-mail, senha, token, credencial e
  comando de ordem.
- O manifesto não contém código arbitrário: composição e parâmetros são allowlists fechadas.
- Paridade pública dos primitivos permanece
  `sha256:f3d4285fc5aa7d7801a565cbee815d70034049c7a963ec137a8fa07da18eae10`; não foi regenerada.

## Rejeições de capability

O Lab e o bot possuem implementações independentes do mesmo contrato. A avaliação termina antes de
qualquer estratégia se tornar executável quando ocorrer:

- `MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED`;
- `MANIFEST_PRODUCT_UNSUPPORTED`;
- `MANIFEST_TIMEFRAME_UNSUPPORTED`;
- `MANIFEST_WARMUP_CAPACITY_EXCEEDED`;
- `MANIFEST_TICK_VOLUME_UNAVAILABLE`;
- `MANIFEST_CAPABILITIES_MISSING`.

Estratégias `rejected` não exigem recurso de execução. Manifestos históricos sem semântica nova
continuam no caminho legado já existente.

## Vetores públicos

`strategy-lab/contracts/strategy_contract_vectors.v2.json` contém hashes próprios e é executado
pelas duas suítes sem import cruzado. Ele cobre:

- manifestos v1.2 reais e sintéticos;
- composição exata F1..F5 e volume derivado;
- fingerprint de receita e alteração hostil;
- capability de semântica, timeframe, warmup e volume;
- outputs de consenso, desacordo e gate de regime;
- bootstrap pronto, incompleto, com gap e identidade divergente;
- liquidação CALL/PUT e empate como perda;
- allowlist/denylist de telemetria.

## Compatibilidade e rollout

| Componente | Histórico sem revision | 1.1 | 1.2 |
|---|---|---|---|
| Modelo/schema Lab 1.2.0 | aceita sem preencher defaults nos bytes | aceita | aceita/valida |
| Bot atual | aceita pelo contrato histórico | aceita | entende, mas recusa execução v2 por capability |
| Builder do Lab | lê histórico | **continua emitindo** | não emite ainda |
| Hub Deno atual | comportamento histórico | aceita | ainda não habilitado; CAT-13 |

Ordem obrigatória:

1. leitor/contrato v1.2 — esta etapa;
2. dataset e replay v2 — CAT-03/CAT-04;
3. engine incremental e paridade — CAT-08/CAT-09;
4. capability v2 habilitada no bot em shadow — após provas;
5. Hub/publicador v1.2 transacional — CAT-13;
6. publicação staging e rollout Practice controlado — fases posteriores.

Nenhum artefato histórico assinado foi reescrito. O schema JSON mantém `schema_version=1`, recebe
a revisão aditiva 1.2 e permanece sincronizado com o Pydantic. `tl-manifest-schema` passou de 1.1.0
para 1.2.0.

## Validação executada

- suíte de contrato do Lab: **140 passed**;
- contratos focados do bot: **83 passed**;
- vetor CAT-02 isolado do Lab: **16 passed**;
- suíte integral do Lab: **328 passed, 3 skipped** (somente staging sem URL);
- suíte integral do Desktop: **1246 passed, 3 failed, 4 skipped**; as três falhas são as mesmas
  reproduzidas no baseline CAT-00 e não pertencem aos arquivos do CAT-02;
- Lab: Ruff check aprovado, mypy strict aprovado em 81 arquivos e pip check aprovado;
- Desktop: mypy aprovado em 304 arquivos, compileall e pip check aprovados; Ruff permanece com
  os 24 diagnósticos anteriores e format-check com os mesmos 7 arquivos anteriores;
- scanner de segredos e `git diff --check`: aprovados;
- schema exportado e modelo/oráculo independentes concordaram nos casos 1.2;
- manifesto 1.2 foi recusado pelo runtime padrão enquanto o engine v2 não existe;
- nenhuma chamada Supabase, publicação, corretora ou ordem foi realizada.

O `pytest` do Windows emitiu um `PermissionError` apenas no callback de limpeza do diretório
temporário, depois de retornar exit code 0 nas suítes aprovadas. Erros históricos do baseline
CAT-00 não podem ser ocultados por este conjunto focado.
