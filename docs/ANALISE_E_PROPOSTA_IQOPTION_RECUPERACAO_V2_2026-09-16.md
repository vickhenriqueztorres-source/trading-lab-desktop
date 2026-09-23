# IQ Option — diagnóstico da v1.9.18 e proposta de recuperação por evidências

Data: 16/09/2026. Status: diagnóstico realizado; implementação da proposta pendente.

Este documento complementa o diagnóstico de 15/09 e revisa o plano v1.9.18 à luz do código implementado, do executável em execução e da persistência operacional. A finalidade é explicar por que o bot continua bloqueado e definir uma correção verificável de ponta a ponta.

## 1. Conclusão

O bloqueio atual tem causa comprovada: **a ordem antiga permanece `ACCEPTED`, conserva reserva ativa e foi excluída da reconciliação por uma tentativa histórica `CONFLICT`**. A migração da v1.9.18 adicionou campos, mas não recuperou esse caso legado.

Além disso, os dois caminhos de revisão manual possuem defeitos de integração reproduzidos: `SETTLE` com ID da corretora falha por ausência de `_runtime` no serviço IPC; `REJECT` sem esse ID falha na validação do próprio protocolo. Mesmo corrigidos esses pontos, a limpeza dos bloqueios de reconciliação ainda está incompleta.

A proposta é criar uma coordenação durável de confirmação por ordem, aproveitando os componentes existentes: coletar e validar evidências pelas rotas disponíveis, consolidar o desfecho financeiro uma única vez e recalcular os bloqueios antes de admitir um sinal novo. Reiniciar o bot ou marcar uma ordem como resolvida sem completar essa cadeia não demonstra recuperação.

## 2. Evidências do sistema em execução

Coleta somente leitura em 16/09/2026, aproximadamente entre 00h55 e 01h07, horário de São Paulo.

| Verificação | Resultado observado |
|---|---|
| Executável e janela | `TradingLab-Desktop-v1.9.18-PRO.exe`; janela informa v1.9.18 / Practice |
| Responsividade | `Responding=True` nas amostras desta auditoria; não é um soak test |
| Schema | Migração 12 aplicada em `2026-09-16T03:39:21Z` |
| Ordem pendente | Prefixo `5f945c50`; `SPX/GOLD`, CALL, USD 1,00 |
| Estado | `ACCEPTED`, versão 1, desde `2026-09-15T18:51:12Z` |
| Identificador externo | Presente; portanto há um contrato exato a pesquisar |
| Resultado financeiro | Ausente; `pnl_application_count=0` |
| Reserva | `ACTIVE`, 100 minor units USD, `release_count=0` |
| Última tentativa | `2026-09-15T21:48:01Z`, `CONFLICT / IQOPTION_SYMBOL_MISMATCH` |
| Candidatos automáticos | 0 |
| Ordens em `MANUAL_REVIEW` | 0 |
| Exposições financeiras ativas | 1: a mesma ordem `ACCEPTED` |
| Resolução manual persistida | Nenhum registro encontrado nos campos de resolução manual |

O journal registra, por exemplo, um ciclo às 00h52min31s de 16/09 com `resolved=0` e `manual_review=0`. Na mesma sessão há repetidas decisões `IQOPTION_ORDER_IN_FLIGHT`. A intenção persistida do operador está armada.

Foram comparados os hashes dos arquivos extraídos do executável com as fontes locais de `reader.py`, reconciliação do Core, `ui_service.py`, reconciliação do worker e `community_read_only.py`: as cinco cópias coincidem. Portanto, os defeitos nesses arquivos não se explicam apenas por executar uma cópia antiga diferente das fontes examinadas.

**Limite da evidência:** não foi feita uma consulta autenticada nova à IQ Option e não foi recuperado o payload bruto do conflito original. Esta auditoria não determina se aquela operação ganhou ou perdeu, nem qual `active_id` foi devolvido naquele instante. Ela comprova por que o sistema parou de buscar o resultado.

## 3. O que já foi tentado

