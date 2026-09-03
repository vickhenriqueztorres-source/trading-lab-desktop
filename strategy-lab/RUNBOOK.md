# RUNBOOK Operacional — Strategy Lab & Hub (R-OPS-1..4)

**Baseline:** v1.9.11  
**Atualizado em:** 2026-09-03  
**Destinatário:** Operador Técnico e Mantenedor Quantitativo

Este Runbook documenta as rotinas de operação sem toque, a matriz de resposta para todos os **11 pontos de falha da Arquitetura §9**, os procedimentos de contingência imediata para incidentes operacionais, a migração para VPS Linux e a rotação criptográfica planejada de chaves.

---

## 1. Rotinas Operacionais Periódicas

### 1.1 Rotina Diária (~2 minutos)
1. **Verificação do Coleta**:
   - No Windows, verificar se as tarefas `TradingLab-Collect-Morning` (07:30) e `TradingLab-Collect-Evening` (19:30) foram concluídas com êxito.
   - Executar:
     ```powershell
     .\.venv\Scripts\python.exe -m strategy_lab.cli status
     ```
   - O payload JSON retornado deve exibir `"status": "ok"` e `"gaps_detected": 0`.
2. **Notificações**: Se houver qualquer alerta (canário falhou ou salto de cotação), a tarefa agendada `TradingLab-Status-Daily` dispara uma notificação Toast na área de trabalho.

### 1.2 Rotina Semanal (~5 minutos)
1. **Backup da Base de Dados**:
   - A tarefa de domingo às 08:00 executa `strategy-lab backup`.
   - Verificar a criação do snapshot em `research/backups/backup_YYYYMMDD.sqlite` ou dump do PostgreSQL:
     ```powershell
     .\.venv\Scripts\python.exe -m strategy_lab.cli backup
     ```
2. **Revisão de Desempenho ao Vivo (SPRT)**:
   - Checar se alguma estratégia em produção foi rebaixada para `"observation"` pelo monitor SPRT.

### 1.3 Rotina Mensal (~1 hora de máquina, 15 min do operador)
1. **Pesquisa e Revalidação Quantitativa**:
   - Executar a esteira de pesquisa com seed determinística:
     ```powershell
     .\.venv\Scripts\python.exe -m strategy_lab.cli research --seed 202609
     ```
2. **Inspeção do Relatório**:
   - Abrir o arquivo gerado `research/runs/<run_id>/ranking.md`.
   - Avaliar a tabela ordenada por Score, verificando taxa $\hat{p}$, Wilson 95%, pior sequência de perdas e a seção `## Novas oportunidades`.
3. **Publicação do Novo Manifesto**:
   - Publicar com chave A assinada:
     ```powershell
     .\.venv\Scripts\python.exe -m strategy_lab.cli publish --run-id <run_id> --key-id A
     ```
   - Digitar o número exato de estratégias exigido na confirmação de terminal (sem `--yes`).
4. **Validação no Bot**:
   - Abrir o Trading Lab Desktop em modo Demo (Practice).
   - Verificar na tela de Estratégias se o novo manifesto foi recebido (notificação "Novo manifesto v{n} aplicado com sucesso!").

---

## 2. Matriz de Falhas e Ações Concretas (Arquitetura §9)

