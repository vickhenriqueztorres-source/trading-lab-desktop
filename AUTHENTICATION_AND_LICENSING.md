# Autenticação e Licenciamento — Fase 1 Local

**Projeto:** DualTrade Desktop  
**Status:** vault Windows executável; identidade/lease ainda simuladas; nenhuma conta real ou
credencial de broker

## 1. Escopo implementado

A base herdada da Fase 0 contém um `AuthAgent` local e um `FakeIdentityService` em memória para
provar:

- login visível por e-mail + código de seis dígitos gerado em runtime;
- PKCE S256 sem `client_secret`;
- `user_id` estável e interno;
- access token curto e refresh token rotativo, com revogação da família após reuso;
- `device_id` aleatório e chave Ed25519 própria, sem fingerprint de hardware;
- prova de posse da chave do dispositivo antes da emissão da lease;
- lease practice Ed25519 assinada, vinculada a usuário, dispositivo, validade, brokers,
  strategy packs, compatibilidade e proibição de modo real;
- verificação local contendo somente chave pública;
- funcionamento offline dentro da validade da lease;
- bloqueio de novas entradas após expiração, revogação conhecida, adulteração,
  incompatibilidade ou entitlement ausente;
- continuidade de eventos, acompanhamento e reconciliação de ordens existentes.

O simulador não envia e-mail, não usa rede e não integra um provedor de identidade externo.

## 2. Autoridade e fronteiras

```text
Fake Identity Service
├── desafio OTP/PKCE
├── família de refresh tokens
├── registro/revogação de dispositivo
└── chave privada efêmera de assinatura de lease
          ↓ respostas validadas
Auth Agent
├── sessão DualTrade
├── device key
├── lease assinada
└── decisão reduzida de autorização
          ↓ allow/block + reason
Trading Core
└── gate exclusivo para novas entradas
```

O Auth Agent nunca recebe senha, cookie, token ou sessão de broker. O cliente IPC de login
transporta e-mail e OTP transitoriamente até o Auth Agent, sem persistir, interpretar ou registrar
esses valores. A fronteira financeira `CoreLeaseEntryAuthorizer` recebe somente decisão reduzida;
access/refresh token, chave privada e lease bruta nunca saem do Auth Agent. A identidade DualTrade
permanece separada das sessões Deriv e IQ Option.

## 3. Proteção local

`WindowsUserScopedVault` persiste cada segredo como `{sha256(chave)}.vault` e usa DPAPI com
`CRYPTPROTECT_UI_FORBIDDEN` no escopo do usuário atual. O flag de máquina não existe no caminho
implementado. A entropia adicional vincula o ciphertext à chave lógica; envelope externo
versionado e pacote interno verificam tamanho, chave e SHA-256 antes de retornar qualquer valor.
Diretório, temporário e arquivo final recebem DACL protegida contendo somente o SID do token atual.
A publicação usa temporário único no mesmo diretório, `fsync` e `os.replace`.

Corrupção, truncamento, chave/entropia divergente, falha de ACL, I/O ou DPAPI geram reason code
tipado e não retornam valor parcial ou vazio. `create_user_scoped_vault` seleciona esse vault no
Windows; simulação ocorre somente fora do Windows ou com `force_simulation=True`. Falha do DPAPI ou
ACL no Windows nunca é mascarada por fallback in-memory.

`SimulatedUserScopedVault` permanece para testes e plataformas não Windows. Ele implementa o mesmo
contrato `set_secret/get_secret/delete_secret/has_secret/clear` e preserva a API legada
`store/load/delete` durante a migração. O simulador não substitui DPAPI em distribuição.

Valores sensíveis usam `SecretValue`, cujo `repr`/`str` é sempre redigido. Código OTP e tokens são
gerados em runtime e não fazem parte de fixtures ou logs.

## 4. Lease practice

O formato v1 contém:

```text
format_version, lease_id, user_id, device_id,
issued_at, expires_at, plan,
broker_access[], strategy_packs[], real_mode_allowed,
client_version_min, client_version_max, nonce
```

A assinatura usa Ed25519 sobre JSON canônico. A lease practice não pode exceder sete dias. O
contrato atual rejeita qualquer caminho de modo real, ainda que um payload tente declará-lo.

## 5. Matriz de falha

