# IQ Option — payout, recuperação e projeção da UI

Data: 09/09/2026 BRT. Escopo: correção local, sem login ou ordem externa.

## Resultado por problema

| Problema | Correção | Prova local |
| --- | --- | --- |
| Payout global descartado por falta de request_id | Via exclusiva para initialization-data, com uma consulta em voo, prazo total monotônico e fila limitada. Respostas sem ID ou com o ID esperado são aceitas; outro ID é ignorado. Nenhuma correlação financeira é relaxada. | Respostas globais e correlacionadas retornam Decimal("0.85"); comissão mantém todos os dígitos; ativo exato, enabled e is_suspended continuam obrigatórios. |
| Catálogo de inicialização grande derruba o WebSocket | Limite explícito de 8 MiB por mensagem e max_queue=4. O fechamento 1009 é apresentado como IQOPTION_RESPONSE_TOO_LARGE. | WebSocket real em loopback: catálogo de 2 MiB aceito, acima de 8 MiB recusado, sem acesso à corretora. |
| Recuperação falsa com IPC READY e saldo antigo | Invalidação da sessão externa limpa projeções e autorização; recovery substitui supervisor/cliente. O atalho manual de conexão verifica saldo no worker, que exige sessão externa viva. Falha durante recovery invalida também a tentativa em andamento. | Cliente antigo com IPC READY e sessão morta é substituído; callback da geração antiga não derruba a nova; bot permanece desarmado. |
| Causa externa apresentada como erro de envelope | Códigos ausentes foram incluídos na allowlist; erro externo desconhecido vira IQOPTION_EXTERNAL_ERROR, sem texto bruto. Erro realmente protocolar continua protocolar. | Testes de códigos estáveis e sanitização; mensagens correspondentes em português na UI. |
| Explosão de ASSET_MISMATCH no AUTO | Um resumo por símbolo/época, com rejected_count, substitui os eventos individuais de candidatos incompatíveis. Decisões de avaliação, payout e ordem são preservadas. | Quatro símbolos e quatro entradas: quatro resumos com contagem três, em vez de doze eventos individuais. Replay AUTO de 24 h mantém as contagens por época. |
| Tabelas reconstruídas sem mudança | Radar e livro de ordens reaproveitam a projeção quando seu conteúdo é idêntico. Mudança de resultado ou idioma continua redesenhando a tabela. | Identidade das células preservada por 100 atualizações; settlement posterior aparece como WON. |

## Limites e decisões importantes

- O relato anterior apontava timeout de dois segundos, mas não capturou o frame bruto da
  corretora para comprovar seu tamanho ou ausência de ID naquela sessão específica. Os dois
  formatos e o limite de tamanho foram reproduzidos localmente, não atribuídos retroativamente
  à corretora como evidência observada.
- O timeout de payout continua em dois segundos, consistente com a validade conservadora do
  ticket, que começa antes da consulta. Simplesmente aumentar o timeout faria a resposta chegar
  com ticket vencido. Não aumentamos a idade aceitável do payout nem usamos payout antigo.
  Se a corretora continuar respondendo fora desse prazo, o caso exige medição externa e desenho
  separado de atualização antecipada; não é justificativa para enviar sem cotação válida.
- Sem ID, uma resposta atrasada não pode satisfazer consulta posterior: timeout invalida a
  geração. Não existe retry de compra. A recuperação existente é limitada, com backoff e rearme
  manual; ordens UNKNOWN mantêm a semântica financeira anterior.
- A geração IPC passa a usar UUID, evitando reaproveitar cache por reutilização de id() do Python.
  Saldo, relógio e indicadores são invalidados quando muda o cliente.
- Não houve alteração de estratégia, fórmula, limiar, payout mínimo, guarda Real, manifesto,
  banco remoto ou código vendorizado do Strategy Lab. A Deriv permanece independente.
- O benchmark local do componente usou 437 linhas e 30 atualizações: 1,25 s de CPU forçando
  reconstrução; abaixo da resolução do relógio para snapshots idênticos com cache. Isso não é
  medição da CPU do aplicativo completo nem promessa de queda de 54% para zero.

## Validação

- Suíte inteira do bot: **1.335 passed, 4 skipped**, exit 0, em 381,71 s.
- Ruff check aprovado; Ruff format: 517 arquivos; mypy: 310 fontes; compileall e diff-check aprovados.
- Dezessete regressões novas em test_iqoption_session_regressions.py. Fixtures antigas foram
  adaptadas à via exclusiva de inicialização e ao estado explícito de sessão invalidada.
- Replay de 24 h: 48 aceitações e 24 rejeições simuladas, 1.368 épocas sem envio,
  zero correlações duplicadas. Não são resultados de negociação externa.
- O aviso conhecido do pytest sobre permissão ao limpar pytest-current ocorreu depois do
  resumo aprovado; não afetou exit code ou testes.
- Build canônico: 547 arquivos no manifesto, zero segredos detectados, integridade e health
  aprovados. Portátil com 957 entradas no ZIP e recurso incorporado idêntico ao payload.
- Smoke portátil em perfil isolado, headless e worker simulado: exit 0, stdout/stderr vazios,
  quick_check=ok; zero intenções, reservas, outbox, ordens e eventos financeiros.
  Encerramento registrado no journal e nenhum processo TradingLab residual.

## Artefato entregue

Portátil: [TradingLab-Desktop-v1.9.11-IQ-SESSION-FIX.exe](../dist/iq-session-release-20260909/TradingLab-Desktop-v1.9.11-IQ-SESSION-FIX.exe).

- SHA-256 portátil: C21B869826E9E1EECE51AB2D1FFCA79AAEE83582207CB9FAB6A05096D1F7919E.
- SHA-256 payload: B265680B03A69F22DB7FC9E318148AB7D7648C801097EEC9A9C5D42302BFF368.
- SHA-256 onedir: 6171B56DF00D258CA59EC20735BC4F60FA34814017B55186AF52AD11F86DE6D2.

O build foi realizado em caminho curto C:\tlb\iq-session-20260909-final1 e copiado para a
nova pasta de release, preservando os artefatos anteriores. A primeira chamada do compilador C#
recusou o caminho relativo com separador Unix; a repetição com caminho absoluto Windows passou.

Classificação: **LOCAL_FIX_VALIDATED**. A validação externa de abertura/liquidação e estabilidade
prolongada não foi executada nesta entrega. Nenhuma operação externa foi provocada.
