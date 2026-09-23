# Trading Lab License Server

Servidor FastAPI de produção para gerenciamento de licenças, emissão de leases criptográficos assinados por Ed25519, controle de dispositivos e painel administrativo para o Trading Lab Desktop.

---

## 1. Geração de Chaves de Assinatura (Ed25519)

Para gerar um novo par de chaves criptográficas para assinatura de leases:

```bash
python scripts/gen_signing_key.py
```

O script gerará e imprimirá:
1. `LICENSE_SIGNING_KEY_PEM`: chave privada em formato PEM PKCS8 (para configurar no servidor).
2. `LICENSE_SIGNING_KEY_ID`: identificador da chave (ex: `tl-2026-09`).
3. Chave pública em base64 urlsafe com padding para colar em `apps/auth_agent/pinned_keys.py`:
   ```python
   PINNED_LEASE_KEYS = {
       "tl-2026-09": "<chave_publica_b64>",
   }
   ```

---

## 2. Variáveis de Ambiente

Crie um arquivo `.env` ou configure no seu painel de hospedagem (Render, Railway, Fly.io):

| Variável | Obrigatória | Descrição |
|---|---|---|
| `ENVIRONMENT` | Sim (Prod) | `production` ou `development` |
| `DATABASE_URL` | Sim (Prod) | URL de conexão PostgreSQL (Neon pooled recomendado) |
| `LICENSE_SIGNING_KEY_PEM` | Sim (Prod) | Chave privada Ed25519 em formato PEM PKCS8 |
| `LICENSE_SIGNING_KEY_ID` | Sim (Prod) | Identificador da chave ativa (ex: `tl-2026-09`) |
| `ADMIN_EMAIL` | Sim | E-mail exclusivo do administrador para login no painel |
| `ADMIN_SESSION_SECRET` | Sim | Segredo para assinatura de cookies da sessão admin (32+ caracteres) |
| `MAIL_PROVIDER` | Sim | Provedor de e-mail: `resend` (produção) ou `console` (dev/test) |
| `RESEND_API_KEY` | Se `resend` | Chave de API do provedor Resend |
| `MAIL_FROM` | Sim | Remetente dos e-mails (ex: `Trading Lab <acceso@tradinglab.app>`) |
| `PUBLIC_BASE_URL` | Sim | URL pública base do servidor (ex: `https://licencias.tradinglab.app`) |
| `SUPPORT_CONTACT_URL` | Sim | Link de suporte ao cliente (Telegram ou WhatsApp) |
| `RENEW_URL` | Sim | Link de checkout / renovação de licença (Hotmart, etc.) |
| `DOWNLOAD_URL` | Não | URL do instalador do Windows (exibe botão na landing page) |
| `HOTMART_HOTTOK` | Não | Token de verificação de webhook do Hotmart |

---

## 3. Como Executar Localmente

Com o ambiente virtual ativado:

```bash
uvicorn apps.license_server.main:app --reload --port 8080
```

Em desenvolvimento local sem banco de dados configurado ou sem chave PEM, o servidor gera automaticamente uma chave efêmera para testes e emite um aviso no log.

---

## 4. Migrações de Banco de Dados

As migrações de banco de dados são idempotentes (`CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`) e executadas automaticamente na inicialização da aplicação (`lifespan`) toda vez que o servidor sobe com `DATABASE_URL` definido.

---

## 5. Deploy em Produção

O servidor inclui suporte nativo a contêineres Docker multi-stage (`python:3.12-slim`).

### Opção A: Render (via render.yaml)
1. Conecte o repositório no Render.
2. Crie uma **Blueprint** apontando para `render.yaml`.
3. Preencha as variáveis secretas no painel (`DATABASE_URL`, `LICENSE_SIGNING_KEY_PEM`, `ADMIN_SESSION_SECRET`, `RESEND_API_KEY`, etc.).
4. O Render executará o health check automático em `/healthz`.

### Opção B: Fly.io (via fly.toml)
1. Instale o CLI da Fly (`flyctl auth login`).
2. Crie o app: `fly launch --no-deploy`.
3. Configure os segredos:
   ```bash
   fly secrets set DATABASE_URL="postgresql://..." \
                   LICENSE_SIGNING_KEY_PEM="<sua-chave-pem-gerada-pelo-script>" \
                   ADMIN_SESSION_SECRET="sua-chave-secreta-com-mais-de-32-chars" \
                   ADMIN_EMAIL="admin@tradinglab.app" \
                   RESEND_API_KEY="re_..."
   ```
4. Faça o deploy: `fly deploy`.

### Opção C: Railway
1. Crie um novo projeto no Railway conectado ao repositório GitHub.
2. Selecione o serviço e aponte o `Dockerfile` na raiz do projeto.
3. Nas configurações do serviço:
   - **Port:** `8080`
   - **Healthcheck Path:** `/healthz`
4. Adicione todas as variáveis de ambiente obrigatórias na aba **Variables**.
5. O Railway fará o build da imagem Docker multi-stage e iniciará o serviço com migrações automáticas.

---

## 6. Primeiro Login de Administrador e Cadastro de Cliente

1. Acesse `https://<seu-servidor>/admin/login` no navegador.
2. Digite o e-mail cadastrado em `ADMIN_EMAIL` e clique em **Enviar código de acesso**.
3. Obtenha o código de 6 dígitos enviado por e-mail (ou exibido no console se `MAIL_PROVIDER=console`).
4. Digite o código de verificação para autenticar. A sessão é emitida em cookie seguro HttpOnly por 12 horas.
5. No painel, clique em **+ Novo Cliente** (`/admin/customers/new`).
6. Preencha o e-mail do cliente, nome, país e dias de acesso (padrão 30 dias).
7. A licença PRO inicial é gerada automaticamente e o cliente já pode abrir o aplicativo desktop Trading Lab e fazer login com seu e-mail.

---

## 7. Estrutura de Rotas e Segurança

- `GET /`: Landing page pública em espanhol (`landing.html`), sem dependências JavaScript, com botões para compra, download e suporte.
- `GET /healthz`: Health check para balanceadores e orquestradores.
- `GET /.well-known/lease-keys`: Chaves públicas de assinatura para verificação local pelo cliente.
- `POST /api/v1/auth/start`: Início de autenticação por e-mail com entrega de código OTP.
- `POST /api/v1/auth/verify`: Validação de OTP e PKCE com emissão de tokens de acesso e refresh rotativo.
- `POST /api/v1/auth/refresh`: Rotação segura de tokens com revogação em cascata de famílias reutilizadas.
- `POST /api/v1/device/register`: Registro criptográfico de dispositivos Ed25519.
- `POST /api/v1/device/challenge`: Emissão de desafios com nonces de 32 bytes para prova de posse.
- `POST /api/v1/lease/issue`: Emissão de lease assinado para operação offline delimitada.
- `GET /api/v1/lease/revoked/{lease_id}`: Consulta rápida de revogação de lease (fail-closed).
- `/admin/*`: Painel administrativo com controle de licenças, renovação, suspensão, modo real e auditoria.

### Headers de Segurança Ativos
Todas as respostas incluem automaticamente via middleware:
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-Frame-Options: DENY`
- `Strict-Transport-Security: max-age=63072000`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
