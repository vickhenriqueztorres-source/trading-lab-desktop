# CAT-14 — Consumo do manifesto no runtime do bot

Data da validação: 2026-09-06.

## Resultado

O `ManifestClient` existente agora faz parte do ciclo de vida normal do Core. O processo iniciado
pelo EXE recebe o token efêmero da UI e inicia um serviço de atualização em thread própria. Testes
e execuções headless permanecem sem rede por padrão, salvo habilitação explícita.

Esta etapa não habilita uma semântica de execução ainda não suportada. O executor atual continua
aceitando somente `legacy.bot.window-replay.v1`; manifestos `tl.candle-close.v2` são recusados com
`MANIFEST_EXECUTION_SEMANTICS_UNSUPPORTED` até existir compilador e paridade completos no bot.

## Canal, fallback e orçamento

- Canal primário: Edge Function pública `manifest_current` do projeto Supabase configurado.
- Fallback compatível: objeto público `manifests/current.json` no Storage do mesmo projeto.
- Ambos exigem HTTPS, hostname válido e ausência de credenciais embutidas ou fragmentos.
- Frequência estável: um ciclo a cada 15 minutos, com jitter limitado de ±10%.
- Backoff de falha: 15 segundos, exponencial e limitado a 15 minutos.
- Orçamento: no máximo 4 ciclos em janela deslizante de uma hora.
- Cada ciclo usa no máximo dois GETs (primário e fallback), portanto o teto é 8 GETs/hora.
- Pressão e esgotamento do orçamento geram telemetria própria.
- ETag é mantido por origem. Resposta `304` conserva o último manifesto aceito.

Não existe GET, consulta remota ou leitura de banco no ciclo de sinal, admissão ou submissão
financeira. A atualização ocorre apenas no serviço de background.

## Validação antes da troca

Antes de tornar uma versão visível, o cliente valida:

1. limite de tamanho e JSON íntegro;
2. schema e assinatura Ed25519 com chave pública de produção conhecida;
3. monotonicidade da versão;
4. relógio do servidor e validade temporal;
5. versão do engine, primitives, semântica de execução, parâmetros e recursos;
6. preparação das instâncias e caches do catálogo fora do lock de execução.

Somente depois dessas etapas o catálogo troca de geração atomicamente. Erro de validação,
preparação ou commit mantém a versão anterior e seu cache sem expor geração parcial.

## Ordens abertas, retiring e rearme

- Ordem e monitoramento em andamento mantêm o contexto da revisão original até o estado terminal.
- Estratégia removida ou alterada com ordem aberta entra em `retiring`: não recebe nova entrada,
  mas continua disponível para settlement e reconciliação.
- Uma troca válida invalida a autoridade de entrada e desarma somente o bot IQ Option.
- O bot Deriv não é armado, desarmado nem alterado pela atualização do catálogo IQ Option.
- Nenhum catálogo recebido pela rede rearma trading automaticamente.

## Cache e restart

O último manifesto aceito é gravado por substituição atômica em arquivo separado de credenciais.
No restart, bytes truncados, assinatura inválida ou manifesto incompatível são recusados. A janela
offline do cache não amplia a elegibilidade da estratégia: expiração do manifesto continua sendo
aplicada no instante exato pelo catálogo.

## Evidência e limitações externas

Os testes com transportes controlados cobrem `304`, timeout, divergência entre canal e fallback,
relógio inválido, falha de preparação, troca concorrente, limite de polling, rollback monotônico e
remoção de estratégia com ordem aberta. A suíte completa do projeto terminou com **1287 testes
aprovados e 4 opcionais ignorados**.

Na verificação pontual de 2026-09-06, os endpoints reais responderam, mas não havia manifesto
publicado após a limpeza do smoke CAT-13: o canal retornou `503` e o objeto compatível retornou
`400`. Portanto, o transporte real foi alcançado, mas uma atualização remota bem-sucedida não é
declarada nesta etapa. O mirror independente R2 continua dependendo de URL/configuração própria;
o fallback padrão atual é o objeto público compatível do Supabase.

Nenhuma corretora, conta financeira, credencial de broker ou ordem foi usada nesta validação.
Nenhum build de EXE foi gerado na CAT-14.
