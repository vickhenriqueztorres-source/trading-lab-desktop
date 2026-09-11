# IQ Option — plano de correção após auditoria da madrugada

Data: 11/09/2026. Baseline: Trading Lab Desktop v1.9.11.
Status: **implementado na árvore de trabalho e validado localmente; ativação no perfil e observação externa permanecem pendentes**.
Escopo: IQ Option no EXE atual e suas estratégias atuais. Não alterar estratégias, execução ou configuração da Deriv. Componentes compartilhados exigem testes de isolamento.

## 1. Objetivo e limites

Eliminar bloqueios falsos, consultas repetitivas sem progresso e atrasos locais evitáveis, preservando o estado financeiro. Recuperação G1/G2 continua opcional e decidida exclusivamente pela vela fechada. Não aumentar risco, ampliar janelas, forçar entradas, reiniciar o bot ou liberar a ordem desconhecida para aparentar normalidade.

Três fatos independentes:

- Resultado técnico da vela: decide avançar/encerrar G1/G2; não é o resultado financeiro do contrato.
- Resultado financeiro: precisa de evidência terminal da IQ; liquida ordem, P&L e reserva no Core.
- Saldo da conta: precisa de observação identificada da IQ; não é calculado somando resultados técnicos ou apenas os trades do bot.

Não há como garantir liquidação automática de uma ordem sem evidência externa suficiente. “Resolução definitiva” significa liquidar/rejeitar com prova ou encaminhar um caso explícito para revisão, mantendo a exposição e o bloqueio enquanto a ambiguidade existir.

Requisitos: FR-020–024, FR-045–046, FR-051–056, FR-060–064, FR-070–073 e FR-080–083. Preservar AG-INV-001–005, R-STATE-005–008, R-DATA-001–005 e R-REL-003.

## 2. Base factual e grau de certeza

Referência: auditoria anterior de 11/09, desde 00:00 até aproximadamente 10:34, horário de São Paulo, equivalente ao início em 03:00 UTC. Os agregados abaixo são daquele relatório, não uma nova medição contínua. O EXE de saldo resiliente iniciou às 00:05:22; a janela contém alguns minutos da execução anterior.

- 27 ordens IQ: 26 liquidadas localmente e uma `UNKNOWN`; 11 P&Ls positivos, 11 negativos e quatro zeros. P&L registrado de USD +0,75, ainda não validado contra extrato completo da IQ.
- A ordem local `744946ee-0667-48dc-9cc8-bdb9ec1f3143`, EURJPY-OTC, PUT, USD 1, foi criada às 06:40:16 e ficou sem ID da corretora após timeout. Outbox ambígua, uma tentativa registrada e reserva ativa. Mais de 570 buscas sem localização no recorte mais recente da auditoria. Não há evidência local de reenvio dessa ordem.
- 1.163 eventos `IQOPTION_CLOCK_STALE`, com idade apresentada como zero em grande parte dos registros. O log não conserva idade negativa: isso limita a identificação da causa exata de cada episódio.
- 196 episódios de saldo desatualizado, somando cerca de 31min42s. Bloqueio e liberação são dois eventos do mesmo episódio, não duas falhas. Durações de gates diferentes podem sobrepor-se e não devem ser somadas como indisponibilidade total.
- 11 perdas técnicas de vela: quatro recuperações submetidas, sendo três G1 e uma G2; sete encerramentos por janela perdida. São oportunidades técnicas de recuperação, não 11 ciclos independentes nem uma taxa de sucesso financeiro.
- 16 tentativas de recuperação da IQ, 11 sucessos e cinco falhas; também houve limites de reconexão. Eventos de transporte podem aparecer duplicados para o mesmo incidente.
- Catálogo com centenas de instrumentos descobertos e zero símbolos executáveis ao final do recorte; fontes de manifesto com erros 503/404. Nenhum desses fatos prova, isoladamente, que o mercado inteiro estava fechado.
- O processo recriado às 05:04:50 era da recuperação Deriv: não atribuir esse restart à IQ.

Defeitos comprovados por leitura do código atual:

1. Busca sem broker ID depende de `client_order_id`, mas esse campo local não integra a mensagem de abertura enviada à IQ. ACK tardio perde o waiter depois do timeout.
2. Reconciliação mistura falha de consulta com `NOT_FOUND`; busca recente não comprova cobertura completa. O backoff é reiniciado por mudanças de gates de relógio/saldo, não apenas por progresso da ordem.
3. Core rejeita qualquer idade UTC negativa do clock e a registra como zero. O worker projeta horário atual, sem transmitir a idade original da amostra no contrato atual. A mesma comparação estrita aparece no saldo.
4. MG precede o radar comum, mas fica depois de chamadas síncronas de relógio/catálogo. Catálogo sem candidatos causa retorno antes do acompanhamento do ciclo. O cooldown de perda de 28s já é explicitamente ignorado pelo caminho MG; não é a causa direta a corrigir.
5. Há checagem da janela com horário capturado antes de consultas bloqueantes; o dispatch cria um deadline novo, e o transporte calcula novamente a expiração. Isso permite perder a vinculação temporal com o ciclo planejado.
6. Saldo push atualiza cache, mas não acorda a consulta pendente. Valor e timestamp do cache podem ser lidos em locks separados. Reconexão seguida de falha exclusiva de saldo pode ser tratada como recuperação de transporte malsucedida.
7. A UI marca qualquer zero `STATUS_QUERY` como suspeito, incluindo potenciais empates novos válidos; não distingue a versão/evidência do incidente histórico.

