# Enterprise Ready — v1.0.0

## Incluído

- Fundação de domínio e worker IQ Option isolado, sem execução externa habilitada.
- Connection Manager, circuit breakers, reconciliação e fila single-writer.
- PostgreSQL/Redis opcionais, lease com fencing token, standby e supervisor.
- SLOs, auditoria HMAC, migrações expand-and-contract, caos local e throughput simulado.
- Deploy/rollback reversível, backup criptografado, restore isolado e runbooks.
- Validação E2E de prontidão em Demo simulada, com bot desarmado por padrão.

## Segurança e escopo

Deriv Real continua somente leitura. Nenhuma ordem Real foi enviada. O canary é read-only/shadow.
Tokens, vaults e bancos operacionais não fazem parte dos artefatos de release.

## Validação

Testes focados das Fases 3–4 passaram; Ruff, formatação, mypy, compileall e diff check passaram.
A suíte histórica completa ainda requer execução no CI Windows limpo devido a limitações locais de
permissão temporária/DPAPI registradas nos relatórios.

## Pendências antes de produção controlada

- executar pip-audit/Safety no CI;
- concluir restore drill em infraestrutura autorizada;
- preencher assinaturas e contatos operacionais;
- conduzir soak Demo externo opt-in antes de qualquer expansão.