| Período | Tentativa implementada/documentada | O que ainda faltou |
|---|---|---|
| 08–09/09 | Recuperação de conexão, gates, relógio e catálogo dinâmico | Restabelecer transporte não comprova desfecho financeiro |
| 10–11/09 | Parsers por origem, prova de finalidade, saldo atualizado e reparo de zeros | O reparo histórico seleciona settlements zero; não alcança a ordem `ACCEPTED` atual |
| 11/09 | Fingerprint, provas negativas, single-flight e serialização do worker de produção | Cobertura e correlação continuam necessárias em cada rota e schema |
| v1.9.16 | Consulta acelerada e proteção contra gale após vitória | Consultar mais depressa não corrige uma resposta interpretada incorretamente |
| v1.9.17 | Transformar incompatibilidade de símbolo em conflito e excluir polling | A ordem foi excluída das consultas sem sair de `ACCEPTED` |
| v1.9.18 | Resolver de ativos, estado de revisão, métodos do writer e painel manual | Faltaram recuperação do legado, integração IPC funcional e prova da retomada completa |

Fontes históricas: `docs/IQ_RESOLUTION_IMPLEMENTATION_20260908.md`, `docs/IQOPTION_RESULT_BALANCE_CANDLE_MARTINGALE_CORRECTION_PLAN_20260910.md`, `docs/IQOPTION_OVERNIGHT_RELIABILITY_CORRECTION_PLAN_20260911.md`, `docs/IQOPTION_DEFINITIVE_UNKNOWN_RECOVERY_20260911.md` e entradas correspondentes do `WORKLOG.md`.

O plano anterior já exigia migração, evidência, idempotência e testes de retomada; a implementação examinada não cumpre integralmente esses requisitos. A revisão abaixo também corrige uma limitação do próprio plano: uma ordem em revisão deve continuar podendo receber evidência tardia e ser reavaliada de forma controlada.

## 4. Defeitos confirmados e suas consequências

### F01 — Conflito histórico funciona como exclusão permanente

Em `packages/persistence/reader.py:147`, a consulta automática contém `NOT EXISTS` para **qualquer** tentativa `CONFLICT` da ordem (`:169`). Não importa se o normalizador foi corrigido depois ou se apareceu evidência nova. A migração 12, em `packages/persistence/migrations.py:508`, apenas adiciona colunas e índices.

Resultado observado: a ordem não entra na fila automática nem na fila de revisão, mas continua bloqueando a execução. Este é o motivo comprovado do incidente atual.

### F02 — O comando de liquidação acessa uma dependência inexistente

`CoreUiProjectionService`, em `apps/core/ui_service.py:999`, não inicializa `_runtime`. Entretanto, o novo handler acessa `self._runtime.writer` em `:1600`. A atribuição existente em `:412` pertence a outra classe. A composição de produção em `apps/core/lifecycle_service.py:484` também não injeta essa dependência.

Reprodução isolada, sem Core operacional ou banco conectado:

```text
SETTLE -> AttributeError: 'CoreUiProjectionService' object has no attribute '_runtime'
```

A captura de exceções do loop IPC, em `ui_service.py:1116`, não inclui `AttributeError`. Assim, essa exceção pode encerrar o atendimento da UI. O encerramento da thread no processo em execução não foi provocado nesta auditoria.

### F03 — O comando “Não executou” não passa no próprio contrato

Em `packages/protocol/ui_messages.py:2729`, `to_payload()` omite `broker_order_id` quando ausente. Já `from_payload()`, em `:2742`, usa `_exact()` exigindo esse campo. O botão em `apps/ui/components/manual_review_panel.py:359` envia `REJECT` sem `broker_order_id`.

Reprodução isolada:

```text
REJECT sem broker_order_id -> ProtocolError: UI IPC payload is invalid
```

Isso explica por que a existência visual do painel não comprova que a operação de recuperação funciona.

### F04 — Bloqueio de conflito não é removido pela conclusão positiva