Hipóteses ainda a provar: frequência real de idade negativa entre processos; causa externa de cada timeout; semântica dos quatro zeros; razão específica do catálogo zerado; contribuição temporal de cada bloqueio nos sete MG perdidos. Não transformar essas hipóteses em resultados confirmados.

## 3. P0 — preservar o incidente e resolver a identificação da ordem

### Caso já existente

1. Reconstruir linha do tempo da intenção, outbox, tentativa, reserva, eventos e reconciliações com limite temporal fixo, sem escrever no perfil vivo.
2. Consultar a ordem por ID exato se existir evidência tardia que o recupere. Sem ID, pesquisar posições e histórico da conta/modo corretos, com cobertura da janela de envio/expiração comprovada e paginação delimitada quando a rota suportar.
3. Usar ativo exato, produto, direção, stake, moeda, conta, expiração e intervalo de execução como critérios de candidatos. Uma coincidência de valor/horário, mesmo única entre os dados disponíveis, não comprova identidade; ausência de correlação suficiente ou possibilidade de trade manual mantém revisão.
4. Validar contrato candidato pela rota individual e registrar origem, geração, cobertura e campos efetivamente observados. Campos copiados da consulta não podem ser apresentados como confirmação externa.
5. Evidência terminal exata aplica transação idempotente de ordem/reserva/P&L. Prova explícita de não execução usa a transição já suportada pelo domínio. Sem prova, manter `UNKNOWN` e exposição ativa com caso de revisão claro.

Não usar a vela técnica WIN das 06:41, diferença de saldo, número de tentativas ou tempo decorrido para resolver financeiramente o caso. A rota atual não comprova ausência definitiva; não fabricar `not_found_evidence` a partir de uma lista vazia.

### Evitar novos casos sem correlação recuperável

- Vincular durablemente ordem local, intenção e identificador de requisição de abertura antes do envio; manter a referência também na fronteira do transporte IQ. Não inventar campo de API supostamente aceito pela corretora.
- Reter um registro limitado das requisições potencialmente enviadas após timeout, separado do waiter síncrono, para receber ACK tardio e associar broker ID sem reenviar a abertura.
- Entregar ACK/evento tardio ao Core, que é o único escritor financeiro. Geração, conta e request ID devem impedir que resposta antiga seja associada a uma ordem nova.
- Sobreviver a crash/restart pelo estado durável do Core. Tracking em memória pode acelerar recuperação, mas não substituir o histórico durável.
- Distinguir busca vazia válida, cobertura incompleta, rota não suportada, timeout, transporte indisponível, identidade conflitante e evidência financeira incompleta.
- Não restringir histórico a “últimos 100” e chamar isso de consulta completa. Se a integração não oferecer cobertura suficiente, a revisão manual é uma limitação explícita.

Arquivos: `apps/iqoption_worker/order_session.py`, `apps/iqoption_worker/reconciliation.py`, `packages/brokers/iqoption/community_read_only.py`, contratos IQ/IPC, `apps/core/reconciliation.py` e persistência.

## 4. P0 — reconciliação com agenda durável e orçamento

- Agenda por broker/conta/ordem com `attempt_count`, `last_progress_at`, `next_due_at`, motivo, cobertura consultada, geração e estado da revisão. O Core persiste; o scheduler usa duração monotônica local e restaura conservadoramente após restart.
- Uma consulta em voo por chave; reunir pesquisas equivalentes por conta/produto/janela. Reconexão, heartbeat e gates não podem criar jobs concorrentes para a mesma ordem.
- `CLOCK_STALE`/`BALANCE_STALE` não reiniciam backoff de uma ordem. Evento financeiro novo é processado imediatamente; mera mudança de gate não é progresso.
- Proposta inicial para buscas sem progresso: primeira tentativa elegível imediata, intervalos de 2, 5, 10, 20, 40, 80 e 160s, acrescidos de jitter positivo limitado. Após oito tentativas executadas ou 15min sem avanço, o que vier primeiro, abrir caso de revisão; tentativas posteriores no máximo a cada 15min, dentro do orçamento global.
- Orçamento inicial proposto: até 32 mensagens externas de reconciliação por ordem na primeira hora e oito/hora depois, incluindo páginas e consultas manuais. Deduplicação por conta reduz o custo adicional. Valores são parâmetros de aceitação a validar, não promessa da API.
- Sem transporte pronto, agendar espera de disponibilidade sem executar buscas inúteis nem consumir orçamento de requisições não enviadas. Registrar o bloqueio de disponibilidade separadamente.
- Ingress de eventos financeiros permanece ativo e não é limitado pelo backoff de polling. Um resultado tardio pode resolver uma ordem já escalada para revisão.
- A UI mostra tentativas, última evidência, próxima consulta e necessidade de revisão. Não criar botão “liberar mesmo assim”. Eventual resolução manual exige comando autenticado, justificativa/evidência e validação no Core.

