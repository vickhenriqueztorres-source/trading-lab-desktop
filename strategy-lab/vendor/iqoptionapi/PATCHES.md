# P03 — patches de segurança autorizados

Requisitos: R-VEND-1..3. Autorização do operador: 2026-09-02.

Upstream: https://github.com/victalejo/iqoptionapi
Commit: acac6e08333466ae188c7dfa7fd2a03174e34ca2
Licença MIT copiada integralmente do upstream. O candidato inicial
iqoptionapi/iqoptionapi@8a903cc não declara licença; não foi adotado.

| Data | Motivo | Arquivos |
|---|---|---|
| 2026-09-02 | TLS HTTP/WS verificado; sem desativação global de warnings | api.py |
| 2026-09-02 | Não propagar registros brutos do vendor nem habilitar DEBUG do WebSocket | __init__.py |
| 2026-09-02 | Não inventar relógio do broker; epoch inteiro e conversão UTC | ws/objects/timesync.py |

Os 86 arquivos upstream estão presentes; apenas os três acima foram modificados.
UPSTREAM_COMMIT e PATCHES.md são metadados locais. LICENSE está byte-idêntico.
Diff: ../iqoptionapi.security.patch; hashes: ../iqoptionapi.integrity.json.
Dependências sync: ../REQUIREMENTS.txt. Extras aio/dev não são usados.

O adaptador iq_client.py usa a classe low-level, recursos HTTP de login e
construtores de canal catálogo/velas. Antes de qualquer I/O, substitui as
fronteiras por allowlists somente leitura, parsing Decimal, waits monotônicos
e erros sem payload sensível. Não chama stable_api, connect/start_websocket
legados, retries internos, perfil, saldo, ordens, troca de conta ou handlers
financeiros. Os callbacks legados são substituídos antes de iniciar o socket.

Ruff/mypy cobrem código próprio, não reformatam o terceiro. Os testes executam
os construtores reais com I/O simulada, não validam a conexão externa nem toda
a biblioteca legada. Não executar exemplos de negociação do snapshot.
Os hashes verificam consistência contra a referência versionada, não autenticidade
contra um invasor com acesso de escrita a todo o repositório.
