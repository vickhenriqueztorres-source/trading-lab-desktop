# Inicialização da coleta real — 2026-09-08

Requisitos: R-COL-1, R-COL-2, R-COL-10, R-COL-13, R-VEND-3, R-ISO-3.

O coletor real exigia o canário sintético de CI (cinco preços de 2023). Essa referência
nunca foi uma gravação da IQ Option. O novo caminho usa um arquivo público de preços
gravado pelo recorder, separado da fixture e dos testes protegidos. O arquivo de CI e
o teste `test_canary_fixture_matches` permanecem intactos.

## Configuração local

Execute os comandos no diretório `strategy-lab/`, no seu próprio terminal interativo:

```powershell
.\scripts\configure_collection.ps1
```

O script chama o Python do ambiente exclusivo do Lab. O e-mail é solicitado no terminal;
a senha tem entrada oculta. No Windows, o backend é fixado em `WinVaultKeyring`, sem
seleção automática de backend de arquivo/texto. Se a ocultação falhar ou a entrada vier de
pipe, o cadastro aborta. O destino continua `StrategyLab/IQOption/collection`.
O script não lê nem transfere o vault do EXE. O comando de cadastro não autentica na corretora.

O `IQClient` aplica também um orçamento persistente e atômico de **duas tentativas de login por
dia UTC**, compartilhado entre processos e reinícios em `state/iq-login-budget.json`. A reserva
ocorre antes da autenticação de rede; estado corrompido, relógio regressivo ou contenção do lock
falham fechados. Fakes e testes injetados não consomem esse orçamento.

Na VPS, o carregador existente aceita `IQ_EMAIL`/`IQ_PASSWORD` somente com
`STRATEGY_LAB_ENV=vps`, via injeção de segredos. No Windows, essas variáveis não são fallback.

Configure também `SUPABASE_DB_URL` no processo de coleta por seu mecanismo de segredos.
É uma URI `postgresql://`, obtida em Connect no projeto Supabase; a URL HTTPS do projeto
e a sessão salva da CLI não substituem essa conexão do adaptador `PostgresRepository`.
Nunca cole a URI contendo senha em logs, documentação ou arquivo versionado.

Para uma execução local com o projeto já vinculado pela Supabase CLI, use o inicializador:

```powershell
.\scripts\run_linked_collection.ps1 -Assets EURUSD-OTC
```

Ele lê apenas host/porta/usuário do arquivo `pooler-url` gerado pela CLI e solicita a senha do
banco com entrada oculta. A URI é montada somente na memória, herdada pelo processo Python e
removida no `finally`; senha e URI não entram em argumento, arquivo ou histórico do shell.
Na VPS, continue usando `SUPABASE_DB_URL` pelo gerenciador de segredos da infraestrutura.

Diagnóstico local (não consulta a rede nem comprova autenticação):

```powershell
.\.venv\Scripts\python.exe -m strategy_lab.cli collection-preflight
```

A saída lista todos os blockers: credencial ausente, configuração PostgreSQL ausente/inválida
e referência real ausente/inválida. `local_prerequisites_ready` significa apenas configuração
local válida. NTP, conexão, capacidades e igualdade de preços só serão comprovados no uso externo.

## Primeira referência de preços

Use um ativo realmente disponível na conta de coleta e cinco velas M1 históricas fechadas.
Exemplo parametrizado, sem expor credenciais:

```powershell
# Preencha a data UTC e o ativo escolhidos antes de executar; intervalo de cinco minutos.
$canaryAsset = Read-Host 'Ativo canônico (OTC e spot são diferentes)'
$canaryFrom = Read-Host 'Início UTC das cinco velas, ISO-8601 com Z'
.\.venv\Scripts\python.exe -m strategy_lab.cli record-canary --asset $canaryAsset --from $canaryFrom
```

