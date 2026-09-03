# Chave pública de teste — R-MAN-4 / I-8, exceção explícita do AGENTS §6

Este par Ed25519 é **público por design**, não protege nada e não é uma credencial.
A seed é a sequência pública de bytes 00..1f. Nunca utilizar para produção.

- `ed25519-test.seed.hex`: 32 bytes, chave privada de TESTE intencionalmente divulgada.
- `ed25519-test.public.hex`: chave pública correspondente.
- Fixtures usam key_id A ou B somente com trust store de teste.
- `sign` e `verify` recusam este par por padrão, independentemente do key_id.
- Só testes passam `allow_test_keys=True`.
- Pacotes de produção não empacotam este diretório. Hub/bot futuros não podem habilitar essa opção.
- Chaves reais e credenciais Supabase não pertencem ao repositório.
