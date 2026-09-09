# Catálogo dinâmico IQ Option — Binary, Digital, regular e OTC

Data: 2026-09-09 (BRT)

## Problema corrigido

O conector e o radar dependiam de uma tabela local curta de símbolos e IDs. Essa tabela não era
uma fotografia confiável da conta/sessão: a IQ Option pode expor centenas de ativos, alterar IDs,
separar produtos e mudar a disponibilidade durante o dia. Além disso, o sufixo `-OTC` não pode ser
normalizado para o mercado regular.

## Fonte de verdade implementada

O worker isolado consulta, sem mensagem financeira:

1. `get-initialization-data` v3 para `binary` e `turbo`;
2. `get-underlying-list` v2, com `type=digital-option`, para Digital.

Cada observação vira `BrokerInstrument`, com identidade exata, produto (`BINARY`, `TURBO` ou
`DIGITAL`), mercado (`REGULAR` ou `OTC`), estado (`OPEN`, `CLOSED`, `SUSPENDED`, `DISABLED` ou
`UNKNOWN`) e capacidades separadas (`detectable`, `analyzable`, `quotable`, `executable`). O mapa
dinâmico de IDs substitui atomicamente o bootstrap após o primeiro catálogo válido.

## Limite financeiro intencional

Digital é descoberto e exibido, mas permanece `detectable=true`, `analyzable=false`,
`quotable=false` e `executable=false`. A rota financeira atual é específica de Turbo/Binary M1.
Não existe conversão silenciosa de uma receita Binary para Digital. Habilitar Digital exigirá
cotação/instrumento, expiração, submissão, eventos e reconciliação específicos e testados.

Para operação automática atual, um ativo precisa simultaneamente:

- estar no catálogo assinado de estratégias;
- corresponder exatamente ao símbolo publicado (`EURUSD` é diferente de `EURUSD-OTC`);
- possuir produto `TURBO` aberto;
- ser analisável, cotável e executável na conta Practice;
- passar todos os gates existentes de payout, evidência, risco e uma ordem em voo.

## Atualização, orçamento e falha

- TTL do catálogo: 60 segundos;
- validade máxima sem refresh: 180 segundos;
- custo de refresh: duas mensagens read-only, previamente reservadas no orçamento operacional;
- falha/staleness: nenhuma substituição por IDs fixos no cliente de produção e nenhuma entrada;
- falha somente no endpoint Digital produz catálogo parcial explícito e não descarta evidência
  Binary/Turbo fresca; Digital continua bloqueado até responder;
- o Core emite `iqoption_instrument_catalog_refreshed` com contagens agregadas e
  `iqoption_instrument_catalog_failed` com reason code sanitizado.

O catálogo sanitizado e a projeção UI exigiram elevar o frame IPC local de 64 KiB para 1 MiB. O
limite continua explícito; nenhum payload bruto, credencial, cookie ou conta atravessa o contrato.

## Interface

O radar passa a mostrar o catálogo da sessão. O seletor deixa de ter pares escritos à mão:
recebe os ativos projetados pelo Core, habilita somente os executáveis e mantém Digital visível
como `SOMENTE DETECÇÃO`. A atualização periódica preserva RSI e sinal já calculados.

## Testes

Os testes acrescentados cobrem:

- descoberta conjunta Binary/Turbo/Digital;
- resposta global de ambos os endpoints, com e sem `request_id`;
- substituição do ID estático pelo ID observado;
- identidade OTC exata;
- Digital aberto, porém impossível de executar;
- suspensão Turbo excluída da rotação;
- dispatch IPC read-only;
- catálogo sanitizado de 510 instrumentos dentro do frame bounded;
- seletor da UI derivado do catálogo e Digital desabilitado para entrada.

Nenhuma chamada externa, login ou ordem faz parte desses testes automatizados.

## Validação final e artefato

- suíte integral final: 1.354 passed, 4 skipped;
- replay IQ AUTO de 24 h: 48 aceitações e 24 rejeições simuladas, 1.368 épocas sem envio,
  zero correlações duplicadas;