O comando verifica NTP, reutiliza `record_fixture`, solicita cinco velas, fecha a sessão e
grava `state/collection-canary.json` apenas se o lote inteiro for válido. Só OHLC, timestamps,
volume e proveniência pública entram no arquivo. Arquivo existente nunca é sobrescrito.
Não existem chamadas financeiras ou escritas Supabase nesse comando. Revise os cinco preços
contra a fonte e preserve o hash; ele detecta corrupção local, não autentica o broker por si só.

## Coleta persistente

```powershell
.\.venv\Scripts\python.exe -m strategy_lab.cli collect --assets EURUSD --from 1788811200
```

O ativo e o início acima são somente um exemplo de sintaxe, não declaração de disponibilidade
do broker. Escolha o intervalo desejado e mantenha OTC/spot separados. `--canary-file` permite
usar outra referência explicitamente revisada. Toda referência precisa ter exatamente cinco
velas, schema estrito, hash válido, vendor atual e intervalo fechado no momento da gravação.
Nenhum login ocorre se a referência for localmente inválida. Após o login, cada uma das cinco
velas é comparada integralmente (timestamp/OHLC/volume); divergência aborta antes de payout/DB.
Uma referência fora da retenção histórica do broker pode precisar ser gravada novamente em
outro arquivo; falha não recalibra ou substitui o canário automaticamente.

`collect --dry-run` continua sendo um ensaio sintético com fakes. Não mede preços externos,
não prepara aprovação de pesquisa e não grava dados reais. Não relaxamos gates de pesquisa,
holdout, payout, controles de risco nem paridade numérica nesta etapa.

## Limites da entrega

Em 2026-09-08 foi gravada e reconsultada uma referência real de cinco velas fechadas de
`EURUSD-OTC`; autenticação, catálogo, janela temporal e igualdade integral foram comprovados.
Isso valida o canário, não uma estratégia. A amostra inicial abaixo ainda não oferece histórico
temporal de payout suficiente para uma comparação quantitativa ou taxa de acerto/frequência válida.

A primeira coleta persistente gravou 995 velas reais e uma observação de payout `0.82`. O catálogo
publicou para `EURUSD-OTC` sessões diárias 00:00–08:00 e 08:30–24:00 UTC; a migration
`0019_eurusd_otc_schedule_20260908.sql` registrou somente esse ativo. A cobertura M1 do intervalo
coletado ficou em 970/970 (`1.000000`), com zero gap dentro de sessão. O gap 08:05–08:10 ficou
corretamente fora da sessão. As 25 velas recebidas durante a pausa não entram na grade elegível.

A observação de payout foi recebida depois da última vela do intervalo. Por causalidade
point-in-time, ela não pode ser aplicada retroativamente: esta amostra ainda não autoriza replay,
ranking, aprovação ou publicação de estratégia. É necessário acumular observações em coletas
futuras, respeitando o limite diário de login.

O runner agora valida todos os ativos/lotes em um buffer limitado a 100.000 registros antes
de escrever no backend. Orçamento esgotado aborta explicitamente; não há truncamento silencioso.
As invariantes de série cobrem os lotes coletados, inclusive suas fronteiras, para qualquer
repositório. Suspeita de salto invalida a coleta antes de persistir. Velas, gaps, observações de
payout e relatório final são aplicados na mesma transação PostgreSQL. Exceção no commit desfaz
todas as alterações. Payout usa o timestamp de recebimento, inclusive se a hora mudou durante
login/coleta. A conexão da transação pertence a um job; não compartilhar essa instância entre
threads/jobs concorrentes.

Limites restantes: as invariantes desta etapa não incluem as últimas velas de runs anteriores
no cálculo do ATR da primeira janela do novo run. Uma coleta grande pode exigir revisar o orçamento.
O `status` legado usa `FakeRepository`; o novo preflight declara seu escopo local e não substitui
um inventário remoto. A transação real está implementada com o contexto do driver psycopg,
e o UPSERT de até 1.000 velas usa um único lote `unnest` parametrizado. Idempotência e constraint
foram executadas no staging real; o teste usa ativo aleatório e rollback obrigatório para não
contaminar a pesquisa. A leitura RLS anônima continua pendente por exigir
`SUPABASE_STAGING_ANON_DB_URL`.