O Core cria `HG_RECONCILIATION_CONFLICT` em `apps/core/reconciliation.py:511`. O método `_clear_gates_after_positive_cycle`, em `:524`, não remove esse motivo. O scheduler também não o inclui em `_RECONCILIATION_GATES` (`apps/core/reconciliation_scheduler.py:13`).

Reprodução do método com nenhuma pendência e um ciclo positivo ainda resulta em:

```text
active_blockers = ('HG_RECONCILIATION_CONFLICT',)
```

O handler manual apenas chama `refresh_digit_health_gate` (`ui_service.py:1620`), voltado a risco de dígitos, sem concluir o fluxo de recuperação IQ. Não se deve presumir que toda exposição em memória está desatualizada: o ledger já consulta armazenamento em alguns caminhos. A lacuna comprovada é a falta de uma conclusão única que sincronize os consumidores e recalcule os bloqueios de reconciliação.

### F05 — Resposta primária incompleta impede fallback útil

Em `apps/iqoption_worker/reconciliation.py:120`, o histórico só é consultado quando `contract_data is None`. O conteúdo financeiro é validado posteriormente. Se `get_betinfo` encontrar o ID, mas faltar um valor necessário, a função retorna indisponível sem tentar um histórico válido.

Reprodução com transporte sintético:

```text
get_betinfo: contrato exato, sem profit
get_options: resultado terminal válido disponível
resultado: UNAVAILABLE / IQOPTION_RESULT_MONEY_MISSING
chamadas efetuadas: ['get_betinfo']
```

Repetir esse caminho não acrescenta evidência e pode terminar em revisão manual desnecessária.

### F06 — Identidade incompleta é tratada ora como conflito, ora como válida

O reconciliador lê `active_canonical`, `active` e `symbol`, mas não `active_id`; lê `direction`, mas não `dir` (`reconciliation.py:280`). Não verifica ali o `user_balance_id`. Depois constrói evidência preenchendo conta, produto, símbolo e direção a partir da consulta local (`:353`).

Duas reproduções mostram comportamentos opostos:

- `active` numérico desconhecido: `INVALID_RESPONSE / IQOPTION_SYMBOL_MISMATCH`, sem fallback. Ausência de mapa foi tratada como incompatibilidade comprovada.
- Payload com `active_id=1`, `dir=put` e outra conta, para uma ordem EURUSD-OTC/CALL: `FOUND`, P&L `+85`. Os aliases conflitantes não foram examinados.

Precisão exige separar “campo ausente”, “identidade ainda não resolvida” e “campo incompatível”. Copiar a expectativa local para a evidência não equivale a observar esse valor na corretora.

### F07 — Eventos de fechamento não passam pela mesma verificação de identidade

`apps/iqoption_worker/order_session.py:208` encontra a ordem rastreada pelo ID e valida os campos financeiros, mas monta a identidade do evento usando o registro local (`:248`), sem conferir todos os campos divergentes recebidos.

Em reprodução sintética, `option-closed` com ID rastreado, porém outro ativo, direção, moeda e conta, gerou `SETTLED +85` para a ordem local. Isso demonstra uma vulnerabilidade de validação; não prova que a IQ Option enviou esse payload no incidente real.

### F08 — Aceite tardio não vincula o ID para o evento seguinte

Em `order_session.py:220`, um evento pode encontrar a ordem por `client_order_id`. Entretanto, esse caminho não preenche `tracked.broker_order_id` nem cria a entrada em `_tracked` por ID externo.

Reprodução:

```text
option-opened com client_order_id -> OPEN
tracked.broker_order_id -> None
índice pelo ID da corretora -> ausente
option-closed seguinte contendo somente o ID -> descartado
```

O sistema passa a depender de reconciliação mesmo após receber o evento que permitiria acompanhar a operação normalmente.

### F09 — Busca por referência pode apagar o ID encontrado

`packages/brokers/iqoption/community_read_only.py:2123`, `_find_exact_contract()`, substitui o ID pelo `wanted_id` quando só a referência do cliente coincidiu. Quando a busca não tinha ID externo, esse valor é uma string vazia.