| # | Ponto de Falha | Diagnóstico / Sintoma | Ação Concreta Imediata |
|---|---|---|---|
| **1** | **API não oficial quebra** | `CANARY_FIXTURE_MISMATCH` ou timeout no websocket da corretora. | O canário aborta automaticamente sem gravar lixo no banco. Acione o vendor isolado (`vendor/iqoptionapi`), verifique atualizações upstream no repositório de patch, rode a suíte `pytest tests/test_iq_vendor_backend.py`. Após a correção, o backfill M1 recupera as horas faltantes pelo watermark idempotente. |
| **2** | **Lookahead / defeito do simulador** | Falso edge detectado em backtest, mas colapso instantâneo em live. | O teste da moeda (`test_coin_flip_approves_zero`) bloqueia o CI se o simulador aprovar séries aleatórias. Verifique a invariante I-4: a decisão no timestamp $t$ só pode consumir velas até $t$, com sinal aplicado na abertura de $t+60$ e payout correspondente à hora de decisão. Aplique a penalidade pessimista de $-1,0\text{ pp}$ em `approve_candidate`. |
| **3** | **Indicador Lab $\ne$ Bot** | `PRIMITIVES_PARITY_MISMATCH` reportado no log do bot desktop; manifesto é recusado. | As implementações são 100% isoladas. Execute o vetor público de 10.000 velas com `pytest packages/primitives/tests/parity/test_primitives_parity_hash.py`. O hash SHA-256 no manifesto deve coincidir bit a bit com a constante do bot. Se um primitivo foi alterado, execute o checklist de release do bot antes de publicar manifesto. |
| **4** | **Payout da corretora varia** | Ordem bloqueada com `PAYOUT_BELOW_VALIDATED_EDGE`. | Comportamento normal e fail-closed do `payout_gate`. O bot impede compras com margem de segurança negativa. Na pesquisa, verifique se a grade horária de payouts (`PayoutLookup`) tem amostras para o ativo e horário. Se a corretora reduziu payout estruturalmente abaixo do limiar, a estratégia permanecerá pausada até o retorno das condições favoráveis. |
| **5** | **Dados corrompidos / DST / Vela corrente** | Gap inesperado de 60 s ou vela incompleta gravada. | Velas correntes em formação **nunca** são persistidas (invariante R-COL-5). Timestamps usam exclusivamente epoch UTC (`from_ts % 60 == 0`). Em caso de salto de cotação $> 8 \times \text{ATR}(14)$, o status emite alerta. Execute a limpeza e re-backfill: `strategy-lab collect --backfill --from-ts <ts>`. |
| **6** | **Incidente de Manifesto (chave, versão, conflito)** | Bot reporta `MANIFEST_SIGNATURE_INVALID` ou erro HTTP 409 em `publish`. | Em caso de 409, a versão publicada já existe ou é regressiva: incremente `manifest_version`. Em caso de assinatura inválida, verifique se `~/.strategy-lab/keys/{A,B}.pem` não foi corrompida. O preflight local reproduz 100% da verificação do bot antes do upload. |
| **7** | **Supabase Hub indisponível** | Bot não consegue baixar `manifests/current.json` (500 ou timeout). | O bot mantém o manifesto em cache local (válido por 45 dias) e tenta automaticamente o espelho secundário no Cloudflare R2 (`R2_MIRROR_URL`). A fila local do bot (`outcomes_uploader`) armazena resultados em SQLite e retenta em background sem bloquear as operações. |
| **8** | **Overfitting sobrevivente** | Candidato com win rate inflado passa nos testes iniciais. | O pipeline aplica FDR Benjamini-Hochberg sobre o total real $N$ de combinações avaliadas, perturbação de hiperparâmetros ($\pm 15\%$), holdout selado de 3 meses e quarentena obrigatória em conta Demo (`status="observation"` por 30 dias ou 200 operações). |
| **9** | **Mudança de regime de mercado** | Perdas consecutivas em estratégia aprovada em Real. | O monitor SPRT ao vivo (`live_monitor.py`) detecta a degradação entre 60 e 120 operações, rebaixando automaticamente a estratégia para `observation` e disparando evento `strategy_demoted`. O `RiskLedger` do bot impõe Stop Loss diário rígido, acotando a perda máxima da sessão. |
| **10** | **Erro humano operacional** | Publicação acidental de estratégia incorreta ou exclusão de ativo. | O comando `publish` exibe o diff estruturado (adições, remoções, alterações de parâmetros) e **obriga** o operador a digitar o número exato de estratégias publicadas no terminal. A flag `--yes` é permanentemente desabilitada. Toda publicação gera backup automático e snapshot versionado em `manifests/v{n}.json`. |
| **11** | **Conta IQ Option de coleta bloqueada** | Erro de autenticação 401/403 ou CAPTCHA persistente na VPS. | **A conta de coleta é 100% isolada das contas reais de operação**. A coleta executa apenas 1 a 2 logins por dia com IP residencial ou proxy dedicado com rotação realista. Em caso de ban, cadastre uma nova conta auxiliar de visualização (apenas leitura de cotações), atualize `/etc/strategy-lab/env` e reinicie o timer. |