- Ruff check e format no escopo canônico de 521 arquivos: aprovado;
- mypy em 310 fontes, compileall, diff-check e scanner de segredos: aprovados;
- PyInstaller onedir: 547 arquivos, scanner, manifesto, integridade e health-check aprovados;
- portátil: ProductVersion 1.9.11, único recurso `TradingLab.payload.zip`, 961 entradas;
- health-check do portátil: exit 0 e nenhum processo `TradingLab` residual;
- SHA-256 portátil:
  `DB867300529E94B8AEA33DED3F7201F0F7AF344855933E2BF16B035858A735B4`;
- SHA-256 payload:
  `F4C13C0559DD052F9EFCF6A8B0FE89738793B1037300AE3BC31CD7A3898EF2C2`;
- SHA-256 do executável onedir:
  `DA9B19977C508D484A9B10C7DD5995A106816261EB38A1B49E470A8901F4B97F`.

Artefato final:
`dist/iq-dynamic-catalog-release-20260909-final/TradingLab-Desktop-v1.9.11-IQ-DYNAMIC-CATALOG.exe`.

O arquivo legado `docs/##  Arquitetura.py` contém Markdown apesar da extensão `.py`; por isso
`ruff .` tenta analisá-lo como Python e falha. Ele é conteúdo anterior fora do escopo e foi
preservado. O gate canônico de código (`apps packages tests build_scripts scripts`) passou.

Esta entrega não realizou login externo nem enviou ordem. Ela prova o contrato, isolamento,
seleção e empacotamento; a disponibilidade concreta da conta continua sendo evidência de runtime.

## Correção pós-smoke da interface

O primeiro EXE revelou dois defeitos que os fakes permissivos não reproduziam:

- a rota comunitária `get-underlying-list` era enviada com `request_id`, embora a implementação
  vendorizada envie essa consulta global sem correlação. Sessões atuais podem ignorar a forma
  correlacionada, resultando em `digital_count=0`;
- o radar mostrava todo o catálogo bruto (ações, índices e produtos legados), mesmo quando não
  existia receita assinada para esses símbolos.

A correção envia a consulta Digital na lane single-flight exclusiva sem `request_id`, concede um
prazo bounded independente a cada uma das duas rotas e guarda a resposta Digital durante a sessão.
Se a rota Digital não responder, a próxima tentativa recebe backoff de 15 minutos; os refreshes
Binary/Turbo continuam sem esperar novamente oito segundos a cada minuto. Reconexão autenticada
limpa o cache e permite uma nova tentativa.

O radar e o seletor agora são a interseção exata entre catálogo da sessão e assets do manifesto
assinado. Instrumentos sem estratégia continuam conhecidos pelo worker, mas não são apresentados
como candidatos operacionais. A mensagem `IQOPTION_CONNECTED_REARM_REQUIRED` também passou a
explicar ao operador que a reconexão exige novo clique em “Ligar Bot IQ Option”, preservando
R-SCOPE-005 em vez de rearmar automaticamente.

Evidência desta correção: 251 testes IQ/manifest e a suíte completa com 1.357 testes aprovados
(4 skips externos/plataforma), incluindo a sessão legada que ignora `get-underlying-list`
correlacionado, backoff do endpoint Digital e filtro do radar. Ruff, format, mypy, compileall e
diff-check passaram. O onedir final contém 547 arquivos e passou scanner, integridade e
health-check. Portátil final:

`dist/iq-catalog-hotfix-r2-20260909/TradingLab-Desktop-v1.9.11-IQ-CATALOG-HOTFIX-R2.exe`

SHA-256: `C0AEF935CCD9D4A85C50AEB909BB58DE78CD07000992E0D5D9167F0FFF505AD3`.

O R2 acrescenta fail-closed explícito para manifesto vazio: zero receitas assinadas implica zero
candidatos e nunca reabre o catálogo bruto. O executável anterior do hotfix foi supersedido.