Reprodução: entrada `{id: '123', client_order_id: 'audit-order'}`, busca apenas pela referência, saída com `id: ''`. O identificador recuperado deve ser preservado e validado, não substituído pelo valor ausente da consulta.

### F10 — O fallback estático pode contradizer o catálogo da mesma geração

`ActiveIdentityResolver.resolve()`, em `community_read_only.py:162`, tenta primeiro casar o símbolo esperado com seu ID, inclusive pelo fallback estático.

Com catálogo sintético `OTHER_ASSET=76`, a resolução de 76 sem expectativa devolve `OTHER_ASSET`, mas com expectativa EURUSD-OTC devolve EURUSD-OTC. A mesma geração aceita duas identidades para o mesmo número. Além disso, a identidade do momento do envio não está anexada duravelmente à ordem pelo novo resolver.

### F11 — Os métodos manuais ainda aceitam decisões sem evidência estruturada

`resolve_with_broker_evidence()` (`packages/persistence/writer.py:2101`) recebe um ID externo e P&L livres, sem referência obrigatória a uma evidência validada. `confirm_not_executed()` (`:2212`) aceita operador e justificativa sem prova negativa estruturada. O primeiro pode substituir o ID externo anterior via `_transition_order()`.

O IPC tampouco carrega versão esperada ou chave de idempotência da ação original. O helper lê a versão atual e gera uma chave nova. A proteção existente não equivale a validar a versão que o operador viu, e o retry com a mesma chave no writer não compara o conteúdo da decisão.

Essas são lacunas de segurança financeira da implementação, não atalhos adequados para destravar a operação.

### F12 — O “reset” e o resultado técnico têm responsabilidades diferentes

`begin_new_run()` em `apps/core/iqoption_auto_trader.py:442` arma o fluxo e reinicia estado de sinal; `stop()` em `:499` encerra o ciclo técnico. Nenhum deles resolve a ordem persistida.

A alteração de `outcome_for_candle()` para receber `entry_price` também ficou parcial: as chamadas produtivas em `iqoption_auto_trader.py:1436` e `iqoption_martingale.py:225` não passam esse valor. A rotina continua usando a abertura da vela nesses caminhos. Esse problema é relevante para a regra de martingale, mas não é a causa demonstrada da ordem presa atual.

## 5. Proposta melhor: confirmação financeira e recuperação coordenadas

### 5.1 Um fluxo único de conclusão, com várias fontes de observação

Preservar `CoreRuntime`, reconciliador, worker, writer e Risk Ledger existentes. Introduzir uma operação do Core que coordene os componentes e exponha um resultado verificável:

```text
Evento de fechamento / consulta por ID / histórico
                      |
                      v
Normalização por fonte + validação da identidade observada
                      |
                      v
Evidência sanitizada persistida e deduplicada pelo Core
                      |
                      v
Conclusão financeira atômica: ordem + reserva + P&L + auditoria
                      |
                      v
Atualização de consumidores e recálculo de bloqueios
                      |
                      v
Intenção ainda armada + gates válidos + sinal novo -> nova admissão
```

Não se deve votar por maioria entre respostas. Duas leituras do mesmo cache não constituem confirmação independente. Uma resposta terminal autoritativa, com identidade e valores comprovados, pode bastar. Evidências realmente contraditórias exigem investigação; uma resposta antiga `OPEN` seguida de uma resposta terminal recente não é, por si só, contradição.

### 5.2 Registrar a identidade no momento do envio

Persistir junto da ordem: broker, conta financeira/balance ID, modo, moeda, produto, símbolo, `active_id`, origem/geração do catálogo, ID da corretora quando conhecido, instante de aceite e expiração contratual. Preservar o ID originalmente recebido; qualquer correção precisa de evento próprio auditado.

Para ordens antigas, consultar o contrato e o histórico para completar somente o que for comprovável. O catálogo atual pode ajudar, mas não deve reinterpretar silenciosamente um contrato de uma geração passada. Não preencher o “observado” com o “esperado” apenas para a validação passar.

