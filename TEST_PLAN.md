# Plano de Testes — DualTrade Desktop

**Status:** plano canônico da Fase 1  
**Objetivo:** demonstrar segurança operacional e recuperação; não medir rentabilidade

## 1. Princípios

- testes comuns são locais, determinísticos e sem rede;
- worker financeiro é simulado;
- integrações externas são separadas, marcadas e opt-in;
- conta real nunca é usada;
- ausência de resposta não é tratada como rejeição;
- falha crítica deve fechar o Health Gate;
- `UNKNOWN` e `SETTLEMENT_UNKNOWN` só mudam com evidência;
- toda mudança de ordem, risco, persistência, IPC ou tempo inclui teste de falha proporcional;
- fixtures não contêm segredo real nem payload de autenticação gravado;
- estratégia é testada por determinismo e proveniência, não por promessa de lucro.

## 2. Ambientes

### Local canônico

- Windows 10/11 64 bits;
- Python 3.13;
- SQLite temporário por teste;
- subprocessos locais;
- fake identity service;
- simulated worker e fake Deriv transport.

### Externo opt-in

O smoke público Deriv permanece skipado até `DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC=1` e usa o marker
`external_deriv_public`. O smoke demo read-only usa `external_deriv_demo`, só executa com
`DUALTRADE_RUN_EXTERNAL_DERIV_DEMO=1` e exige configuração demo explícita. Ambos permanecem fora da
suíte canônica; conta real é rejeitada antes da conexão. IQ Option não possui teste externo
executável.

### Proibido

- conta real;
- credencial real em fixture/comando/log;
- teste externo obrigatório para CI/local;
- depender de internet na suíte comum;
- alterar timeout para esconder deadlock/flake sem diagnóstico;
- apagar banco ou evidência após falha.

## 3. Comandos canônicos

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

Execução silenciosa/resumida:

```powershell
python -m pytest -q
```

Subconjuntos úteis:

```powershell
python -m pytest tests/unit
python -m pytest tests/contract
python -m pytest tests/integration
python -m pytest tests/replay
python -m pytest tests/chaos
```

Smoke público externo, somente quando solicitado:

```powershell
$env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC = "1"
python -m pytest tests/external/test_deriv_public_external.py -m external_deriv_public
Remove-Item Env:DUALTRADE_RUN_EXTERNAL_DERIV_PUBLIC
```

## 4. Pirâmide e diretórios

| Camada | Diretório | Prova principal |
|---|---|---|
| unidade | `tests/unit/` | validação, estados, limites, funções puras |
| contrato | `tests/contract/` | worker/adapter/IPC contra contrato interno |
| integração | `tests/integration/` | Core + SQLite + subprocesso + recovery |
| replay | `tests/replay/` | determinismo, journal e restore |
| caos | `tests/chaos/` | kill real antes/depois de commit |
| externo | `tests/external/` | smoke read-only explicitamente opt-in |

Há testes headless da UI e smoke automatizado da montagem da distribuição. O instalador Inno Setup
possui smoke real manual no Windows cobrindo instalação em diretório temporário, health-check do
binário instalado, startup/shutdown bounded da árvore local, desinstalação e ausência de processos
órfãos. A promoção para canal alpha/beta ainda requer automatizar esse smoke em VM Windows limpa.

## 5. Matriz mínima por área

| Área | Cenários obrigatórios |
|---|---|
| domínio | construção válida/inválida, serialização, UTC, dinheiro e IDs |
| ordem | transições, duplicidade, fora de ordem, terminal, deadline |
| outbox/dispatch | persist-before-send, claim, crash e entrega ambígua |
| Risk Ledger | concorrência, limites, moeda, restore e `UNKNOWN` |
| persistência | migration, checksum, corrupção, DB ausente, I/O e backup |
| IPC | framing, tamanho, JSON, versão, role, replay e processo morto |
| launcher | ordem de startup, lock, Job Object, safe shutdown, escala e órfãos |
| worker | handshake, timeout, crash, backoff, capability e shutdown |
| reconciliação | found/not-found/timeout/conflito/evidência/idempotência |
| eventos | aceite/open/settled, duplicado, gap e settlement conflict |
| identidade/licença | OTP/PKCE, rotação, reuso, dispositivo, assinatura e expiry |
| estratégia | manifesto/hash/status/entitlement, warm-up e determinismo |
| arbiter/allocator | opostos, iguais, expirados, orçamento e moeda |
| market data | schema, candle fechado, gap, stale, backpressure e scope |
| replay | equivalência, tamper, checkpoint e reprocessamento |
| shadow | `DECISION_ONLY`, `dispatch=False`, reconnect/backfill e kill |
| soak | limites, recursos, recovery, relatório redigido e retenção bounded |
| CLI de soak | opt-in, limites, exit codes, atomicidade, FIFO e subprocesso real |
| perfis/fault schedule | presets, ciclos determinísticos, recovery e resumo redigido |
| scanner de segredos | positivos sintéticos, workspace/fixtures e relatório de soak |
| restore drill | backup, marker, quick/full check, migrations, estado e original intacto |
| UI futura | projeção, modos, bloqueios, acessibilidade e safe close |
| release futuro | assinatura, adulteração, interrupção e rollback |