Arquivos: `apps/core/reconciliation_scheduler.py`, `apps/core/reconciliation.py`, reader/writer/migração aditiva e projeção UI. Não alterar migração já publicada.

## 5. P0 — contrato de relógio robusto entre processos

- Worker fornece idade da amostra original medida com seu relógio monotônico, sequência/geração, epoch da corretora projetado e qualidade da amostra. Reconsultar projeção não renova a idade da fonte.
- Core mede envio/recepção IPC e idade do snapshot em seu próprio relógio monotônico. Não subtrair monotônicos de processos diferentes; compor idade da fonte com limite conservador do transporte IPC e tempo local desde a recepção.
- Conservar UTC para auditoria e representar delta UTC com sinal. Registrar separadamente `source_age_ms`, `ipc_rtt_ms`, `core_snapshot_age_ms`, `signed_wall_delta_ms`, geração e motivo da invalidação.
- Tolerância pequena para divergência UTC de recepção pode existir na compatibilidade, com limite explícito e testes; nunca substituir a comprovação de freshness. Preservar os limites atuais de idade e skew até haver evidência para outra política.
- Suspensão, salto relevante de relógio, geração nova, sequência regressiva e amostra genuinamente velha invalidam confiança. Nenhuma entrada usa relógio do PC como fallback quando a IQ não está confirmada.
- Projetar prazos com precisão suficiente e margem para a incerteza medida. Truncar o horário para segundos inteiros não pode conceder tempo adicional de entrada.
- Publicar uma única decisão de validade do Core para execução e UI; a UI não deve considerar o clock pronto apenas porque seu offset está no limite.

Arquivos: `packages/domain/market.py`, conversores de snapshots IPC, `community_read_only.py`, `apps/core/iqoption_auto_trader.py`, `apps/core/ui_service.py`.

## 6. P0 — prioridade real e prazo absoluto para G1/G2

### Separar acompanhamento de autorização para enviar

- Acompanhar ciclo/ordem existente antes da descoberta global e de retornos por catálogo vazio, ausência de candidatos, bot degradado ou licença vencida. Esses fatores podem bloquear novas entradas; não podem apagar o acompanhamento.
- Fixar no ciclo estratégia/versão, configuração, conta, produto, ativo exato, direção, etapa, ordem-pai, candle-alvo, expiração contratual e deadline exclusivo da recuperação.
- Observar a vela exata fechada: CALL `close > open`, PUT `close < open`; igualdade encerra. Candle parcial, gap, série diferente ou evidência conflitante não decide.
- A decisão técnica passa a pronta para G1/G2, mas envio ainda depende de ordem anterior financeiramente resolvida, saldo, relógio, payout, manifesto, risco e exposição. Vitória técnica nunca libera a reserva.
- Preservar o bypass existente apenas do cooldown financeiro ordinário no caminho MG; preservar limites de perda, quantidade, stake e stop. Não alterar configuração do operador.

### Retirar trabalho demorado da janela crítica

- Atualizar relógio, catálogo e saldo em observadores independentes com snapshots válidos; não executar refresh global síncrono antes de tratar a recuperação.
- Planejar fetch da vela do ativo da sequência e acompanhamento financeiro com prioridade efetiva no hub, orçamento de mensagens, cliente IPC e worker. O rótulo `RECOVERY` sozinho não basta.
- Prioridade: ingress/eventos financeiros e persistência; consultas necessárias à ordem/ciclo com deadline próximo; saúde essencial; radar; descoberta global; projeções e telemetria agregada. Filas limitadas e fairness devem impedir fome de saúde/reconciliação.
- Não iniciar trabalho opcional não preemptível quando seu pior prazo invadir a janela conhecida. Uma chamada externa já enviada não pode ser magicamente preemptada: seu limite precisa entrar no orçamento.
- Instrumentar fechamento→recepção da vela→decisão→resolução anterior→gates→commit→envio→ACK. Classificar janela perdida pelo fator predominante e conservar a sequência completa de bloqueios.

### Não enviar uma recuperação atrasada em outro contrato

