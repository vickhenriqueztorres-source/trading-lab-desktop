# Causa 3 — portões de execução do manifesto

Versão do produto: **1.9.11**. Implementação e validação local: 2026-09-03.

## Fatos verificados

O trader não chamava `DynamicManifestCatalog.is_eligible`; o lifecycle não criava
`LiveMonitor`; as notificações de ordens do catálogo não estavam no caminho de execução.
O worker externo não oferecia cotação de payout para o Core. O carregamento local do
catálogo também lia JSON diretamente, sem passar pelo validador de assinatura.

A afirmação de que observação já operava em Real **não foi comprovada**: motor e
conector possuíam barreiras Practice-only independentes. Essas barreiras foram mantidas.

## Plano aplicado

1. Usar `BROKER_QUOTE_REQUEST/RESPONSE`, já existentes, para uma leitura do payout
   turbo do ativo exato. Reutilizar sessão, transporte e orçamento de mensagens.
2. Chamar o portão do catálogo antes de consumir o sinal, validando também conta,
   prontidão do monitor, ativo e cotação. Revalidar dentro da serialização de conta,
   imediatamente antes da reserva/persistência. Nunca repetir envio financeiro.
3. Persistir o vínculo ordem–revisão numérica na mesma transação de intenção,
   reserva e outbox. Não transmitir esse metadado privado ao broker.
4. Consumir resultados persistidos em background, com estado SPRT e marcador de
   consumo na mesma transação. Isso cobre eventos, reconciliação e restart.
5. Restaurar rebaixamentos, preservar retiring, verificar expiração e assinatura,
   encerrar o monitor antes de fechar o banco. Testar falhas e duplicações.

## Contratos implementados

| Fronteira | Garantia |
|---|---|
| Payout | `get-initialization-data` v3; `turbo` do ativo exato; enabled e não suspended; `(100 - commission) / 100` com Decimal |
| Orçamento | Leitura no orçamento existente de mercado; sem subscrição nem nova autenticação dedicada; sem leitura de payout se orçamento esgotado |
| Validade | Janela máxima de 2 s, contada antes da requisição em relógio monotônico do Core; resposta lenta não ganha prazo extra |
| Contexto | Cliente worker idêntico; conta confirmada Demo/Practice; ativo exato; produto BINARY_OPTION e duração 1 minuto |
| Gate | Status, retiring, horário, publicação/expiração e payout com Wilson/offset existentes; nenhum limiar estatístico alterado |
| Core | Ticket de uso único; rechecagem sob serialização de conta; lock do catálogo impede remoção/rebaixamento concorrente entre validação e registro |
| Persistência | Migration 0008 aditiva; migrations 0001–0007 preservadas |
| SPRT | Vínculo contém hipóteses e hash do contexto validado; resultado antigo não entra na revisão nova |
| Idempotência | Estado estatístico e marcador de consumo atômicos; não depende da entrega única do callback |
| Ordem seguinte | Transação de admissão bloqueia se existe settlement ainda não consumido; não marca falha de banco e não envia ordem |
| Retiring | Ordens não terminais são registradas/restauradas; descarte só após confirmação terminal persistida |
| Falha de monitor | Ausência, erro ou heartbeat local vencido bloqueiam entradas de manifesto |
| Shutdown | Thread sinalizada e aguardada antes de fechar o writer; timeout de encerramento é erro explícito |
| Confiança | Lifecycle usa validador de schema, assinatura Ed25519, versão e hash de primitivos; chaves de teste não habilitadas nesse caminho |

Referência do protocolo comunitário (não é uma garantia do broker):
[api.py — get_api_option_init_all_v2](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/api.py)
e [stable_api.py — get_all_profit](https://github.com/iqoptionapi/iqoptionapi/blob/master/iqoptionapi/stable_api.py).
Não foram copiados os loops ilimitados, retentativas de login ou opções TLS do upstream.
Caso o servidor não responda com correlação/formato esperados, a entrada fica bloqueada.

## Decisões e limites explícitos

- O RSI local explícito `iqoption-rsi-demo` continua limitado a SINGLE/Practice. Exige
  payout válido e fresco, mas não inventa Wilson ou hipóteses SPRT para uma receita não
  validada. Não é convertido em estratégia aprovada do manifesto.
- Expiração bloqueia novas entradas e gera rebaixamento/evento no monitor; não impede
  processar ordens já abertas ou reconciliação.
- Payout é uma leitura temporal, **não** um preço reservado pelo broker. Não se promete
  que um endpoint não oficial mantenha o mesmo payout entre leitura e aceite.
- O monitor usa o SPRT já existente (incluindo decisões absorventes). Não foram alteradas
  fórmulas, thresholds, janelas, estratégias ou gestão financeira.
- Ordens históricas sem vínculo de manifesto não são atribuídas retroativamente a uma
  revisão presumida. O histórico financeiro permanece intacto. Estado SPRT legado com
  hipóteses correspondentes continua sendo restaurado; o novo vínculo é prospectivo.
- O uploader externo não é ativado: esta tarefa não autoriza transmitir resultados a terceiros.
- As raízes de confiança de produção existentes não foram substituídas por chaves
  inventadas ou de teste. Um manifesto sem assinatura verificável deixa de alimentar o
  catálogo operacional; isso pode expor uma configuração de publicação ainda incompleta.
- Nenhuma ordem externa, login, mudança de perfil do operador, alteração Supabase,
  build de EXE, commit ou push foi executado nesta etapa.

## Evidências de testes

`tests/integration/test_manifest_execution_gates.py` exercita o caminho real de
admissão/persistência com transporte simulado: payout ausente/baixo/não finito, retomada
do mesmo sinal após sanar o gate, ausência do monitor, mudança de worker, TTL, retirada,
Real, expiração, ticket único, gravação antes do broker, crash entre commits, rollback,
consumidores concorrentes, retiring, rebaixamento após restart, revisão nova, parada da
thread, erro de banco, rota IPC de cotação e parsing Decimal.

Primeiras execuções focadas: **86 aprovados**; execução ampliada de monitor/payout/
conector/SPRT: **66 aprovados** (conjuntos se sobrepõem; não somar).
Mypy: **303 arquivos sem erros**. Resultado final da suíte global e verificações:
registrado na entrada desta etapa do `WORKLOG.md` após conclusão dos comandos.

Fechamento: consolidação com lifecycle/conector **128 passed**; complemento final
monitor/gates **39 passed** (conjuntos sobrepostos). Mypy 303 arquivos, Ruff da etapa,
compileall, diff check e scanner aprovados. A suíte global encerrou com violação de
acesso nativa do Windows em teste de distribuição/scanner, antes do resumo final.
Ruff global mantém 24 diagnósticos e seis arquivos de formatação fora desta etapa.
Portanto, não há aprovação global de release.

Última execução consolidada, com todas as correções: **130 passed em 22,65 s**.

Não há evidência de cotação na IQ Option externa nesta etapa. Não confundir testes com
fakes com validação de disponibilidade, correlação ou payout do broker em produção.