## 6. Cenários financeiros críticos

### Commit antes do dispatch

Provar que falha após intenção, após reserva ou após outbox não deixa subconjunto financeiro
confirmado. Somente a transação completa pode tornar o comando despachável.

### Timeout potencialmente aceito

Provar que:

- ordem vira `UNKNOWN`;
- outbox vira ambígua;
- reserva permanece ativa;
- novas entradas no escopo ficam bloqueadas;
- restart não repete a submissão;
- tempo decorrido não resolve o estado.

### Settlement desconhecido

Provar que a exposição permanece ativa e que eventos tardios/idempotentes ou reconciliação por
evidência podem resolver, sem regressão terminal.

### Licença/estratégia suspensa

Provar que novas entradas são bloqueadas, enquanto evento, reconciliação e liquidação de ordem
existente continuam.

## 7. Persistência e crash

Testes devem cobrir:

- kill antes/depois do commit SQLite;
- WAL ativo durante backup;
- migration nova e checksum divergente;
- rollback da migration falha;
- foreign keys e unique constraints;
- reader query-only;
- banco esperado ausente;
- corrupção detectada por `quick_check`/`integrity_check`;
- claim de outbox interrompido;
- checkpoint/journal antes e depois do commit de candle.

Kill tests usam subprocessos reais e arquivos temporários, nunca o perfil do usuário.

## 8. IPC e workers

Contract tests validam:

- `PROTOCOL_VERSION=1` e handshake compatível;
- frame máximo de 64 KiB;
- envelope completo e papéis corretos;
- payload desconhecido/inválido;
- message replay idempotente e conflito;
- deadline expirado antes do envio;
- disconnect/crash/hang/shutdown;
- filas bounded/backpressure;
- capability read-only e rejeição de operação Deriv de trading;
- processo filho encerrado após falha de startup/recovery.

## 9. Identidade e segurança

- nenhum `client_secret` no desktop;
- PKCE S256 e OTP simulados;
- token curto, refresh rotativo e detecção de reuso;
- device ID aleatório e prova Ed25519;
- lease assinada, adulterada, expirada, incompatível e revogada;
- DPAPI CurrentUser com e sem entropia e rejeição de entropia divergente;
- persistência/reopen, delete, clear, binding por chave e envelope corrompido/truncado do vault;
- seleção Windows, fallback explicitamente simulado e propagação de falha DPAPI/ACL sem fallback;
- handshake Auth IPC com token correto/incorreto e prova do servidor;
- login/OTP/renovação/autorização reduzida sem material persistente na resposta;
- kill real do Auth Agent, bloqueio exclusivo de entrada, settlement independente e restore DPAPI;
- lease expirada/adulterada via subprocesso e restart com novo handshake;
- escopo por usuário do vault simulado para testes não Windows;
- `SecretValue` redigido em `str`/`repr`;
- ausência de broker credential no identity service;
- scanner manual/automatizado no código, logs e artefatos quando existir.

## 9.1 Launcher e árvore de processos

- startup lógico Auth Agent → Core/recovery → Simulated → Deriv read-only;
- token lifecycle ausente/incorreto e prova HMAC inválida;
- segunda instância do Launcher rejeitada sem perturbar o dono;
- kill do worker financeiro degrada sem matar o Core nem reiniciar ordem;
- kill de Auth/Deriv read-only permite apenas restart bounded;
- kill do Core encerra todos os descendentes e libera locks pelo SO;
- kill abrupto do Launcher fecha o Job Object e não deixa órfãos;
- safe stop precede drain, workers, Auth e Core;
- timeout gracioso escala para terminate/kill;
- CLI com auto-shutdown opera somente em `tmp_path`/perfil explicitamente escolhido.

## 10. Estratégia, market data e replay

O mesmo código de estratégia deve produzir resultado equivalente em replay e shadow. Testes usam
candle fechado e ordenado, clock virtual/monotônico e IDs determinísticos. Devem rejeitar:

