# Production readiness — controlada

## Checklist

- [x] Ambiente isolado: Demo/Practice por padrão; Real permanece somente leitura.
- [x] Credenciais protegidas no keyring/DPAPI/Vault; nenhum token em logs ou artefatos.
- [x] Monitoramento de health, lease, reconciliação, SLO e journal disponível.
- [x] Alertas e burn rate documentados em `docs/slo_dashboard.md`.
- [x] Runbooks operacionais disponíveis em `docs/runbooks/`.
- [ ] Equipe treinada e contatos de emergência preenchidos.
- [ ] Aprovação operacional assinada em `docs/operational_approval.md`.
- [ ] pip-audit/Safety executado no CI com resultado anexado.
- [ ] Restore drill em infraestrutura aprovada concluído.

## Ativação manual

1. Revisar configuração, versão, manifesto e limites.
2. Confirmar conta Demo e credencial pelo fluxo oficial.
3. Iniciar Safe Stop, bot desarmado e uma conta.
4. Habilitar uma estratégia e um ativo.
5. Monitorar intensivamente as primeiras 24 horas simuladas/operacionais.
6. Expandir somente após SLOs, reconciliação e alertas estáveis.

## Critérios de parada

Divergência, UNKNOWN, perda de lease, breaker aberto, fila saturada, falha de banco, clock inválido,
segredo exposto, erro de reconciliação ou SLO crítico exigem Safe Stop e revisão.

## Rollback

Preservar logs e estado, bloquear novas entradas, reconciliar ordens e executar
`deploy/rollback.sh` para a versão verificada anterior. Nunca apagar histórico ou fazer rollback
destrutivo de schema.

Esta documentação não autoriza execução Real automática. Qualquer habilitação Real futura exige
revisão formal, confirmação explícita e nova aprovação.