- Transportar o deadline e a expiração absoluta do ciclo pela intenção/outbox até o worker. Não substituí-los por `agora + 15s` nem recalcular uma expiração futura ao chegar tarde.
- Revalidar tempo após fetch, payout, espera de fila e commit, e imediatamente antes do envio externo. Tempo usado antes de uma chamada bloqueante não autoriza o envio depois dela.
- Preservar a janela atual de 20s e o limite base atual. Não deslocar MG perdido para outra vela; fechar a oportunidade com motivo explícito.
- Se o prazo terminou antes de qualquer envio, cancelar com prova de não envio e transação adequada. Se o envio pode ter ocorrido, manter `UNKNOWN`, mesmo que a janela já tenha terminado.
- Persistir consumo do nível antes do dispatch; crashes, reentrância, evento duplicado ou reconexão não podem gerar duas ordens da mesma etapa.

Critério de desempenho proposto para replay/ambiente controlado: decisão→enqueue p95 até 500ms depois de todas as evidências/gates disponíveis, mantendo os limites de envio existentes. Zero janela perdida por trabalho local opcional quando os pré-requisitos chegaram com margem suficiente. Atraso externo legítimo pode continuar cancelando uma recuperação: não prometer 100% de G1/G2.

Arquivos: `apps/core/iqoption_auto_trader.py`, `apps/core/iqoption_martingale.py`, `apps/core/iqoption_series_hub.py`, orçamento/scheduler de mensagens, cliente IPC, `apps/iqoption_connection_worker/server.py`, `apps/iqoption_worker/order_session.py`, `community_read_only.py` e contratos de ordem.

## 7. P1 — saldo resiliente e recuperação seletiva da conexão

### Saldo

- Snapshot atômico de valor + conta/balance ID + modo + moeda + origem + revisão + timestamps + geração, lido e publicado sob a mesma proteção. Não casar valor antigo com timestamp novo.
- Push válido da conta ativa atualiza o snapshot e satisfaz a espera de sincronização. Polling é fallback limitado; não deve ignorar confirmação que acabou de chegar por outro canal.
- Full snapshot sem a conta selecionada não substitui o último dado válido dessa conta. Resposta antiga, de outra geração/modo ou monetariamente inválida não renova freshness.
- Proteger contra snapshot antigo chegando depois de push novo. Quando a origem não fornecer sequência temporal suficiente, não supor que “última recepção” equivale ao estado mais novo; registrar incerteza e confirmar novamente de forma limitada.
- Conservar polling de fallback de 5s e limite de confirmação de 15s inicialmente; notificar após abertura/liquidação com coalescimento, não com rajadas. Orçamento reservado para saúde evita starvation por radar/reconciliação.
- Agendar polling pelo próximo prazo monotônico, evitando somar a duração da consulta ao intervalo em cada volta. Transportar idade monotônica real ao Core e recalcular validade em cada uso/projeção, mesmo se o observador estiver bloqueado ou parado; usar o mesmo contrato temporal da correção do relógio.
- Uma falha transitória mantém último valor e horário real com `RETRYING`, enquanto comprovadamente fresco. Expiração bloqueia; não renova timestamp, inventa saldo ou aumenta TTL para esconder falha.
- Projeção Core→UI consistente: valor confirmado, idade, fonte, tentativas e bloqueio, inclusive bot parado. Meta controlada de atualização visual em até 1s após confirmação no Core.

### Transporte

- Distinguir `TRANSPORT_CONNECTED`, sincronização pendente e autorização para novas entradas. Falha de leitura de saldo após reconnect bem-sucedido não deve reconectar novamente o mesmo socket saudável.
- Retry de saldo/catálogo/clock é por operação; recuperação de sessão ocorre apenas com evidência de transporte indisponível ou sessão rejeitada. Manter limitadores, sessão cifrada e armamento do operador.
- Um coordenador de recovery por geração deduplica notificações; backoff/breaker permanece entre processos e reinícios. Não consumir login HTTP por uma falha exclusiva de leitura.
- O worker efetivo de conexão despacha chamadas síncronas; introduzir execução limitada por capacidade e fila priorizada, preservando serialização financeira e exclusão das rotas sem correlação. Ping/saúde e entrega de evento financeiro não devem ficar atrás de um catálogo de oito segundos.
- Para rotas sem request ID confiável, timeout exige tratamento próprio: quarentena da rota, registros de requisições expiradas e invalidação por geração. Não simplesmente remover o teardown e aceitar a próxima resposta como nova. Se isolamento seguro não for comprovável, a recuperação continua necessária.
- Overflow de respostas read-only obsoletas não pode ser confundido automaticamente com perda do canal financeiro; separar filas, motivos e tratamento sem descartar evento financeiro.

Arquivos: `apps/core/iqoption_balance_monitor.py`, `apps/core/lifecycle_service.py`, `apps/core/ui_service.py`, worker de conexão, `community_read_only.py` e painéis IQ.

## 8. P1 — catálogo, manifesto e apresentação dos resultados

### Catálogo e manifesto

