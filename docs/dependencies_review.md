# Revisão de dependências

## Dependências fixadas

| Pacote | Versão | Uso |
|---|---:|---|
| cryptography | 46.0.5 | DPAPI/HMAC/assinaturas |
| PySide6 | 6.11.2 | UI Windows |
| websockets | 15.0.1 | transporte Deriv |
| mypy | 1.17.1 | verificação de tipos |
| pytest | 8.4.1 | testes |
| ruff | 0.15.22 | lint/formatação |

PostgreSQL/Redis são opcionais e carregados de forma lazy; não foram adicionadas dependências novas nesta fase.

## Verificações

Ruff, mypy, compileall e suíte focada foram executados. pip-audit/Safety não estavam instalados neste host; devem rodar no CI de release com bloqueio para vulnerabilidades críticas/altas.

Atualizações devem ser feitas uma por vez, com lockfile/manifesto, contract tests dos adapters, smoke e rollback verificado.

