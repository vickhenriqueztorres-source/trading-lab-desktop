# Contribuindo com o DualTrade Desktop

## 1. Antes de começar

Leia, nesta ordem:

1. [AIGUARD.md](AIGUARD.md)
2. [RULES.md](RULES.md)
3. [AGENTS.md](AGENTS.md)
4. [PRD](PRD_Trading_Desktop_Deriv_IQOption.md)
5. [Arquitetura](Arquitetura_Resiliente_Trading_Desktop_Deriv_IQOption.md)
6. [SECURITY.md](SECURITY.md) e [TEST_PLAN.md](TEST_PLAN.md)
7. documento da área alterada
8. [WORKLOG.md](WORKLOG.md)

Não implemente comportamento financeiro apenas pelo título de uma issue/tarefa.

## 2. Declare a mudança antes de editar

Registre na descrição da mudança:

- requisito/ID atendido;
- processo dono do estado;
- risco baixo/médio/alto;
- efeito em timeout, crash, restart, duplicidade e expiração/revogação;
- broker(s) afetado(s);
- menor fatia testável;
- o que permanece explicitamente fora do escopo.

Mudanças em ordem, risco, persistência, worker, identidade/licença, catálogo distribuído ou conta
real exigem análise de falha e entrada no `WORKLOG`.

## 3. Ambiente

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

Consulte [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## 4. Regras de implementação

- Python 3.13 com type hints em fronteiras públicas;
- mensagens/modelos de fronteira imutáveis;
- `Decimal` ou minor units para dinheiro;
- UTC para timestamps persistidos e monotonic clock para duração;
- enums/reason codes explícitos;
- payload externo validado antes do domínio;
- filas e relatórios bounded;
- correlação e causação preservadas;
- SQL/migrações concentrados em persistência;
- dependência de broker confinada ao worker/adapter correto;
- estratégia sem efeito externo;
- UI futura sem SQLite/API de broker;
- erro crítico nunca é silenciado para manter `READY`.

## 5. Invariantes financeiros

- Core é o único escritor financeiro;
- persistir intenção + reserva + outbox antes do dispatch;
- timeout potencialmente aceito vira `UNKNOWN`;
- `UNKNOWN` permanece exposição;
- nenhuma submissão automática é repetida;
- licença/status de estratégia bloqueia novas entradas, não ordens abertas;
- Arbiter e Allocator precedem Risk Ledger;
- sinais opostos cancelam e sinais iguais não somam stake;
- conta real continua proibida.

## 6. Testes

Adicione testes junto com a implementação. Execute ao menos o subconjunto afetado e, antes de
concluir, os comandos canônicos:

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy apps packages
python -m compileall apps packages
```

Integrações externas permanecem opt-in. Não use credencial real. Consulte
[TEST_PLAN.md](TEST_PLAN.md).

## 7. Persistência e migrações

- não edite migration publicada;
- crie nova versão com checksum;
- teste upgrade e rollback transacional da falha;
- preserve banco/estado anterior;
- não aplique SQL manual para “corrigir” evidência;
- backup deve usar SQLite Backup API, não cópia de `state.db`/WAL ativo.

## 8. Segurança

Nunca adicione senha, token, cookie, OTP, lease bruta, chave privada ou credencial de broker. Revise
logs, fixtures, screenshots e documentação. Em caso de dúvida, pare e siga [SECURITY.md](SECURITY.md).

## 9. Documentação

Atualize o documento que é fonte de verdade da área. Evite copiar a mesma regra em muitos arquivos;
prefira link. Mudança de escopo atualiza PRD/arquitetura. Mudança estrutural recebe decisão no
`WORKLOG` ou ADR futuro.

Toda mudança material acrescenta ao `WORKLOG`:

- data/ID;
- objetivo e requisitos;
- arquivos;
- implementação/decisões;
- validações realmente executadas;
- resultado, riscos e próximo passo.

O histórico é append-only.

## 10. Checklist de revisão

- [ ] escopo pequeno e coeso;
- [ ] proprietário do estado correto;
- [ ] IDs/correlação preservados;
- [ ] falha fechado;
- [ ] nenhuma rota real/segredo;
- [ ] `UNKNOWN` não foi reclassificado por suposição;
- [ ] ordens abertas sobrevivem a expiração/suspensão;
- [ ] testes de falha incluídos;
- [ ] lint/tipagem/compileall executados;
- [ ] scanner de segredo executado;
- [ ] docs e worklog atualizados;
- [ ] riscos residuais declarados.