- Separar catálogo da corretora, estratégias elegíveis do manifesto e dados/payout disponíveis. `executable_count` atual é a interseção desses filtros, não a quantidade de mercados abertos.
- Publicar contadores por etapa: descobertos, abertos, TURBO suportados, analisáveis, executáveis, permitidos por manifesto, com dados/payout válidos. Expor versão/expiração do manifesto e razão do zero.
- Não apresentar “mercado fechado” se a causa for ausência/expiração do manifesto ou falha de parsing. Falha parcial de Digital não deve invalidar TURBO validado.
- Manter último catálogo/signed manifest apenas enquanto sua validade permitir. Cache expirado pode ser mostrado como histórico, nunca autorizar envio. Não remover assinatura, hash, status ou entitlement.
- Falhas primária 503 e espelho 404 devem mostrar indisponibilidade das fontes e situação do cache, com backoff. Corrigir publicação/objeto no servidor é frente externa separada, a confirmar com evidência e autorização; não tratar modificação do EXE como solução suficiente.
- Zero candidatos ou manifesto indisponível não interrompe liquidação de ordens e acompanhamento de vela já vinculada a um ciclo.

### Resultados e histórico

- Exibir separadamente execução da ordem, resultado técnico da vela, resultado financeiro confirmado e nível G0/G1/G2. Zero confirmado é empate/reembolso; campo ausente é desconhecido, nunca USD 0,00 por padrão.
- Substituir o predicado genérico `P&L == 0 && STATUS_QUERY` por estado explícito de verificação associado à proveniência, versão do normalizador e evidência. Revalidar os quatro zeros da madrugada antes de classificá-los.
- Reexaminar os 114 zeros do incidente antigo como lote histórico separado. Preservar evidência antiga e proposta de reparo; aplicar apenas diferenças comprovadas, auditadas e aprovadas via Core, sem repetir liberação de reserva ou apagar histórico.
- Conservar fonte original (`betinfo`, evento terminal ou histórico), schema/versão, finalidade, stake, retorno e hashes da evidência minimizada. `STATUS_QUERY` sozinho não explica a semântica financeira usada.
- Divergência entre vela WIN e P&L negativo não é automaticamente bug do parser: cor de vela e preço efetivo de execução são referências diferentes. Registrar strike/entrada/fechamento/expiração somente quando realmente disponíveis e comparar a semântica do produto.
- Mostrar P&L confirmado separado de montante sob revisão. Comparar saldo com extrato levando em conta operações manuais, outros contratos e ajustes de conta; não distribuir diferença de saldo entre trades por suposição.

Arquivos: `apps/core/manifest_client.py`, catálogo/eligibilidade IQ, `apps/core/ui_service.py`, `apps/ui/components/order_table.py`, workspace IQ, `packages/protocol/ui_messages.py`, `result_parser.py`, reader/writer e `iqoption_settlement_repair.py`.

## 9. P1 — relatório operacional que explique o bot ligado

- Separar processo vivo, transporte conectado, intenção armada, entradas permitidas, bloqueio de risco, espera da vela, espera de liquidação, ausência de sinal e revisão manual.
- Mostrar início/duração do bloqueio atual, última atualização válida, última ordem e próxima ação automática. “Ligado” não deve sugerir que o bot está apto a entrar.
- Contar incidentes por identidade/geração, não por linhas repetidas; calcular disponibilidade por união dos intervalos bloqueados, com timezone explícito e cutoff reproduzível.
- `ASSET_MISMATCH`, `REGIME`, `NO_SIGNAL` e descartes normais de candidatos entram em agregados, não viram defeitos ou motivo para enfraquecer estratégias.
- Alertas por mudança significativa, primeira falha e escalada; eventos financeiros completos permanecem auditáveis. Sampling somente de ruído não financeiro.
- Relatório por ciclo: perda técnica, elegibilidade, tentativa, aceitação, atraso e motivo de cancelamento. Separar janela perdida por processamento local de pré-requisito externo indisponível.
- Logs/fixtures/pacotes minimizados, redigidos e limitados; não incluir vault, credenciais ou banco operacional em ZIP de suporte.

## 10. Sequência de implementação e validação

1. **Reprodução:** fixtures sintéticas minimizadas dos defeitos e linha do tempo do incidente. Fixar relógios virtuais, IDs e fronteiras temporais; não usar o perfil vivo como banco de testes.
2. **Integridade e tráfego:** correlação/ACK tardio, outcomes corretos, agenda durável e backoff. Tratar o caso existente por evidência em trilha separada.
3. **Tempo e transporte:** contrato de idade monotônica, snapshots atômicos, saldo push e reconexão seletiva; tirar operações longas do dispatcher crítico com concorrência limitada.
4. **MG:** prioridade efetiva de ponta a ponta, acompanhamento independente e deadline/expiração imutáveis.
5. **Transparência:** catálogo por filtros, revisão financeira por evidência e UI/relatórios coerentes.
6. **Integração:** testes de caos/replay/soak, suíte completa e build separado do EXE atual. Não declarar aprovação integral apenas porque um teste instável passou numa repetição.

