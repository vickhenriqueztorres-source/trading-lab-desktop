# Segurança — DualTrade Desktop

**Status:** política obrigatória da Fase 1  
**Escopo:** código, testes, fixtures, logs, documentação, build, suporte e integrações

## 1. Objetivo

Este documento consolida o modelo de ameaças e os controles de segurança do DualTrade Desktop. Em
caso de conflito, prevalecem [AIGUARD.md](AIGUARD.md), [RULES.md](RULES.md), o
[PRD](PRD_Trading_Desktop_Deriv_IQOption.md) e a
[arquitetura](Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md).

Segurança neste projeto inclui confidencialidade, mas também integridade financeira, preservação de
estado, isolamento entre corretoras e disponibilidade para acompanhar ordens já abertas.

## 2. Estado de segurança da fase atual

- a conexão Deriv Real existe somente após seleção/confirmação explícitas e prova oficial do tipo
  da conta, mas opera read-only: não cria sessão de ordens, não anuncia `can_submit_orders` e não é
  anexada ao dispatch financeiro do Core;
- o worker financeiro simulado permanece o padrão local;
- Deriv inicia read-only; Demo/Real live exigem seleção interna, conta e OTP oficiais do mesmo tipo;
  somente Demo pode compor a sessão financeira e ainda permanece sem auto-trader nesta fatia;
- Auth Agent, UI e Simulated Worker recebem ambiente sanitizado sem variáveis de credencial Deriv
  ou IQ; somente o subprocesso Deriv herda a configuração externa necessária ao opt-in;
- IQ Option não está implementada;
- Auth Agent, identidade e lease são simulados localmente; o vault Windows CurrentUser é
  persistente e executável;
- `SecretValue` reduz exposição acidental em `repr`/`str`, mas não protege memória;
- `WindowsUserScopedVault` usa DPAPI CurrentUser, integridade em duas camadas, escrita atômica e
  DACL protegida para o SID atual; falha não regride silenciosamente para simulação;
- IPC do Auth Agent usa token efêmero entregue por pipe e prova HMAC mútua; IPC de workers continua
  com handshake de role/capability, sem autenticação criptográfica de peer;
- IPC lifecycle Launcher/Core também usa token efêmero por pipe e prova HMAC; no Windows, Job Object
  com `KILL_ON_JOB_CLOSE` contém Core, Auth Agent e workers;
- telemetria remota não existe.
- `SecretScanner` faz varredura local bounded de código, fixtures e relatórios, sem retornar o valor
  encontrado; ele não substitui revisão, rotação ou scanner de dependências.

Esses limites impedem tratar a Fase 1 como distribuição segura para operação comercial.

## 3. Ativos protegidos

| Ativo | Impacto principal |
|---|---|
| estado de ordens e reconciliação | duplicidade, abandono ou classificação incorreta |
| reservas e limites de risco | exposição acima do permitido |
| `state.db` e migrations | perda de evidência financeira |
| `strategy_data.db`, journal e checkpoint | replay divergente ou evidência adulterada |
| credenciais/sessões de broker futuras | acesso indevido a contas externas |
| refresh token, device key e lease | sequestro de sessão/licença |
| manifestos e código de estratégia | execução não autorizada ou incompatível |
| protocolo IPC e subprocessos | comando forjado, replay ou payload excessivo |
| logs e pacote de diagnóstico | vazamento de segredo ou dado operacional |
| instalador local/atualização | execução de binário adulterado |

## 4. Fronteiras de confiança

```text
Serviço de identidade futuro
  └── recebe somente identidade/dispositivo/licença
      NUNCA recebe credencial de broker ou histórico financeiro completo

Desktop do usuário
├── UI futura                  (não autoritativa)
├── Auth Agent                 (sessão/lease, sem credencial de broker)
├── Trading Core               (única autoridade financeira local)
├── Deriv Worker               (protocolo Deriv isolado)
├── IQ Option Worker futuro    (credencial/sessão IQ confinada)
├── state.db                   (estado financeiro crítico)
└── strategy_data.db           (market data e evidência)
```

Dados que cruzam uma fronteira devem usar modelos imutáveis, envelope versionado, limites e
validação antes do domínio.

## 5. Ameaças e controles