O limite existente de uma ordem por corretora continua valendo. A identificação por conta serve para consultar a conta correta e atribuir os bloqueios corretamente; este plano não autoriza aumentar concorrência nem contornar uma exposição trocando Practice por Real.

### 5.3 Normalizar antes de decidir qual fonte tentar em seguida

Criar um resultado interno de observação tipado, diferente do estado financeiro da ordem:

| Observação | Próxima ação |
|---|---|
| Contrato aberto e informação atual | Aguardar expiração; manter eventos e acompanhamento |
| Resultado terminal completo e compatível | Aplicar evidência uma vez |
| Valor financeiro ou identidade ausente | Consultar próxima fonte e registrar incompletude |
| ID ativo sem mapeamento suficiente | Resolver usando identidade persistida/catálogo; não declarar conflito automaticamente |
| Erro de transporte/schema | Registrar motivo e tentar rota compatível dentro do orçamento |
| Dados positivos incompatíveis | Abrir incidente/revisão e buscar esclarecimento; não escolher arbitrariamente uma resposta |
| Não encontrado em cobertura parcial | Manter desconhecido; ampliar a busca quando possível |
| Prova negativa suficiente para ordem sem aceite confirmado | Aplicar a política de confirmação negativa existente |

Uma ordem com aceite/ID externo confirmado não deve ser classificada como “não executada” só porque não apareceu numa lista de histórico recente.

`get_betinfo`, histórico e `option-closed` devem passar por validadores de fonte que convergem para a mesma estrutura de evidência. Os aliases permitidos precisam ser explícitos. Se dois aliases presentes discordarem, registrar a divergência.

Histórico deve informar a janela realmente coberta e o contexto de conta/produto. Usar paginação somente se a rota de fato oferecer cursor/intervalo verificável; caso contrário, declarar cobertura incompleta. A implementação atual usa limite 500, o que não prova cobertura ilimitada.

### 5.4 Observação continua ativa durante a revisão

`MANUAL_REVIEW` continua representando exposição e bloqueando novas entradas, mas deixa de ser uma fila sem saída automática possível.

Continuar aceitando eventos tardios e permitir reavaliação quando houver evidência nova, reconexão pertinente, novo normalizador, identidade recuperada ou pedido explícito do operador. Separar essa reavaliação da repetição agressiva da mesma consulta já falhada.

Persistir por ordem: motivo, último progresso real, fontes consultadas, próxima consulta, versão do normalizador e referências das evidências. Um limite de tentativas deve disparar escalada/diagnóstico, não uma liberação financeira nem o abandono permanente da observação.

### 5.5 “Recuperar operação” deve comandar uma verificação

Adicionar ação assíncrona **Consultar resultado e recuperar**, com callback explicitamente injetado no serviço IPC. O Core devolve um identificador de acompanhamento e executa a consulta fora da thread Qt e fora do atendimento bloqueante da UI.

A ação deve:

1. localizar a ordem e o contexto financeiro exatos;
2. validar versão e deduplicar a solicitação;
3. consultar/validar as fontes pertinentes;
4. persistir o desfecho quando houver prova suficiente;
5. recalcular somente os bloqueios pertencentes àquela pendência;
6. atualizar estado técnico, métricas e projeções pelo mesmo fluxo usado na liquidação normal;
7. informar “resultado confirmado, pronto para sinal novo” ou o impedimento restante.

O botão Ligar/Desligar conserva sua função de controlar novas entradas. Não deve ser necessário alterná-lo para aplicar um resultado já confirmado.

Revisão financeira manual permanece disponível como exceção auditada, com evidência selecionada, versão vista pelo operador, identidade de sessão e chave de idempotência. O P&L de uma evidência da corretora deve ser derivado de seus campos, não livremente digitado para liberar o bot.

### 5.6 Gates derivados das pendências atuais

Representar a causa do bloqueio com referência à ordem, conta e motivo. Ao resolver uma ordem, remover somente a contribuição correspondente. Manter bloqueios de outras ordens, transporte, saldo, licença, stop loss e comando do usuário.