Testes indispensáveis:

- Ordem não enviada, aceita com ACK perdido, ACK tardio, resposta fora de geração, duplicidade, histórico parcial, duas correspondências, indisponibilidade de fonte, rejeição provada e resultado conflitante.
- Crash antes/depois de envio/commit; restart conserva reserva, correlação e agenda; nenhuma submissão ambígua é repetida. Revisão manual não abandona eventos tardios.
- Oito horas simuladas de `NOT_FOUND` com flapping de clock/saldo e reconexões: orçamento respeitado, próximo prazo não zerado e estado financeiro preservado.
- Pequeno delta UTC positivo/negativo, atraso IPC, amostra velha, relógio regressivo, suspensão e worker reiniciado: nenhum falso stale por comparação entre processos, nenhum dado velho aceito como novo.
- Push de saldo durante polling com timeout, frame de outra conta, geração antiga, ordem inversa, leitura concorrente valor/timestamp e expiração real. Sem reconexão por falha exclusiva de saldo.
- Catálogo lento ou vazio durante fechamento; gates disponíveis em momentos distintos; payout consumindo o prazo; deadline exatamente na fronteira. Recuperação não muda de vela nem de vencimento, não duplica e respeita todos os limites de risco.
- Exemplo obrigatório: MG elegível no segundo 18, consulta de payout encerrada no segundo 21. Esperado: nenhuma abertura enviada, ciclo encerrado por prazo, sem novo deadline relativo e sem deslocamento da expiração. Se já houve possível envio, esperado: ambiguidade conservada, não cancelamento fictício.
- G0→G1→G2, WIN/LOSS/empate técnicos, resultado financeiro divergente, quatro zeros novos e classe de zeros antigos, desarme e licença/estratégia suspensa durante ordem aberta.
- Respostas read-only tardias/overflow, saturação do radar e mensagens simultâneas; entrega financeira preservada e outra corretora não afetada.
- Migração aditiva, backup SQLite consistente, rollback compatível, writer único, falha de disco e proteção de segredos.

Gate local: pytest completo com falhas/skip identificados; Ruff, format, mypy, compileall, scanner de segredos e teste de integridade do build. Métricas de latência são metas técnicas de teste, não garantias de resposta da corretora ou rentabilidade.

## 11. Entrega e ativação do próximo EXE

- Entregar build distinto com hash, versão de contratos, notas, testes executados e limitações. Preservar o EXE anterior e o histórico do perfil.
- Validar primeiro replay/soak simulado longo e observação Practice sem envio; teste externo exige opt-in explícito. Depois, ensaio Practice controlado, com limites escolhidos pelo operador, incluindo reconexão e pelo menos uma sequência G1/G2 quando houver cenário legítimo.
- Para comparação operacional, usar janela equivalente à madrugada, medindo causas e duração dos bloqueios, quantidade de consultas, latência pós-vela, consistência financeira e uso de recursos. Não exigir quantidade de trades nem forçar sinal para passar teste.
- Não trocar o EXE no perfil enquanto existir ordem ambígua. Se o reparo do incidente depender de código novo, preparar recuperação isolada/somente leitura e revisão auditada antes da ativação; não contornar o bloqueio de atualização.
- Antes da atualização autorizada, backup consistente pela API SQLite, nunca cópia isolada do arquivo principal com WAL ativo. Rollback não deve restaurar snapshot antigo por cima de operações novas nem reverter schema incompatível silenciosamente.
- Aceitação final: nenhuma liberação ou resultado inventado; nenhuma ordem duplicada; zero MG fora de prazo; nenhuma consulta infinita sem escalada; UI fiel à qualidade dos dados; regressão Deriv sem alteração funcional.

## 12. Pontos do código que sustentam o plano

Referências da árvore de trabalho inspecionada; linhas podem mudar durante a implementação:

- `apps/iqoption_worker/reconciliation.py:114`: fallback por client reference; `:137` e `:141`: erro/ausência terminam no mesmo `NOT_FOUND`.
- `packages/brokers/iqoption/community_read_only.py:1536`: remoção do waiter; `:1550`: roteamento tardio sem waiter; `:1590`: corpo da abertura; `:1639`: histórico recente limitado.
- `apps/core/reconciliation_scheduler.py:67`: trigger reinicia agenda; `:133`: mudança de assinatura; `:153`: inclusão dos blockers.
- `apps/core/iqoption_auto_trader.py:582`: idade UTC estrita; `:588`: clamp no diagnóstico; `:613`: catálogo antes do MG; `:628`: retorno sem candidatos.
- `apps/core/iqoption_auto_trader.py:1272`: instante usado antes do fetch; `:1320`: janela; `:1330`: exceção do cooldown; `:1842`: deadline novo no dispatch.
- `packages/brokers/iqoption/community_read_only.py:1781`: cálculo de expiração; `apps/core/iqoption_series_hub.py:243`: caminho de refresh/prioridade a integrar.
- `apps/iqoption_connection_worker/server.py:93`: dispatcher síncrono; `apps/core/lifecycle_service.py:772`: reconnect seguido de saldo.
- `packages/brokers/iqoption/community_read_only.py:549`: leitura do cache; `:1100`: atualização do full snapshot; `apps/core/iqoption_balance_monitor.py:233`: freshness por UTC.
- `apps/core/iqoption_auto_trader.py:2453`: filtros dos executáveis; `apps/core/manifest_client.py:1132`: fallback das fontes e preservação de cache.
- `apps/core/ui_service.py:872`: classificação ampla de zeros; `packages/brokers/iqoption/result_parser.py:46`: normalização por fonte; `packages/persistence/iqoption_settlement_repair.py:644`: verificação de reparo.

