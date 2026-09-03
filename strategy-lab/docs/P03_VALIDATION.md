# P03 — adaptador IQ Option isolado

Data: 2026-09-02. Requisitos: R-VEND-1..3; isolamento R-ISO-1..6 preservado.

**Veredito: implementação local validada; aceite de coleta real pendente.**

## Escopo e autorização

O operador autorizou patches mínimos de segurança, lock fora do snapshot e coleta
real como etapa manual com credencial própria do Lab. Não foram usados login
anterior do EXE, vault do bot, credenciais Supabase, contas ou dados privados.
Não houve requisição à IQ Option nem ordem. Rede foi usada somente para consultar
e obter código público/dependências, não durante a suíte.

O candidato iqoptionapi/iqoptionapi@8a903cc não trouxe licença explícita. Sua cópia
de análise foi movida para state/rejected-upstream-8a903cc, ignorada e não importada.
Foi adotado [victalejo/iqoptionapi](https://github.com/victalejo/iqoptionapi),
que declara MIT, no commit acac6e08333466ae188c7dfa7fd2a03174e34ca2.
Essa escolha não constitui certificação de segurança ou funcionamento externo.

## Entregas e prova de aceite

| Critério | Resultado | Evidência |
|---|---|---|
| Vendor integral, licença e pin | PASS local | 86 arquivos upstream, LICENSE byte-idêntico, UPSTREAM_COMMIT |
| Diff vazio upstream | Substituído por autorização | Exatamente 3 fontes alteradas, 83 idênticas; PATCHES.md, diff e hashes antes/depois |
| Único importador próprio | PASS | test_vendor_import_boundary; scan AST do Lab e controles positivos diretos/dinâmicos |
| IQClientProtocol, IQClient, FakeIQClient, Candle | PASS | testes de contrato; Candle é reexportado, sem cópia do modelo |
| Decimal, vela inválida, OTC e pacing | PASS | test_iq_client.py; lote all-or-nothing, jitter injetável 0,5–2s |
| Proteção do transporte | PASS local | test_iq_vendor_backend.py executa componentes reais do vendor com I/O fake |
| Recorder de até 1.000 M1 | PASS local | 1.000 linhas sintéticas apenas em tmp_path; hash, cobertura, falha de disco e não sobrescrita |
| Fixture real 1 ativo × 1.000 velas gravada/commitada | NOT EXECUTED | Sem credencial própria de coleta usada/disponibilizada nesta etapa |
| Isolamento do EXE | Preservado | Alterações só em strategy-lab; nenhum import ou leitura de perfil do bot |

O teste de integridade detecta arquivo alterado/injetado antes da importação.
O snapshot é uma referência local verificada por hash, não assinatura anti-invasor.
As pastas de testes/exemplos do vendor permanecem copiadas, mas não são executadas
pela suíte nem pelo adaptador. Ruff/mypy não reformatam o terceiro.

## Transporte e dados

- Reutiliza classe low-level, recurso HTTP de login e construtores de canais do vendor.
- Não chama connect/start_websocket legados, stable_api ou handlers financeiros.
- Somente HTTPS login no host fixo, authenticate, catálogo e get-candles; demais
  mensagens/URLs negadas antes do envio. Sem compra, venda ou mudança de conta.
- HTTP sem redirects, TLS HTTP/WS validado, timeout de requisição/socket e espera monotônica.
- Callbacks próprios; sem reautenticação/retry automático. Thread persistente após
  shutdown gera IQ_SHUTDOWN_INCOMPLETE, não sucesso falso.
- Não solicita nem retém profile/saldo. JSON bruto não é logado; debug/trace do vendor
  não chega aos logs do Lab. Exceções externas são convertidas sem seu conteúdo.
- Preços chegam como Decimal no decode, inclusive números JSON fracionários; NaN,
  Infinity e chaves duplicadas são rejeitados. Payout é ratio líquido M1/turbo:
  (100 - commission) / 100. Não usa o cálculo float de get_all_profit.
- IDs vêm do catálogo; -OTC é preservado. Não se substitui payout de outro produto.
- InvalidCandleError expõe somente chaves numéricas de preço seguras em payload.
  Metadados não-preço e texto arbitrário são removidos explicitamente.
- Gravação exige M1 [from,to), cobertura exata, ordem e fechamento. Nenhuma escrita
  ocorre com lote inválido; arquivo existente nunca é sobrescrito.

## Comandos e resultados finais

No ambiente .venv exclusivo do Lab:

```powershell
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m compileall -q tools/strategy_lab packages
.venv/Scripts/python.exe -m pip check
.venv/Scripts/strategy-lab.exe --help
git diff --check
```

- pytest: **240 passed**, 25,99s (150 anteriores + 90 P03).
- Ruff e format: aprovados no diretório do Lab.
- mypy strict: aprovado, 37 fontes.
- compileall, pip check, CLI --help e git diff --check: aprovados.
- Scan heurístico de JWT, service key e chave privada em tools/tests/vendor: sem matches.
  Não substitui auditoria independente; testes adicionais verificam redação em falhas.
- Instalação editável do CLI aprovada. Setuptools 80.9.0 já previsto no lock precisou
  ser reinstalado usando prefixo Windows de caminho estendido: instalação comum
  falhou com WinError 206 (caminho longo). Nenhuma versão do produto foi alterada.

git diff --check não cobre arquivos untracked: o Lab continua untracked como no início.
Não houve commit/push automático, build do EXE, integração Supabase ou deploy.
O CLI P03 é suportado no checkout com instalação editável; um wheel standalone com
vendor incorporado ainda não foi preparado.

## Hashes SHA-256

| Artefato | SHA-256 |
|---|---|
| Archive upstream acac6e0 | ad86660b8d8691e966b655cb601f2f853b17975a3364684aed1abdaf7f755a38 |
| vendor/iqoptionapi.integrity.json | 6aaf4d1fc0533514f2ab3defc8ec6d25339abe959452e426c491f8c95a26897b |
| vendor/iqoptionapi.security.patch | 9e0462c19aa33934b7756fe01a593aa1849377023279aa56d525a2e3f4f3c3e0 |
| tests/fixtures/iq/synthetic-EURUSD-OTC.json | 5513f216fb47889d3f3dfbf2bf469ff29fcee6c2caeb2a33098b0369468ac769 |

## Pendências e limitações

1. Cadastrar credencial exclusiva de coleta e executar o recorder manualmente.
   Instruções em tests/fixtures/iq/README.md. Não colar senha na conversa.
2. Inspecionar a fixture real, comprovar 1.000 velas/cobertura/hash e então versioná-la.
   Dados sintéticos nunca substituem este aceite.
3. Não validado: login externo, MFA/challenges, compatibilidade atual do catálogo
   remoto, disponibilidade de histórico e shutdown com rede real.
4. P04: NTP, canário real, cota diária persistente de login, backfill e Supabase.
   Os testes de moeda/canário/DST futuros não foram inventados para declarar aprovação.
5. Sem mudança em indicadores, manifesto P02, estratégias, gestão de risco ou EXE.
   A suíte do produto principal não foi rodada: nenhum código seu foi alterado.

### Ocorrência durante a conferência final

Uma chamada de verificações estáticas foi executada inadvertidamente no diretório pai
com seu ambiente, somente leitura. Mypy do pai passou; Ruff encontrou 13.584 erros,
incluindo o arquivo preexistente docs/## Arquitetura.py, que contém Markdown.
Nenhum arquivo do pai foi modificado; esses resultados não são usados no aceite
do P03. A conferência foi repetida no diretório/ambiente correto do Lab e passou.
