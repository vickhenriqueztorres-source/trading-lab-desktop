# Manifest acceptance contract v1 — R-MAN-1..7

Artefatos públicos, sem dados de conta nem segredos de produção. Lab e bot mantêm código isolado
e executam os mesmos vetores. Não importar `manifest_schema` no bot.

## Camadas obrigatórias

1. Leitura UTF-8 de JSON limitada a 4 MiB e 32 níveis. Rejeitar nomes duplicados, NaN/Infinity,
   tokens float/exponenciais (inclusive 1.0), UTF-8/surrogates inválidos e inteiros fora de
   ±(2^53−1). Não é suficiente fazer JSON.parse e perder informação lexical.
2. Draft 2020-12 + perfil semântico **urn:strategy-lab:manifest-policy:v1**.
3. Ed25519 com key_id A/B da trust store configurada; nenhuma chave fornecida dentro do
   manifesto é confiável. O exemplo usa a chave de teste que produção deve rejeitar.
4. Compatibilidade primitives_version/hash. No consumidor: antirrollback/expiração/cache (P09).

O export tem `x-tl-policy-v1`; um consumidor sem suporte deve abortar, não ignorar o keyword.
O JSON Schema é estrutural, não um mecanismo de assinatura. Assinatura inválida pode acompanhar
uma estrutura válida. O schema não comprova procedência, aprovação estatística ou permissão de trade.

## Perfil semântico normativo

- `x-tl-decimal-range`: interpretar min/max/step e instância como decimais exatos; exigir min ≤
  valor ≤ max e (valor−min)/step integral. kind=int exige valor integral. Sem float intermediário.
- `x-tl-ordered-params`: para cada [a,b], exigir Decimal(params[a]) < Decimal(params[b]).
- Root: 0 < expires_at−published_at ≤ 3888000; keys de estratégias únicas.
- hours_utc: dois inteiros e 0 ≤ início < fim ≤ 24.
- Probabilidades p_hat/wilson_lower/p_min_at_validation em [0,1]; wilson_lower ≤ p_hat.
- ops_per_day ≥ 0, worst_streak ≤ n, janelas aprovadas ≤ total e total > 0.
- payout_min em (0,1], grade 0.01, Wilson ≥ 1/(1+payout_min)+0.015; passo anterior não passa.
  Divisão Decimal com precisão 28, ROUND_HALF_EVEN. Não recalcular estatística experimental.
- stake_pct em (0,100]; etapas 0..10; são apenas metadados, não overrides do Risk Ledger.
- rejected exige reason_pt não vazio; approved exige holdout_passed=true.
- Strings decimais: regex ASCII com correspondência da string inteira, até 24 caracteres;
  nunca aceitar newline final por diferenças de regex entre motores.

## Canonicalização e hashes

JSON canônico usa sort_keys=True, separators=(",",":"), ensure_ascii=False (UTF-8).
Ordenação por pontos de código Unicode; não normalizar Unicode ou a representação dos decimais.
Excluir apenas signature na raiz antes de assinar. Sem preencher campos opcionais ausentes;
null é assinado quando explicitamente presente. Assinatura = ed25519: + base64 padrão com padding,
64 bytes; chave pública raw = 32 bytes.

Cada vetor contém documento ou raw_json, expected reason_code, accepted, schema_valid e SHA-256.
Seu hash exclui somente seu próprio sha256; o hash do envelope exclui somente o sha256 da raiz,
mantendo hashes e assinaturas internos. O próprio arquivo pode ter pretty-print diferente.
As chaves do envelope são públicas exclusivamente para testes, nunca para distribuição de confiança.

`schema_valid` significa estrutura + perfil semântico, antes de assinatura/compatibilidade.
Casos de JSON inválido falham antes do schema. A suíte compara independentemente Pydantic e
jsonschema+perfil, e testa separadamente assinatura/compatibilidade pelo pipeline de ingestão.

## Limite desta etapa

Nenhuma função Supabase foi instalada. O adaptador Deno deverá implementar integralmente este
perfil e passar os vetores antes do deploy; apenas carregar o JSON Schema não satisfaz R-HUB-3.
O bot deverá portar o contrato sem import cruzado na etapa P09.

Referências técnicas:
[JSON Schema — vocabulários](https://json-schema.org/understanding-json-schema/reference/schema),
[extensão de validadores jsonschema](https://python-jsonschema.readthedocs.io/en/stable/creating/),
[Ed25519 cryptography](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).