## 13. Implementação e evidência local

Implementação concluída em 11/09/2026, somente para o fluxo IQ Option. O comportamento financeiro
da Deriv não foi alterado; a suíte integral cobre a regressão compartilhada.

- ACK de abertura recebido depois do timeout é conservado em registro limitado por TTL e pode
  completar a ordem ambígua sem nova submissão. O vencimento calculado no Core atravessa IPC e
  worker como valor imutável; o worker verifica o deadline imediatamente antes do transporte.
- A reconciliação distingue resposta vazia válida de consulta indisponível/parcial, realiza apenas
  uma consulta por ciclo, restaura o backoff durável após restart e escala para revisão depois de
  oito tentativas ou 15 minutos. A ordem continua `UNKNOWN`, com reserva e escuta de evidência
  tardia; revisão não inventa rejeição nem liquidação.
- Relógio e saldo carregam geração, sequência/revisão e idade da amostra. O Core compõe idade por
  relógio monotônico, rejeita regressões e não usa pequeno delta UTC entre processos como stale.
  Push de saldo válido vence snapshot antigo, acorda o polling pendente e falha exclusiva de saldo
  não força relogin de um transporte saudável.
- O worker de conexão possui filas limitadas separadas para tráfego crítico e catálogo. Ping,
  saúde, eventos financeiros, abertura/reconciliação, clock e saldo não aguardam na fila comum de
  catálogo. A serialização necessária das rotas sem correlação permanece preservada.
- Recuperação G1/G2 é tratada antes da descoberta global, mantém o candle-alvo e usa deadline e
  expiração originais. O horário é revalidado depois de payout/chamadas bloqueantes; no caso
  obrigatório segundo 18 para segundo 21, nenhuma abertura é enviada e o ciclo termina por janela.
- A UI mostra tentativas de reconciliação, próximo prazo e revisão necessária sem oferecer liberação
  manual da reserva. Zeros do normalizador atual não são marcados genericamente como falha; legado
  sem evidência suficiente continua explicitamente sob revisão.

Validação local: `1466 passed, 4 skipped, 0 failed` em 496,00 s; Ruff check e format em 536
arquivos, mypy em 317 fontes, compileall, pip check e diff-check aprovados. Os quatro skips são os
smokes externos/plataforma já opt-in. O aviso `WinError 5` ocorreu apenas no callback de limpeza
temporária do pytest depois do resultado aprovado.

Build separado: PyInstaller 6.22.2, scanner com zero segredos, manifesto de 554 arquivos e SHA-256
de manifesto `d6f3ee6c05bd7a9305e1e1f18d7b543c80ba1cba804e80bd70ab1badc47a4477`; integridade e
health-check do onedir aprovados. O portátil contém 996 entradas e somente o recurso
`TradingLab.payload.zip`, ProductVersion 1.9.11, 59.382.272 bytes, health-check exit 0 e SHA-256
`EE04ABF542ED25DDBCF628437155D0DCB43238F8551BB1FA8F6299E67B01C0E2`.

O pacote não foi instalado nem ativado no perfil operacional. Nenhuma credencial, login, consulta
externa ou ordem foi usada. A publicação/correção das fontes remotas de manifesto, a revisão da
ordem histórica ainda ambígua e o ensaio Practice externo continuam gates separados e exigem
evidência/autorização operacional.

## 14. Correção complementar do `HG_ORDER_UNKNOWN` histórico

O primeiro build corrigia ACK tardio para novas ordens, mas a ordem histórica
`744946ee-0667-48dc-9cc8-bdb9ec1f3143` foi criada antes desse registro existir. A inspeção somente
leitura do perfil confirmou: estado `UNKNOWN`, outbox `AMBIGUOUS`, ausência de broker ID, reserva
ativa de USD 1,00, nenhuma evidência positiva e 705 tentativas até 15:51:23 UTC. O código anterior
consultava o histórico por `client_order_id`, embora a IQ não devolva essa referência em contratos
binários antigos, e o worker também descartava o `not_found_evidence` já previsto no protocolo.

A correção complementar implementa duas saídas conservadoras:

