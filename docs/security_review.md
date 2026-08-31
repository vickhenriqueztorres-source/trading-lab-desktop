# Revisão de segurança

## Checklist

- Credenciais: mantidas fora do código e do plano de identidade; vault/DPAPI continua no escopo do usuário.
- Redaction: logs e auditoria aceitam somente campos operacionais redigidos.
- Auditoria: cadeia SHA-256 com HMAC e verificação de integridade.
- Revogação: bloqueia novas entradas sem abandonar acompanhamento de ordens abertas.
- MFA/bloqueio: delegados ao provedor quando suportados; tentativas permanecem limitadas pelo agente de autenticação.
- Transporte/repouso: TLS do broker e proteção local conforme baseline; backups usam AES-256-CBC com chave externa.
- Dependências: versões fixadas no pyproject.toml; revisão registrada abaixo.
- Secret scanning: executar no pipeline de release antes de publicar.
- Dumps/snapshots: scripts excluem token, secret e vault.
- Conta Real: somente leitura; nenhuma alteração nesta fase habilita submissão.

## Ações corretivas

1. Manter chave de backup fora do repositório e rotacioná-la conforme política operacional.
2. Executar pip-audit/Safety no CI com artefato anexado ao build.
3. Aprovação humana obrigatória antes de qualquer mudança de ambiente.

**Status:** revisão técnica concluída; aprovação operacional humana pendente.

