# Habilitação manual e gradual

1. Revisar versão, manifesto, configuração e limites.
2. Validar credencial e conta selecionada; confirmar DEMO explicitamente.
3. Iniciar com Safe Stop e bot desarmado.
4. Habilitar uma conta, uma estratégia e um ativo.
5. Monitorar health, lease, reconciliação, SLO, rate limit e journal.
6. Liberar novas entradas somente por ação manual do operador.
7. Expandir gradualmente após janela de estabilidade documentada.

## Parada imediata

Parar em divergência, ordem UNKNOWN, perda de lease, breaker aberto, fila saturada, falha de banco, clock inválido, segredo exposto, erro de reconciliação ou SLO crítico.

## Rollback

Ativar Safe Stop, preservar evidências, impedir novas entradas, reconciliar ordens e executar deploy/rollback.sh para a versão anterior verificada. Nunca apagar histórico ou reverter migração destrutiva.

Conta Real continua somente leitura nesta baseline; não existe habilitação automática.

