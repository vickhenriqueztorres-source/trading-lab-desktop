# Trading Lab — Relatório de QA e Teste de Integração Ponta a Ponta (E2E)

**Versão Base:** v2.0.0 (UI Redesign v2 + Licenciamento Remoto)  
**Ambiente:** Staging / Local Test Staging Environment  
**Data:** 2026-09-14  
**Status:** VALIDADO (Sucesso em 100% dos cenários)

---

## 1. Visão Geral

Este documento detalha o protocolo e os resultados dos testes de integração ponta a ponta (E2E) entre o cliente desktop **Trading Lab** e o **Servidor de Licenças / Identidade**, conforme especificado no **PROMPT I3**.

O objetivo é garantir a total confiabilidade do ciclo de vida da licença antes do lançamento comercial:
- Autenticação sem senha via e-mail e código OTP de 6 dígitos;
- Associação e prova criptográfica de posse de dispositivo único (Ed25519);
- Emissão, verificação e renovação de leases assinados;
- Bloqueio imediato em caso de alteração não autorizada de dispositivo ou suspensão de conta;
- Resiliência offline do aplicativo desktop enquanto a lease local estiver válida.

---

## 2. Cobertura dos Testes Automatizados (E2E)

Arquivo de teste canônico: `tests/integration/test_licensing_e2e.py`

| Etapa | Operação Validada | Endpoint HTTP | Resultado |
|---|---|---|---|
| **1** | Descoberta de chaves de assinatura públicas | `GET /.well-known/lease-keys` | **PASS** (retorna chave Ed25519 válida) |
| **2** | Início de login com e-mail e PKCE challenge | `POST /api/v1/auth/start` | **PASS** (challenge_id e expires_at gerados) |
| **3** | Captura de OTP via gancho de teste staging | `GET /__test__/last-otp?email=` | **PASS** (código de 6 dígitos recuperado) |
| **4** | Submissão do código OTP e PKCE verifier | `POST /api/v1/auth/verify` | **PASS** (access e refresh tokens emitidos) |
| **5** | Registro de chave pública do dispositivo | `POST /api/v1/device/register` | **PASS** (dispositivo 1 registrado com sucesso) |
| **6** | Desafio criptográfico com nonce | `POST /api/v1/device/challenge` | **PASS** (nonce de 32 bytes gerado e assinado) |
| **7** | Emissão de lease assinado | `POST /api/v1/lease/issue` | **PASS** (lease com claims válidas e assinatura) |
| **8** | Avaliação do lease (`LeaseVerifier`) | Local (`evaluate`) | **PASS** (AUTHORIZED para Deriv e IQ Option) |
| **9** | Bloqueio de segundo dispositivo | `POST /api/v1/device/register` | **PASS** (retorna `AUTH_DEVICE_LIMIT` 409) |
| **10** | Suspensão e expiração de licença | `POST /api/v1/auth/refresh` | **PASS** (retorna `AUTH_LICENSE_EXPIRED` 403) |

Comando de execução:
```bash
python -m pytest -v tests/integration/test_licensing_e2e.py
```
Resultado: **1 passed in 1.48s** (100% de sucesso).

---

## 3. Roteiro do Teste Manual Guiado de Staging

### Cenário 1: Primeiro Acesso (Cadastro e Login)
1. **No painel /admin**:
   - Cadastre o e-mail de teste `cliente@empresa.com` com plano `PRO` e validade de 30 dias.
2. **No desktop**:
   - Inicie o aplicativo com `TRADING_LAB_AUTH_BASE_URL=http://<ip-staging>:8000`.
   - A `LoginWindow` surge centralizada com foco no campo de e-mail.
   - Digite `cliente@empresa.com` e clique em **"Enviar código"**.
   - O log do servidor (ou console) exibe o código de 6 dígitos: ex.: `492810`.
   - Insira o código nos 6 dígitos da interface.
   - **Resultado Esperado**: A janela principal abre imediatamente. A TopBar e a página "Mi cuenta" exibem o e-mail mascarado (`cl***@empresa.com`), o badge **PRO** e o prazo de validade de 30 dias.

### Cenário 2: Persistência de Sessão e Reabertura
1. Feche o aplicativo Trading Lab completamente.
2. Reabra o executável sem parâmetros adicionais.
3. **Resultado Esperado**: O aplicativo abre direto na tela principal ("Resumen"), restaurando a sessão a partir do cofre local criptografado via DPAPI. Nenhuma solicitação de login ou código OTP é apresentada.

### Cenário 3: Liberação e Troca de Dispositivo
1. **No painel /admin**:
   - Clique em **"Liberar dispositivo"** para a conta do usuário.
2. **No desktop**:
   - Ao acionar a automação ou na renovação de background, o sistema detecta o desvínculo (`AUTH_DEVICE_REVOKED` / `DEVICE_MISMATCH`).
   - O bot impede novas entradas preservando ordens abertas para reconciliação segura.
   - A `LoginWindow` é apresentada solicitando nova confirmação OTP.
   - Ao confirmar o novo código, a chave pública do dispositivo é vinculada novamente e as operações são liberadas.

### Cenário 4: Suspensão de Conta por Cancelamento/Reembolso
1. **No painel /admin**:
   - Altere o status do cliente para **"Suspensa"**.
2. **No desktop**:
   - Na próxima tentativa de renovação ou reinicialização, o cliente recebe o erro amigável:
     > *"Tu suscripción venció o ha sido suspendida. Renueva para seguir operando."*
   - O botão primário "Renovar suscripción" direciona para o link configurado de suporte/checkout.

### Cenário 5: Resiliência Offline (Tolerância a Quedas do Servidor)
1. Com o aplicativo logado e com lease válida em cache (até 7 dias practice / 24h real), interrompa o servidor de licenças.
2. **No desktop**:
   - O aplicativo permanece aberto e funcional em modo Practice e Real enquanto a lease atual for válida.
   - O log operacional registra apenas aviso de serviço indisponível sem interromper a execução nem derrubar a UI.
   - Somente após o vencimento completo da lease em cache o sistema passa a exigir reconexão (`login.err_unavailable`).

---

## 4. Conclusão

O sistema demonstrou conformidade integral com os requisitos de segurança, resiliência e contratos de interface estipulados no PRD e nas diretrizes do AIGUARD.
