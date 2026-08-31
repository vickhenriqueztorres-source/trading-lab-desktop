# Disaster recovery

Metas iniciais: RPO 15 minutos para eventos persistidos e RTO 30 minutos para o control plane. Verifique checksum, restaure em perfil isolado, aplique WAL/eventos, execute integrity check e reconciliação. Promova somente com aprovação; mantenha o bot desarmado. Execute restore drill semanal.