| Ameaça | Controle atual/obrigatório | Falha segura |
|---|---|---|
| ordem duplicada após timeout | delivery certainty e `UNKNOWN`, sem retry | bloquear novas entradas |
| worker/UI alterando finanças | Single Database Writer no Core | rejeitar acesso/escrita |
| payload IPC forjado/inválido | framing limitado, JSON estrito, envelope/role/version | encerrar/degradar worker |
| replay/conflito de mensagem | IDs e conteúdo idempotente | conflito estável, Health Gate fechado |
| banco ausente/corrompido | marker, checksum, quick/full integrity check | startup bloqueado |
| journal/checkpoint adulterado | hashes canônicos e append-only | replay/recovery bloqueado |
| candle parcial/gap/stale | validação, Market Health Gate, overlap | nenhuma decisão entregue |
| credencial em log/fixture | tipos redigidos, allowlist de campos, scanner manual | remover artefato e rotacionar |
| vault copiado/adulterado | DPAPI CurrentUser, entropia por chave e checksums interno/externo | erro tipado, nenhum valor |
| ACL ou persistência do vault falha | DACL protegida por SID e replace atômico | startup/operação falha; sem fallback |
| conta real acidental | nenhuma pré-seleção, confirmação dupla, tipo/OTP coincidentes, lease e capabilities | reason code estável |
| estratégia adulterada | manifesto, hash, status, entitlement | não carregar/não gerar entrada |
| sinais conflitantes | Arbiter antes de allocator/risk | nenhuma entrada |
| licença revogada | gate exclusivo de novas entradas | ordens abertas continuam |
| comprometimento de uma corretora | processo/dependência isolados | outra corretora não cai |
| pacote/binário de release adulterado | `ReleaseIntegrityVerifier` e `release_manifest.json` no startup | startup bloqueado com `INTEGRITY_CHECK_FAILED` |
| atualização adulterada ou falha pós-update | Assinatura digital Ed25519 (`UpdateSignatureVerifier`) e rollback automático (`UpdateApplier.rollback`) | rejeição pré-aplicação ou rollback automático preservando banco financeiro |

## 6. Segredos

São segredos ou dados sensíveis:

- senha, cookie, token, OTP e código de desafio;
- Authorization header e material equivalente;
- chave privada do dispositivo;
- lease bruta;
- credencial/token de Deriv ou IQ Option;
- payload de autenticação;
- dados pessoais desnecessários;
- qualquer valor que permita impersonação ou acesso à conta.

Segredos não podem aparecer em:

- código, exemplos ou documentação;
- fixtures, snapshots ou golden files;
- logs, traces, métricas e analytics;
- screenshots e gravações;
- comandos de terminal registrados;
- relatórios de soak;
- pacote de suporte/diagnóstico;
- issue ou canal público.

Valores sintéticos de teste devem ser gerados em runtime e não parecer credenciais reais.

## 7. Armazenamento local protegido

Refresh token, device key, lease e sessão de broker devem usar proteção vinculada ao usuário atual
do Windows. Proteção equivalente a máquina inteira não é suficiente. O desktop é cliente público:
nenhum `client_secret`, segredo mestre ou chave privada de assinatura pode ser confiado ao binário.

O vault Windows implementado grava somente ciphertext DPAPI em arquivos de nome derivado por
SHA-256 da chave lógica. O flag `LOCAL_MACHINE` é proibido. Cada arquivo possui envelope de versão,
tamanho e checksum; o plaintext protegido pelo DPAPI também contém binding da chave, tamanho e
checksum. Diretório e arquivos usam DACL protegida restrita ao SID do token atual. Escritas usam
temporário único no mesmo diretório, `fsync` e `os.replace`; temporários nunca são lidos como
segredos.

Simulação é permitida somente por opt-in de teste ou em plataforma não Windows. Erro de DPAPI, ACL,
integridade ou I/O no Windows deve propagar código estável, sem fallback silencioso e sem valor
parcial. A proteção não elimina cópias imutáveis transitórias na memória Python e ainda requer
validação cross-SID em harness Windows multiusuário antes de distribuição.

## 8. Identidade e corretoras

- identidade DualTrade usa `user_id` estável; e-mail é atributo mutável;
- autenticação do produto não substitui autenticação da corretora;
- o serviço de identidade não recebe senha, cookie, token, saldo, ordem ou histórico completo;
- Deriv comercial deverá preferir OAuth;
- sessão/credencial IQ deverá permanecer no IQ Worker e vault local protegido;
- lease/entitlement só autoriza nova entrada; não liquida nem reconcilia ordem.

## 9. IPC e subprocessos

O IPC v1 usa TCP loopback, frames length-prefixed, JSON e limite de 64 KiB. É proibido usar `pickle`,
desserialização arbitrária ou payload sem schema. Handshake negocia papéis, versão e capacidades.

Requisitos futuros antes de distribuição comercial:

- vincular a identidade do peer local ao SID/artefato assinado, além da prova de posse atual;
- restringir permissões de processo/arquivo;
- definir estratégia de rotação/compatibilidade de protocolo;
- manter filas bounded e backpressure explícito;
- revisar herança de handles e shutdown no Windows.