- candle aberto, duplicado, fora de ordem ou com gap;
- contexto/manifesto/hash/configuração divergente;
- strategy status/entitlement incompatível;
- sinal expirado;
- sinais opostos e stake somada;
- orçamento insuficiente/moeda divergente;
- journal/checkpoint adulterado;
- shadow divergente do replay.

Backtest não usa embaralhamento temporal. Resultado sintético não é evidência de rentabilidade.

## 11. Soak e recursos

Soak deve ter sempre dois freios: duração monotônica e máximo de ciclos. Também deve limitar
amostras, cenários, filas, recovery e recursos. Critérios podem incluir:

- ciclos mínimos;
- falhas máximas de poll/recovery;
- RSS do Core e processo filho;
- CPU/lag;
- estado final não degradado;
- shutdown do runner;
- relatório JSON sem payload bruto, credencial ou estado financeiro.

A matriz comparativa continua após falha para preservar evidência, mas o outcome agregado só passa
quando todos os cenários passam.

A CLI local deve provar adicionalmente:

- ausência de opt-in retorna código `2` sem iniciar cenário;
- argumentos fora dos limites retornam código `2` com reason estável;
- matriz aprovada retorna `0` e matriz reprovada ou falha operacional retorna `1`;
- falha antes de `os.replace` não deixa temporário órfão nem relatório parcial;
- retenção por quantidade e bytes é FIFO e não remove JSON não relacionado;
- symlink, escape de escopo e remoção bloqueada falham fechado;
- execução por subprocesso real produz quatro cenários locais e nenhum artefato financeiro;
- colisão de nome gera sufixo, preservando a evidência anterior.

Perfis e fault presets devem provar que todos os ciclos configurados aparecem como injetados e
observados/recuperados, que limites do runner cobrem a agenda sem se tornarem ilimitados e que o
JSON permanece livre de payload financeiro. O scanner usa positivos montados em runtime para não
gravar um segredo sintético completo no próprio repositório e varre código, fixtures e relatórios.

O restore drill usa somente `tmp_path`: gera backup pela API SQLite, torna o source fechado
temporariamente indisponível, copia o backup para outro perfil, cria `state.db.expected`, executa
`quick_check` e `integrity_check`, inicia outro Core e compara migrations/intenção/reserva/outbox/
ordem. O hash do source original deve permanecer idêntico após o ensaio.

## 12. Fixtures e dados

- use `tmp_path`/diretórios temporários;
- gere UUIDs/tokens sintéticos em runtime;
- use valores monetários pequenos em minor units e moeda explícita;
- mantenha payload externo mínimo e redigido;
- não copie resposta de autenticação real;
- não grave banco do perfil como fixture;
- preserve origem/timestamps/correlação necessários ao cenário;
- limite listas, batches e mensagens.

## 13. Política de flake

Um teste flakey é falha de confiabilidade. Quando ocorrer:

1. registre o caso e o ambiente;
2. execute isoladamente para obter contraste, sem declarar sucesso apenas por retry;
3. verifique processo órfão, porta, CPU, disco e timeout monotônico;
4. não aumente deadline global sem evidência;
5. mantenha a falha visível no `WORKLOG` quando afetar a validação;
6. adicione instrumentação redigida e correção determinística.

Testes Windows de subprocesso podem sofrer atraso sob carga, mas isso não autoriza mascarar
deadlock ou handshake incompleto.

## 14. Gate de merge/fatia

- testes diretamente afetados passam;
- `python -m pytest` passa ou toda falha é documentada e comprovadamente fora da fatia;
- Ruff, format check, mypy e compileall passam;
- scanner de segredos foi executado no diff/arquivos afetados;
- nenhuma conta real/segredo foi usado;
- documentação e rastreabilidade foram atualizadas;
- `WORKLOG.md` contém comandos/resultados reais;
- risco residual e próximo passo estão explícitos.

## 15. Gate de release futuro

Além do gate anterior:

- VM Windows limpa;
- instalação/desinstalação/upgrade/rollback;
- assinatura e verificação de artefato;
- SBOM e revisão de dependências;
- teste de pacote de diagnóstico e redação;
- soak prolongado;
- restore de backup;
- acessibilidade/UI;
- nenhuma ordem ambígua durante atualização;
- aprovação formal do modo e ambiente permitidos.

## 16. Evidência e rastreabilidade

Nomeie testes por comportamento/requisito, registre reason codes estáveis e preserve correlação. A
matriz atual de requisitos, implementação e testes está em [docs/TRACEABILITY.md](docs/TRACEABILITY.md).
O histórico de execuções materiais fica em [WORKLOG.md](WORKLOG.md).