- contrato antigo pode ser recuperado somente por uma correspondência única de ID do ativo,
  direção, stake e horário de submissão dentro de 20 segundos; mais de um candidato ou campo
  incompleto mantém a ordem ambígua;
- ausência só vira prova quando a mesma resposta contém portfólio aberto completo e histórico
  fechado não truncado (menos de 100 linhas), com identidade temporal verificável. O Core exige
  duas provas completas separadas por pelo menos 10 segundos antes de marcar `REJECTED`, reconciliar
  a outbox, liberar a reserva uma única vez e remover o gate.

O IPC do worker IQ agora transporta `not_found_evidence`, e o scheduler prioriza a segunda
confirmação sem redefinir o backoff geral. A janela modal deixou de expor apenas o código técnico e
explica que a proteção evita duplicidade financeira.

Validação após a correção: 1.471 testes aprovados, 4 skips previstos e zero falhas em 402,66 s;
Ruff check/format, mypy em 317 fontes, compileall, pip check e diff-check aprovados. Build onedir com
scanner zero segredos, manifesto de 554 arquivos, hash de manifesto
`9b6239cfdacc7ee27865f37c03f44033a93cbfaac4722055d1e6d549d407f47a`, integridade e health-check
aprovados. Portátil com 996 entradas, recurso único `TradingLab.payload.zip`, 59.398.144 bytes,
ProductVersion 1.9.11, health-check exit 0 e SHA-256
`FD617C2C58DC4E8EC41E6AB96A51E8BD97CCFB07801309647A308DE14CA978B9`.

O perfil operacional permaneceu somente leitura nesta inspeção e não foi reparado fora do Core. O
EXE corrigido deverá executar a reconciliação normal ao ser aberto; se a IQ devolver histórico
truncado, incompleto ou ambíguo, o bloqueio permanecerá por segurança e exigirá o extrato/ID da
corretora, não edição manual do banco.

## 15. Recuperação automática sem alerta técnico ao cliente

A execução do build anterior contra o perfil real revelou dois contratos residuais. O histórico
binário da IQ usa `dir` para a direção, não apenas `direction`; por isso, linhas completas eram
classificadas como identidade incompleta. Além disso, depois de localizar um contrato pelo
fingerprint único, a camada do worker ainda exigia o `client_order_id` que a IQ não conserva nesse
histórico. O normalizador agora aceita `dir` e repõe a referência local somente depois de uma
correspondência única de ativo, direção, stake e instante, registrando a origem
`HISTORY_FINGERPRINT`. A camada seguinte pode então consumir essa identidade sem aceitar
correspondências parciais ou múltiplas.

A consulta fechada foi ampliada de 100 para 500 linhas. Mesmo quando a página atinge o limite, ela
pode provar cobertura se o registro mais antigo ultrapassa toda a janela temporal da submissão;
campos temporais incompletos continuam impedindo prova negativa. As duas observações independentes,
o intervalo mínimo e a liberação idempotente da reserva foram preservados.

O comando de ligar o bot agora persiste a intenção enquanto `HG_ORDER_UNKNOWN`,
`HG_RECONCILIATION_REQUIRED`, `HG_RECONCILIATION_UNAVAILABLE` ou `HG_SETTLEMENT_UNKNOWN` estiverem
em recuperação. O bot fica visivelmente “ARMADO · SINCRONIZANDO”, mas o gate financeiro permanece
fechado. Ao concluir a reconciliação, o Core muda a projeção para armado e as avaliações retomam sem
novo clique. O popup e o código técnico deixaram de aparecer nesse fluxo; falhas definitivas de
conta, risco ou banco continuam rejeitando a ativação normalmente.

Validação final: 1.476 testes aprovados, 4 skips previstos e zero falhas em 476,52 s; Ruff format e
check em 540 arquivos, mypy em 321 fontes, compileall, pip check e diff-check aprovados. O aviso
`WinError 5` ocorreu somente na limpeza temporária do pytest depois do exit code 0.

Build separado em `C:\tlb_iq_auto_recovery_20260911\TradingLab`: scanner zero segredos, manifesto
de 554 arquivos, hash informado pelo pipeline
`972590ba5c35ab4c8c71a181dc1cac62f6bb8e314c1b62279d6df63a03195d44`, integridade e health-check
aprovados. O executável onedir tem SHA-256
`05DBBC9FD29912B6957F47D2646046DB1E45AB545B5A2FFAD86DBDD8CDD10A31`. O portátil contém 1.000
entradas e um único recurso `TradingLab.payload.zip`, ProductVersion 1.9.11, 59.403.264 bytes e
SHA-256 `131B94417BC6326C2B4D6AADF474879572DA173C77A404B579C6403B979E9F0C`.

A instância operacional permaneceu aberta com oito processos durante todo o build. Ela, seu perfil,
credenciais, login e ordens não foram alterados. O portátil não foi iniciado por cima dessa
instância; seu payload e metadados foram verificados, e o health-check do onedir incorporado passou
no pipeline.