O Auth Agent já exige posse de token aleatório de 256 bits no primeiro frame e prova HMAC do
servidor. O token não aparece em argv, environment, stdout ou eventos. E-mail/OTP são payloads
transitórios do fluxo de login e o envelope os redige no `repr`; tokens persistentes, device key e
lease bruta não chegam ao Core. Essa autenticação de posse não substitui vínculo futuro ao SID,
named pipe/ACL, assinatura do executável ou proteção contra processo local privilegiado.

O canal lifecycle aplica o mesmo modelo de posse entre Launcher e Core e aceita apenas status,
safe-stop, drain, restart não financeiro e shutdown. Ele não transporta ordem, saldo, stake,
credencial de broker ou conteúdo do `state.db`. O Job Object evita órfãos, mas não transforma morte
abrupta do Launcher em shutdown gracioso; recovery e reconciliação continuam obrigatórios.

## 10. Observabilidade e diagnóstico

Eventos operacionais usam nomes/reason codes estáveis e campos escalares allowlisted. Payload bruto
de broker, candle completo, saldo, ordem completa ou credencial não deve ser logado por padrão.

Qualquer pacote de diagnóstico futuro deve:

1. ser gerado localmente e com consentimento;
2. aplicar redação antes de gravar;
3. impor limites de tamanho e retenção;
4. listar exatamente os arquivos incluídos;
5. excluir bancos e credenciais por padrão;
6. possuir teste automatizado de scanner de segredos.

O scanner implementado reconhece markers de chave privada, JWT/Bearer/Authorization, token Deriv
contextual, OTP contextual, cookie de sessão e senha literal. Resultado contém somente categoria,
localização, comprimento e fingerprint derivado de metadados — nunca o trecho sensível. Arquivo
excessivo, symlink, encoding inválido ou diretório além dos limites falha fechado com
`SECRET_SCAN_FAILED`. O relatório de soak é escaneado em memória antes de `os.replace`.

Consulte [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).

## 11. Dependências, build e release

- versões de runtime/desenvolvimento ficam fixadas no `pyproject.toml`;
- mudança de dependência exige revisão de origem, licença, CVEs e superfície de importação;
- build de produção deve ser reproduzível;
- distribuição Windows local usa onedir; publicação deverá usar instalador assinado;
- atualização requer assinatura, health check e rollback;
- atualização não pode ocorrer com ordem ambígua;
- modo real não pode ser habilitado por environment variable.

Consulte [docs/RELEASE_PROCESS.md](docs/RELEASE_PROCESS.md).

## 12. Resposta a incidente

Ao suspeitar de vazamento ou adulteração:

1. pare novas entradas; não abandone ordens abertas;
2. preserve arquivos e timestamps sem copiar segredo para logs;
3. isole o worker/integração afetado;
4. revogue/rotacione a credencial no sistema de origem quando aplicável;
5. bloqueie Health Gate no escopo afetado;
6. reconcilie estado financeiro por evidência;
7. documente o incidente e a correção sem valor sensível;
8. acrescente teste de regressão.

Não apague banco ou histórico para “limpar” o incidente.

## 13. Relato de vulnerabilidade

Não publique segredo, dado pessoal, banco, screenshot sensível ou passo que execute operação real.
Use um canal privado acordado com os mantenedores. O projeto ainda não definiu endereço público de
security contact; essa definição é uma pendência de release. Até lá, interrompa qualquer teste que
exija credencial real ou ação financeira e preserve somente evidência redigida.

## 14. Checklist de revisão

- [ ] rota Real, quando alterada, preserva seleção/confirmacão explícitas, lease curta, Health Gate e
  proibição de testes financeiros reais;
- [ ] entrada externa é validada antes do domínio;
- [ ] filas, relatórios e payloads possuem limite;
- [ ] correlação/proveniência foi preservada;
- [ ] segredo não aparece no diff, testes ou logs;
- [ ] timeout potencialmente aceito permanece `UNKNOWN`;
- [ ] exposição desconhecida permanece ativa;
- [ ] licença bloqueia somente novas entradas;
- [ ] falha fecha o Health Gate correto;
- [ ] worker da outra corretora permanece independente;
- [ ] testes de falha e scanner foram executados;
- [ ] `WORKLOG.md` foi atualizado.

## 15. Lacunas conhecidas

- validação cross-SID/installer da DACL do vault Windows;
- autenticação de peer/SID para UI e workers e hardening além do token do Auth Agent;
- code signing e cadeia de release;
- SBOM/scanner automatizado de dependências;
- pacote de diagnóstico ainda não integrado ao scanner automatizado;
- política formal de security contact/divulgação;
- vínculo SID/binário para IPC de UI/launcher além da prova de posse;
- pentest e revisão externa;
- requisitos regulatórios por região.