---

## 3. Resolução de Incidentes Específicos

### 3.1 Canário Falhou (`CANARY_FIXTURE_MISMATCH`)
1. **Causa**: O websocket da corretora retornou velas diferentes da assinatura histórica conhecida, indicando mudança no formato da API ou payload corrompido.
2. **Ação**:
   - O `collect` aborta imediatamente sem gravar nenhuma linha no banco de dados.
   - Execute o teste em isolamento:
     ```powershell
     .\.venv\Scripts\python.exe -m pytest tests/test_collect_p04.py -k test_canary_fixture_matches -v
     ```
   - Inspecione a resposta do vendor em `logs/canary.log`. Se a corretora alterou o formato de mensagem websocket, adapte o parser em `strategy-lab/vendor/iqoptionapi/` e revalide os testes.

### 3.2 API Mudou / Atualização de Vendor
1. Clone a branch de atualização do adapter.
2. Execute a suíte de compatibilidade do vendor:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_iq_vendor_backend.py tests/test_iq_client.py -v
   ```
3. Realize um dry-run da coleta para certificar-se da integridade das velas:
   ```powershell
   .\.venv\Scripts\python.exe -m strategy_lab.cli collect --dry-run
   ```

### 3.3 Projeto Supabase Pausado (Plano Gratuito)
1. **Sintoma**: Falha de conexão na Edge Function ou no banco de dados Postgres (`connection refused`).
2. **Ação**:
   - Acesse o painel web do Supabase e clique em "Restore Project".
   - Enquanto o banco restaura:
     - O bot desktop continua funcionando normalmente a partir do cache local `manifest.json`.
     - Os clientes continuam operando ordens abertas.
     - As execuções acumuladas ficam seguras na fila SQLite local do bot (`outcomes_queue`).
   - Assim que o projeto reativar, a fila SQLite local descarrega os resultados automaticamente.

### 3.4 Estratégia Rebaixada pelo SPRT (`STRATEGY_DEMOTED_BY_SPRT`)
1. **Comportamento Automático**: O bot altera o estado local da estratégia para `observation`.
2. **Ação do Operador**:
   - Nenhuma ação manual é necessária no bot; a estratégia não fará mais entradas em conta Real.
   - Na rotina mensal de pesquisa, os resultados ao vivo de `live_outcomes` entrarão como janela de teste fora da amostra (`live_merge.py`). Se a estratégia não demonstrar mais vantagem estatística contra a hipótese nula, ela será automaticamente descartada do próximo manifesto.

### 3.5 Manifesto Rejeitado no Bot
1. **Diagnóstico**: Inspecione os logs do bot em `%APPDATA%\TradingLab\logs\trading_lab.log` procurando pelo evento `manifest_rejected`.
2. **Causas Comuns e Soluções**:
   - `INVALID_SIGNATURE`: A chave privada usada no publish não corresponde às chaves públicas embutidas A ou B.
   - `PARITY_HASH_MISMATCH`: Os primitivos do bot são de versão diferente da do Strategy Lab. Atualize o aplicativo desktop para a versão correspondente.
   - `REGRESSIVE_VERSION`: O manifesto publicado tem `manifest_version` menor ou igual ao já em vigor.
   - `PAYOUT_MIN_VIOLATION`: Algum parâmetro de payout está fora dos limites 0.70..0.95.

### 3.6 Comprometimento da Chave Privada A $\to$ Ativação Imediata da Chave B
O sistema nasce com arquitetura dual de chaves: o bot possui as chaves públicas **A** e **B** embutidas desde o primeiro build.
1. **Corte da Chave A**:
   - Descarte imediatamente a chave privada local `~/.strategy-lab/keys/A.pem`.
2. **Publicação de Emergência com Chave B**:
   - Publique o manifesto assinado exclusivamente com a Chave B:
     ```powershell
     .\.venv\Scripts\python.exe -m strategy_lab.cli publish --run-id <run_id> --key-id B
     ```
3. **Comportamento do Bot**:
   - O `manifest_client` valida a assinatura contra a Chave Pública B.
   - Como a assinatura é válida e a versão é incrementada, o novo manifesto é aceito e substitui o anterior imediatamente.

### 3.7 Restauração de Backup do Banco de Dados
1. **Restaurar SQLite Local**:
   - Localize o snapshot desejado em `research/backups/`.
   - Copie sobre o arquivo de trabalho:
     ```powershell
     Copy-Item "research/backups/backup_20260901.sqlite" "research/data/strategy_lab.sqlite" -Force
     ```
2. **Restaurar PostgreSQL (Supabase)**:
   - Restaure via `psql`:
     ```bash
     psql "$SUPABASE_DB_URL" < research/backups/supabase_dump_20260901.sql
     ```

---

## 4. Migração Passo a Passo para VPS Linux

Para migrar a coleta de dados e geração horária de payouts para uma VPS Linux (Ubuntu 22.04/24.04 ou Debian 12):

1. **Provisionamento da Máquina**:
   - Instância básica (1 vCPU, 2 GB RAM, 20 GB disco).
   - Configure acesso SSH com autenticação por chave pública.
2. **Transferência dos Arquivos de Deploy**:
   ```bash
   scp -r deploy/vps/ root@vps-ip:/tmp/vps-deploy/
   ```
3. **Execução do Instalador**:
   ```bash
   ssh root@vps-ip
   cd /tmp/vps-deploy
   chmod +x install.sh
   ./install.sh
   ```
4. **Configuração das Credenciais**:
   ```bash
   nano /etc/strategy-lab/env
   ```
   - Preencha `IQ_EMAIL`, `IQ_PASSWORD`, `SUPABASE_DB_URL`, `SUPABASE_URL` e `SUPABASE_SERVICE_ROLE_KEY`.
   - Confirme as permissões:
     ```bash
     ls -la /etc/strategy-lab/env
     # Saída obrigatória: -rw------- 1 strategylab strategylab ...
     ```
5. **Verificação dos Serviços**:
   ```bash
   systemctl status strategy-lab-collect.timer
   systemctl status strategy-lab-payout.timer
   systemctl status strategy-lab-backup.timer
   ```
6. **Teste Manual Inicial**:
   ```bash
   systemctl start strategy-lab-payout.service
   journalctl -u strategy-lab-payout.service -n 50 --no-pager
   ```

---

## 5. Rotação Planejada de Chaves Ed25519

Quando uma chave pública precisa ser aposentada de forma planejada:

1. Gere o novo par de chaves Ed25519 para a chave C:
   ```bash
   openssl genpkey -algorithm Ed25519 -out ~/.strategy-lab/keys/C.pem
   chmod 0600 ~/.strategy-lab/keys/C.pem
   ```
2. Adicione a nova chave pública C em `apps/core/manifest_keys.py` no aplicativo Desktop para a próxima versão planejada do bot.
3. Publique os manifestos usando a Chave B (que já está em todos os clientes).
4. Após o período de migração dos clientes para o novo build do desktop, desative a Chave A antiga e promova a Chave C como reserva operacional.