Não considerar “zero candidatos automáticos” como sinônimo de “zero exposição”. A pós-condição de recuperação deve verificar ordem terminal, contabilização única, reserva correta, consumidores atualizados e ausência da pendência nos gates. Reiniciar o processo deve reconstruir o mesmo resultado a partir do banco.

### 5.7 Martingale: reconciliar também a regra de decisão

Resultado financeiro da corretora continua sendo a autoridade para P&L e liberação da reserva. Candle/strike são evidência técnica separada.

A regra atual por candle, as proteções contra gale após vitória e a proposta de usar strike precisam ser documentadas sem contradição e testadas nos chamadores reais. Se a intenção do produto for decidir recuperação pela perda financeira do contrato, isso é uma mudança explícita da regra anterior, não uma correção silenciosa. Em qualquer caso, uma recuperação com janela já vencida deve ser cancelada; a retomada usa sinal novo, sem gale atrasado.

## 6. Correção imediata antes de ampliar a arquitetura

### Etapa A — Tornar a recuperação utilizável

- Corrigir o contrato IPC de campos opcionais e injetar a dependência de recuperação no serviço.
- Conter erros de comando sem encerrar o atendimento da UI; retornar código estável e registrar diagnóstico sanitizado.
- Criar recuperação idempotente de `ACCEPTED/OPEN + conflito histórico`, preservando tentativas anteriores e reserva.
- Substituir a exclusão por “qualquer conflito passado” por elegibilidade baseada no estado atual e na situação atual da revisão.
- Corrigir fallback após resposta incompleta, aliases/identidade, vínculo de ACK tardio e preservação do ID externo.
- Recalcular bloqueios ao final do fluxo, sem `clear` global indiscriminado.

### Etapa B — Tornar o resultado durável e rastreável

- Identidade contratual persistida, evidências imutáveis e versionamento do normalizador.
- Estado de acompanhamento por ordem com consultas limitadas e reavaliação por progresso novo.
- Mesma aplicação transacional para push, consulta e revisão aprovada.
- Idempotência com fingerprint da decisão: chave igual + conteúdo diferente deve ser rejeitado.
- Proteção contra disputa entre resultado tardio e decisão manual; I/O de rede fora da transação, com revalidação de versão antes do commit.

### Etapa C — Demonstrar o funcionamento no caminho distribuído

- Testar a composição de produção, incluindo o worker efetivamente empacotado e IPC real local.
- Reproduzir os incidentes históricos com fixtures mínimas e sanitizadas.
- Validar em Practice com eventos observados, antes de classificar o release como corrigido.

## 7. Recuperação específica da ordem `5f945c50`

O procedimento a implementar deve ser executado pelo Core proprietário do perfil:

1. manter novas entradas bloqueadas e fazer backup consistente via mecanismo SQLite;
2. localizar pelo UUID completo e confirmar contrato externo, conta, stake e reserva;
3. registrar que o conflito histórico será reavaliado pela nova versão, sem apagá-lo;
4. recuperar a elegibilidade da ordem em transação e consultar pelo ID conhecido;
5. se a primeira resposta for incompleta, tentar o histórico com contexto correto;
6. liquidar apenas com resultado final e identidade suficientes; caso contrário, exibir uma revisão explícita com causa e fontes faltantes;
7. comprovar reserva/P&L e recalcular gates;
8. manter a intenção do operador e esperar um sinal novo, respeitando os demais limites.

Para um contrato antigo cujo contexto não possa mais ser reconstruído, será necessária evidência adicional do histórico da corretora. Esse limite deve aparecer ao operador; não se deve inventar vitória, perda ou cancelamento.

Não executar a CLI atual contra o perfil ativo como forma de contornar o Core: ela instancia outro writer e seu caminho padrão de banco também não representa necessariamente o perfil usado pelo launcher.

## 8. Validação realizada nesta auditoria

### Leitura operacional

