# CAT-15 — Outcomes mínimos, privados e limitados

Data: 2026-09-06. Produto alterado: Strategy Lab Hub/Supabase.

## Resultado

O endpoint `outcomes` preserva o payload legado v1 e aceita um contrato v2 explícito. O v2
identifica a receita e sua revisão, manifesto e engines, ativo/timeframe/produto, ambiente da
conta, origem, grupo do sinal e evento terminal UUID. `client_id` sempre vem do JWT verificado;
um valor enviado no corpo nunca substitui a identidade autenticada.

O Hub não recebe conta da corretora, login, e-mail, saldo, token, senha, dispositivo, IP persistido
ou outra PII. Campos extras são recusados, inclusive nomes típicos de conta.

## Contratos

O v1 continua aceito pela Edge Function para clientes antigos:

```json
[{"client_id":"ignorado","strategy_key":"f1","ts":1788350460,"won":true,"payout_pct":"87.00"}]
```

O v2 usa um envelope versionado:

```json
{
  "schema_version": 2,
  "outcomes": [{
    "event_id": "018f81d6-25d4-4f3f-8e1d-294f5bcdef02",
    "strategy_key": "f1_reversal:EURUSD-OTC:M1:00-06",
    "recipe_revision": 2,
    "manifest_version": 14,
    "execution_semantics_version": "tl.candle-close.v2",
    "primitives_version": "1.0.0",
    "asset": "EURUSD-OTC",
    "timeframe_s": 60,
    "product": "turbo",
    "account_environment": "practice",
    "source": "desktop_bot",
    "signal_group_id": "sha256:<64 hex>",
    "ts": 1788350460,
    "won": true,
    "payout_pct": "87.00"
  }]
}
```

Payout permanece string decimal e é validado sem conversão por ponto flutuante. Timestamp precisa
estar na grade M1, não pode estar no futuro e não pode ter mais de sete dias. O lote deve conter
1–500 eventos e o corpo completo não pode exceder 256 KiB.

## Idempotência e dependência estatística

- `(client_id, event_id)` impede retry duplicado do mesmo terminal.
- `(client_id, strategy_key, recipe_revision, signal_group_id)` impede o mesmo cliente de inflar
  um sinal trocando apenas o UUID do evento.
- A inserção e a atualização do agregado ocorrem em uma RPC transacional.
- Relatos de clientes diferentes sobre o mesmo `signal_group_id` aumentam `client_reports`, não o
  número de sinais independentes. Divergência entre win/loss fica visível.
- Payload bruto não é armazenado. Somente colunas normalizadas mínimas permanecem no banco.
- Eventos normalizados expiram em 90 dias; agregados, em 400 dias.

Esses dados servem monitoramento e detecção de divergência. Não existe trigger, RPC ou permissão
que promova estratégia, altere manifesto ou marque pesquisa como aprovada. Aprovação continua
dependendo do pipeline qualificado do Strategy Lab.

## Autenticação, RLS e quotas

O token novo carrega o ambiente do Hub (`staging` ou `production`). Token antigo, sem esse claim,
continua válido apenas para v1. Assinatura HS256, algoritmo/header, expiração, UUID e ambiente são
verificados pela própria função.

Inserção anônima direta em `live_outcomes` foi revogada. As tabelas v2 e de agregados não têm grant
nem policy para `anon` ou `authenticated`; as RPCs de ingestão e orçamento são exclusivas do
`service_role` usado internamente pela Edge Function.

Quotas atômicas:

- outcomes: 600 requests/h global e 60 requests/h por cliente;
- eventos: 5.000/dia global e 500/dia por cliente;
- emissão de token: 60 requests/h global;
- UUID rotativo não contorna os limites globais;
- falha em qualquer cota reverte todos os débitos daquela tentativa.

## Custo estimado

Chamadas só acontecem para lote não vazio. Para `C` clientes, `O` outcomes/dia por cliente e média
`B` por lote, a estimativa mensal é `30 × C × teto(O/B)` chamadas.

| Cenário | Hipótese | Chamadas/mês | Eventos/mês |
|---|---:|---:|---:|
| Piloto | 100 clientes, 10/dia, lote 10 | 3.000 | 30.000 |
| Médio | 1.000 clientes, 20/dia, lote 5 | 120.000 | 600.000 solicitados* |
| Teto do Hub | orçamento global sustentado | até 432.000 | até 150.000 aceitos |

\* O cenário médio excede o orçamento diário de eventos e sofrerá `429`; exige aumentar capacidade
e orçamento conscientemente, não apenas criar UUIDs. O teto de 150.000 eventos/mês mantém o uso
previsível; o tamanho real em bytes deve ser acompanhado antes de qualquer aumento.

## Evidência

As migrations `0010` e `0011` foram aplicadas ao projeto Supabase staging vinculado. Smoke remoto
Practice enviou um evento v2: primeira tentativa `inserted=1`; retry idêntico `inserted=0` e
`duplicates=1`. O payload legado v1 também foi aceito pela Edge Function. Os registros e
agregados sintéticos foram removidos em seguida.

Consulta remota confirmou: inserção direta anon v1 = false, inserção direta anon v2 = false,
leitura anon de agregado = false e execução anon da RPC de orçamento = false.

Nenhuma corretora, conta financeira, ordem ou ambiente Real foi acessado.
