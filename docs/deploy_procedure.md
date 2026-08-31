# Deploy e rollback

Confirme manifesto, assinatura e backup; execute `EXPAND`; faça smoke de startup/readiness/Safe Stop; canary apenas read-only; promova gradualmente; execute `MIGRATE` em batches e `CONTRACT` após janela de segurança.

Rollback é obrigatório em falha de health, latência, erro, divergência ou reconciliação. Preserve logs, bancos, vaults e ordens ambíguas.