- Processo/janela e comparação de cinco arquivos do payload.
- SQLite aberto com `mode=ro` e `PRAGMA query_only=ON` nas consultas diretas.
- Consulta de candidatos, exposição e revisão pelo `StateReader`.
- Journal operacional com seleção de campos relevantes, sem leitura de credenciais.

### Reproduções isoladas

Foram observados os defeitos F02–F10 usando objetos locais e payloads sintéticos, sem conexão à corretora. A verificação de F04 chamou o método de limpeza com leitor vazio e ciclo positivo. As provas de UI invocaram o dispatcher isolado; não acionaram botões no app do usuário.

### Suíte existente

```powershell
python -m pytest tests/unit/test_active_identity_resolver.py tests/unit/test_iqoption_result_parser.py tests/integration/test_manual_resolution.py tests/unit/test_reconciliation_scheduler.py tests/contract/test_ui_ipc_contract.py -q
```

Resultado: **36 passed em 4,27 s**, exit code 0. Houve um `PermissionError` no cleanup temporário do pytest após o resultado, sem falha nos testes executados.

O resultado verde não contradiz os defeitos reproduzidos: os cenários compostos acima não são cobertos. Os testes manuais atuais chegam a aceitar um ID de corretora diferente do originalmente persistido e uma rejeição sem evidência negativa estruturada.

## 9. Critérios de aceite da próxima implementação

| Cenário obrigatório | Prova exigida |
|---|---|
| Banco legado com `ACCEPTED + CONFLICT` | A ordem ganha um responsável de recuperação e uma ação visível; não fica fora de ambas as filas |
| Botão de recuperação em IPC real local | Comando aceita/recusa de forma tipada; serviço segue respondendo após erro |
| Primário incompleto + histórico válido | Histórico é consultado e o desfecho é aplicado corretamente |
| Alias `active_id`/`dir` e conta incompatível | Campo divergente não vira confirmação por cópia da expectativa |
| ID ativo desconhecido | Incompletude identificada; não há falso conflito permanente |
| Catálogo alterado/colisão | Identidade antiga preservada; contradição não é silenciosamente resolvida pelo fallback |
| ACK tardio + fechamento só por ID | Vínculo recuperado e settlement recebido |
| Resultado durante revisão | Reavaliação controlada; resultado suficiente não é ignorado por conflito histórico |
| Resolução duplicada/concorrente | Um lançamento, uma liberação e conteúdo conflitante rejeitado |
| Ordem resolvida | Gates correspondentes removidos; outros bloqueios preservados |
| Reinício antes/depois do commit | Mesma exposição e P&L, sem reenvio da operação original |
| Perda, vitória e empate | Valores exatos e regra de recuperação consistente |
| Expiração/revogação de licença | Novas entradas bloqueadas; ordem aberta continua acompanhada |
| Novo sinal após resolução | Exatamente uma nova admissão, se a intenção e os demais gates permitirem |

O teste decisivo deve atravessar: resposta simulada da IQ → worker de produção → IPC → Core → SQLite/reserva/P&L → gates → novo sinal. Em seguida, repetir em Practice e comparar os resultados com o histórico da corretora. Quando o resultado não estiver disponível, o aceite é entrar em acompanhamento/revisão explícitos, e não desbloquear por tempo.

## 10. Escopo, requisitos e entrega

Responsável pelo estado financeiro: Trading Core. Risco da futura implementação: alto, pois envolve ordem, reserva, persistência e concorrência. Esta etapa realizou diagnóstico e documentação; não alterou lógica financeira, ordem operacional, reserva, resultado ou configuração do bot.

Requisitos relacionados: FR-046, FR-053 a FR-056, FR-061 a FR-063, FR-071 e FR-080; AG-INV-002 a AG-INV-005 e AG-INV-011; R-STATE-003 a R-STATE-008; R-ORD-005 a R-ORD-008.

A prioridade é corrigir os caminhos comprovadamente quebrados e validar a recuperação completa da pendência atual. A ampliação de observabilidade/evidências deve apoiar esse fluxo, não substituir a demonstração de que uma ordem confirmada deixa de bloquear o robô.
