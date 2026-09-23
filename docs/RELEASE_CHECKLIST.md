# Checklist de Lançamento e Rotina Operacional — Trading Lab

Este documento define os itens mandatórios de verificação pré-lançamento e o protocolo de operação cotidiana do ecossistema Trading Lab (Servidor de Licenças e Aplicativo Desktop).

---

## 1. Servidor de Licenças (Infraestrutura e Produção)

- [ ] **Domínio com HTTPS Válido**: Domínio oficial (ex.: `https://licencias.tradinglab.app`) com certificado TLS ativo apontando para o servidor.
- [ ] **Chave de Assinatura de Produção**: Variáveis `LICENSE_SIGNING_KEY_PEM` e `LICENSE_SIGNING_KEY_ID` configuradas exclusivamente no ambiente de produção (diferentes das chaves de staging/teste).
- [ ] **Pinning Criptográfico Alinhado**: Endpoint `GET /.well-known/lease-keys` responde com o mesmo `key_id` e mesma chave pública configurada em `apps/auth_agent/pinned_keys.py`.
- [ ] **Provedor de E-mail de Alta Entregabilidade**: `MAIL_PROVIDER=resend` configurado com domínio verificado (SPF, DKIM e DMARC). Teste de envio realizado com sucesso para Gmail, Outlook e Hotmail sem cair na caixa de spam.
- [ ] **Painel Administrativo Seguro**: `ADMIN_EMAIL` configurado com e-mail corporativo do proprietário, login testado e cookies configurados com flag `Secure; HttpOnly; SameSite=Lax`.
- [ ] **Rotina de Backup Automatizada**: Backup diário e PITR (Point-in-Time Recovery) ativos no Postgres (Neon/Supabase).
- [ ] **Ganchos de Teste Desativados**: Variável `ENABLE_TEST_HOOKS` rigorosamente ausente em produção (verificar se `GET /__test__/last-otp` retorna HTTP 404).

---

## 2. Aplicativo Desktop (Cliente Final)

- [ ] **Defaults de Produção Embutidos**: Build configurado com `AUTH_BASE_URL_DEFAULT="https://licencias.tradinglab.app"`, e modo simulação estritamente desabilitado por padrão (`FORCE_SIMULATION_DEFAULT = False`).
- [ ] **Assinatura Digital de Binários**: Executável e instalador assinados digitalmente ou instruções documentadas para passagem pelo SmartScreen do Windows.
- [ ] **Auditoria i18n Verde**: Verificação automatizada `python scripts/check_i18n.py` 100% aprovada (zero termos em português nas interfaces de usuário).
- [ ] **Recursos e Pegada de Sistema**: Consumo de memória RAM (< 180MB em idle) e CPU (< 1%) verificados e validados dentro dos limites normativos.
- [ ] **Link de Download Oficial**: URL de distribuição (`DOWNLOAD_URL`) apontando para o executável final validado.

---

## 3. Procedimentos Operacionais (Dia a Dia)

### A. Novo Cliente Pagou
1. Acesse o painel `/admin`.
2. Clique em **"Novo cliente"**.
3. Insira o e-mail exato utilizado no pagamento.
4. Defina a validade inicial de **30 dias**.
5. Envie a mensagem padrão de boas-vindas abaixo pelo WhatsApp/Telegram.

### B. Cliente Trocou de Computador
1. Acesse `/admin` → Localize o cliente pelo e-mail.
2. Clique em **"Liberar dispositivo"**.
3. Oriente o cliente a abrir o Trading Lab no novo computador e solicitar um código de acesso normal. O novo dispositivo será vinculado automaticamente.

### C. Pedido de Cancelamento ou Reembolso
1. Acesse `/admin` → Localize o cliente pelo e-mail.
2. Clique em **"Suspender"**.
3. O desktop bloqueará novas entradas na próxima verificação de background ou reabertura.

### D. Alerta de Renovação Proativa
1. Consulte diariamente a listagem de **"Licenças vencendo em até 3 dias"** no `/admin`.
2. Notifique os clientes com link de renovação para evitar interrupções no fluxo operacional.

---

## 4. Texto Padrão de Boas-Vindas (Espanhol)

Utilize este modelo de mensagem após cadastrar o cliente no sistema:

```text
¡Listo! Tu acceso a Trading Lab está activo hasta el {fecha}. 

1) Descarga el programa: {DOWNLOAD_URL}
2) Ábrelo e ingresa este mismo correo: {email}
3) Te llegará un código de 6 dígitos por correo.

Empieza siempre en MODO PRÁCTICA. 
Soporte oficial: {SUPPORT_CONTACT_URL}
```

---

## 5. Tabela de Referência de Erros e Ações de Suporte

| Código Retornado | Mensagem para o Cliente (ES) | Ação Recomendada do Operador |
|---|---|---|
| `AUTH_LICENSE_EXPIRED` | *"Tu suscripción venció. Renueva para seguir operando."* | Cobrar taxa de renovação. Após confirmação, clicar em **Renovar (+30 dias)** no admin. |
| `AUTH_DEVICE_LIMIT` | *"Tu licencia ya está activa en otro equipo. Contacta soporte."* | Confirmar titularidade do cliente e clicar em **Liberar dispositivo** no admin. |
| `AUTH_DEVICE_REVOKED` | *"Tu licencia ya está activa en otro equipo. Contacta soporte."* | Dispositivo anterior desvinculado; solicitar que realize novo login no equipamento atual. |
| `AUTH_SERVICE_UNAVAILABLE` | *"No pudimos conectar con el servidor. Intenta de nuevo en unos minutos."* | Verificar status da API no healthcheck (`/healthz`), conectividade do banco de dados e Resend. |
| `AUTH_OTP_INVALID` | *"Código incorrecto."* | Nenhuma ação técnica; orientar o usuário a conferir os 6 dígitos digitados (limite de 5 tentativas). |
| `AUTH_CHALLENGE_EXPIRED` | *"El código venció. Pide uno nuevo."* | Orientar o usuário a clicar em "Reenviar código" para gerar um novo desafio. |
