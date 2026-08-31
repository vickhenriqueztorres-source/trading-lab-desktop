# Relatório de validação Demo

**Data:** 2026-08-31 17:30 BRT  
**Escopo:** harness local simulado; nenhuma conta Real e nenhuma ordem externa.

## Resultado

A validação E2E reproduziu 24 horas simuladas de observabilidade, lease, supervisor, divergência, checksum, auditoria, SLO e runbooks. O bot permanece desarmado por padrão e o harness não possui rota de submissão.

- Duplicidade interna: 0 no cenário.
- Envio sem lease: 0; standby não adquire lease concorrente.
- UNKNOWN: retry automático proibido até reconciliação.
- Divergência: bloqueou novas entradas.
- Supervisor: crash-loop detectado após o limite configurado.
- Restore: checksum e contagem de eventos conferidos.
- Segredos: nenhum token, senha ou payload sensível em eventos.
- Runbooks: 12 arquivos presentes.
- SLO: 1.441 amostras simuladas; alerta/severidade exercitados.

**Limitação:** isto é validação local/simulada, não soak contra Deriv. O uso de Demo externo continua opt-in e depende de credencial Demo fornecida separadamente.