| Evento | Nova entrada | Ordem aberta / evento / reconciliação |
|---|---|---|
| Backend indisponível + lease válida | permitida pelos entitlements locais | continua |
| Backend indisponível + lease expirada | bloqueada | continua |
| Assinatura/payload adulterado | bloqueada | continua |
| Dispositivo/lease revogado conhecido | bloqueada | continua |
| Broker/strategy pack ausente | bloqueada no escopo | continua |
| Cliente incompatível | bloqueada | continua |
| Reuso de refresh token | reautenticação exigida | continua |
| Crash/restart do Auth Agent | lease é revalidada; nenhuma autorização é inferida | continua |

## 6. Integração com o Core

`CoreLeaseEntryAuthorizer` expõe apenas uma decisão reduzida e é injetado em `OrderCoordinator`
antes de reserva/persistência. `CoreRuntime` aceita uma factory dessa fronteira. Bloqueios de lease
impedem a criação de nova `TradeIntent`; eles não são consultados pelo processador de eventos nem
pelo reconciliador, preservando ordens abertas.

Harnesses legados podem continuar sem a factory durante a transição. Qualquer composição
que declare licenciamento usa a factory e falha fechado quando o estado não autoriza entrada.

### 6.1 Processo e IPC autenticado

`AuthAgentSupervisor` inicia `python -m apps.auth_agent.runner` sem segredo nos argumentos. Um token
efêmero de 256 bits e, somente no harness fake, o OTP gerado em runtime entram pelo pipe `stdin`.
O subprocesso publica no `stdout` apenas a porta escolhida pelo SO em `127.0.0.1`.

O primeiro frame deve ser `AUTH_HANDSHAKE_REQUEST`. O servidor compara o token em tempo constante e
responde com prova HMAC sobre nonces de cliente/servidor; ausência, token incorreto, prova inválida,
versão/role/deadline incorretos encerram a conexão. Depois do handshake, o mesmo framing JSON v1 de
4 bytes e limite de 64 KiB suporta login, OTP, renovação, autorização, status e shutdown. Cache de
replay é bounded; mesmo `message_id` com conteúdo divergente falha fechado.

As respostas de autorização contêm somente `allowed`, `reason_code` e expiração UTC. Status contém
estado, preview SHA-256 truncado do usuário, device ID e indicador de lease ativa. Nenhuma resposta
contém access/refresh token, chave privada, assinatura ou lease bruta.

Heartbeat usa `AUTH_STATUS_REQUEST`. Perda do processo/conexão produz
`HG_AUTH_AGENT_UNAVAILABLE`; restart é explícito, bounded e usa backoff monotônico, novo token de
sessão e novo handshake. A lease e o device permanecem no vault DPAPI. Chaves públicas efêmeras do
simulador são mantidas no vault para verificar a lease anterior após restart; a chave privada de
assinatura fake permanece apenas na instância efêmera do `FakeIdentityService`.

## 7. Falha, restart e isolamento

| Evento | Comportamento do vault |
|---|---|
| crash antes de `os.replace` | arquivo anterior permanece; temporário não é aceito como segredo |
| crash depois de `os.replace` | reopen valida envelope e DPAPI antes de entregar |
| escrita duplicada da mesma chave | substituição atômica; não há concatenação nem arquivo parcial |
| blob truncado/adulterado | `VAULT_INTEGRITY_FAILED` |
| usuário/entropia incompatível | `VAULT_DECRYPTION_FAILED` |
| ACL não comprovada | `VAULT_ACL_FAILED`; startup não faz fallback |
| expiração/revogação da lease | novas entradas continuam bloqueadas; ordens abertas não dependem do vault |

O Auth Agent é dono lógico de refresh token, device key e lease. O vault protege somente sua
persistência. O Trading Core continua sendo a única autoridade financeira local e recebe apenas a
decisão reduzida de autorização.

## 8. Fora desta fatia

- provedor OTP/PKCE real, TLS, antifraude e recuperação de conta;
- política comercial de limite de dispositivos;
- distribuição/rotação de chaves públicas de lease;
- revogação push e política offline definitiva;
- lease de modo real, que permanece proibida;
- ensaio negativo sob outro SID real e auditoria da DACL em matriz multiusuário/instalador;
- garantia de apagamento de todas as cópias imutáveis em memória do runtime Python;
- roteamento final do fluxo de login pela futura UI/launcher sem ampliar o Core financeiro;
- vínculo do peer IPC ao SID/artefato assinado além do token efêmero de posse.
