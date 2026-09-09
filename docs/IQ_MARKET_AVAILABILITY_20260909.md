# IQ Option — disponibilidade real do produto turbo

## Evidência externa (Practice, somente leitura)

Em 09/09/2026, o operador autorizou testes. O aplicativo foi confirmado fechado e a sonda
adquiriu o mesmo profile.lock. Credenciais foram lidas exclusivamente do vault DPAPI.
Nenhum segredo, frame de autenticação ou payload de conta foi registrado.

- 13:05:29 UTC: GBPUSD-OTC respondeu em 750 ms; ID 81, nome exato, enabled=true,
  is_suspended=true, comissão numérica presente. O parser anteriormente converteu essa
  indisponibilidade explícita em IQOPTION_PAYOUT_UNAVAILABLE.
- 13:06:16 UTC: catálogo recebido em 781 ms. Nos 17 IDs estáticos conhecidos pelo conector:
  sete presentes/suspensos, nove ausentes e NZDUSD-OTC presente/não suspenso.
  Isso mede a disponibilidade desses IDs naquele instante, não todos os produtos da IQ Option.
- A terceira tentativa foi impedida pela guarda persistente de login antes da rede.
  A guarda não foi removida nem zerada.
- 13:22:06 UTC, após expiração natural da quarentena: NZDUSD-OTC retornou payout
  Decimal("0.82") em 797 ms, ID 80, enabled=true e is_suspended=false.
  Busca por nomes exatos no mesmo catálogo não encontrou IDs alternativos para os outros
  nomes suportados. Total externo desta investigação: três sessões autorizadas somente leitura;
  uma tentativa adicional bloqueada localmente; zero compras/vendas.
- A sessão anterior do EXE registrou um sinal GBPUSD-OTC às 09:35 BRT e bloqueio de payout;
  não houve nova ordem IQ no banco em 09/09. A suspensão medida depois não prova
  retroativamente o conteúdo exato daquela resposta anterior.

## Correção aplicada

- Separar IQOPTION_ACTIVE_SUSPENDED e IQOPTION_ACTIVE_UNAVAILABLE de resposta malformada
  ou payout inválido; preservar os códigos no IPC.
- Manter indisponibilidade negativa por símbolo por 60 s monotônicos. Não armazenar
  autorização de compra ou payout positivo nesse cache.
- Continuar aquecimento/análise, mas excluir temporariamente o símbolo indisponível da
  arbitragem. Outros símbolos continuam candidatos; após expiração exige consulta nova.
- Não tratar indisponibilidade de mercado como ordem rejeitada, não criar intenção,
  não desconectar a conta, não impor bloqueio global nem fabricar um sinal.
- Radar e cabeçalho explicam suspensão/ausência no produto turbo, em vez de zona neutra.
- Sonda opt-in em scripts/iqoption_payout_probe.py, allowlist própria de mensagens somente
  leitura; compra/venda/open-option e qualquer mensagem desconhecida falham antes do transporte.

Não houve mudança de produto para digital, remapeamento especulativo de IDs, redução de
limiar de estratégia ou remoção do Risk Ledger. Autorização para testar não garante que a
corretora aceitará uma ordem em produto suspenso.

## Limite da entrega

O operador autorizou ordens de teste, mas nenhuma foi enviada: a sonda é estritamente
somente leitura. A existência de payout em NZDUSD-OTC não comprova sinal elegível da
estratégia naquele ativo, aceite remoto ou settlement. Não se deve fabricar sinal,
usar outro ativo com a receita de GBPUSD ou trocar turbo por digital silenciosamente.
Com um único ativo disponível entre os 17 atuais, a frequência continuará limitada pela
disponibilidade do produto e pelas condições da estratégia.

## Segundo bloqueio: condições do manifesto

A receita local publicada f1:NZDUSD-OTC:M1:00-24:rsi_bollinger informa payout_min=0.85
e wilson_lower=0.557. Com o payout externo observado de 0.82:

- 0.82 < 0.85, portanto o piso publicado não é atendido;
- a exigência do gate é 1/(1+0.82)+0.015 = aproximadamente 0.564451;
  o Wilson publicado, 0.557, também não a atende.

Logo, corrigir disponibilidade não basta para autorizar essa receita nas condições medidas.
Reduzir apenas payout_min tampouco resolveria a segunda condição. Nenhum desses parâmetros
foi alterado. Isso requer validação de uma receita compatível ou ensaio experimental Demo
explicitamente separado do catálogo aprovado, sem promessa de vantagem estatística.

## Validação e artefato

- Suíte integral: 1.345 passed, 4 skipped, exit 0, 399,02 s.
- Depois da coleta inicial da suíte foram acrescentados os casos de texto do radar e do
  piso de payout observado: rodada final focada de disponibilidade/UI, 16 passed.
- Ruff check/format (519 arquivos), mypy (311 fontes incluindo a sonda), compileall e
  diff-check aprovados. Aviso conhecido não fatal de limpeza pytest-current no Windows.
- Build canônico final: 547 arquivos no manifesto; scanner de segredos, integridade e
  health-check aprovados. Primeira compilação preservada, substituída na entrega por
  segunda compilação incluindo o texto final do radar.
- Portátil abre e encerra em perfil isolado/headless: exit 0, stdout/stderr vazios,
  quick_check=ok, zero intenção/reserva/outbox/ordem, nenhum processo residual.
- Entrega: dist/iq-availability-release-20260909/TradingLab-Desktop-v1.9.11-IQ-AVAILABILITY-FIX.exe.
- SHA-256 portátil: FE3013073567411DB05E69E743321D4477E8800C0B1C6970D25EAB605951C050.
- SHA-256 payload (igual ao recurso incorporado): CD2F497BBD9BB397D205B56BEE43A877A8C1A18406098B35B4F5ED06C731358B.
- SHA-256 onedir: E8BC698C803B9587A92E8060332EA542BC5E673A92C772D23C99A61990E1C562.

Classificação: correção local validada + payout externo confirmado, não execução externa
validada. Nenhuma ordem externa, alteração de manifesto assinado, commit/push ou limpeza de
histórico foi realizada. As consultas consumiram o orçamento real de login e respeitaram
a quarentena existente; não houve reinício automático do aplicativo do operador.
