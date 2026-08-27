# Visão geral do projeto — estado atual v1.9.11

## 1. O que é o Trading Lab Desktop

O Trading Lab Desktop é uma aplicação Windows local para observar mercados, executar estratégias
automatizadas em ambiente Deriv Demo e manter estado financeiro de forma conservadora. O nome
interno histórico do projeto é `dualtrade-desktop`, porque a arquitetura foi preparada para Deriv
e IQ Option. Na versão atual, somente a integração Deriv está disponível para uso no aplicativo.

O objetivo principal da arquitetura é preservar segurança e evidência durante falhas. O sistema
prefere bloquear novas entradas a operar quando banco, sessão, dados, relógio, worker ou estado de
ordem não são confiáveis.

## 2. Capacidades disponíveis

### Aplicativo e interface

- Aplicação desktop com PySide6/Qt 6.
- Idiomas de interface: espanhol e inglês, alternáveis no cabeçalho.
- Abas principais: visão geral, Deriv, IQ Option, atividade e configuração.
- Dashboard de ganhos, perdas, resultado líquido, exposição e Health Gates.
- Botão separado para ligar/desligar o bot e botão de encerramento seguro.
- Exportação de diagnóstico redigido em ZIP.
- Aplicativo sempre inicia com novas entradas bloqueadas.

### Deriv

- App ID público incorporado ao produto.
- Login por API Token/PAT com permissão de leitura e operação.
- Descoberta das contas Options associadas ao token.
- Seleção explícita entre Demo e Real.
- Confirmação adicional para selecionar conta Real.
- Token protegido com Windows DPAPI no escopo do usuário atual.
- Consulta de saldo, relógio, símbolos, contratos, histórico e ticks.
- Streaming contínuo de ticks com detecção de duplicidade, gap e desconexão.
- Reconexão autenticada supervisionada.
- Execução automática somente em conta Demo.
- Conta Real conectada em modo estritamente somente leitura.

### Estratégias Deriv

- `Tail Probability Edge`: contratos Digit Over/Under.
- `Selective Differs Edge`: contrato Digit Differs.
- `Parity Regime Edge`: contratos Digit Even/Odd.
- Aquecimento mínimo de 500 ticks.
- Análise de janelas de 200, 350 e 500 ticks.
- Intervalo de Wilson de 99% como filtro conservador.
- Seleção manual de ativo ou radar automático multiativo.
- Apenas uma estratégia é selecionada para execução por vez.
- Apenas uma ordem Deriv pode ficar em voo por vez.
- Todo sinal de um tick é consumido uma única vez; falha exige um sinal novo.

### Gestão de risco

- Stake base, Stop Loss diário, Take Profit diário e limite de perdas consecutivas.
- Cooldown pós-perda usando relógio monotônico.
- Limite global e limite por símbolo.
- Reserva de risco persistida antes do envio da ordem.
- Bounded Martingale opcional, com multiplicador, número máximo de passos e teto de stake.
- Martingale desativado por padrão.
- Sequência de martingale presa ao mesmo ativo até recuperação ou reset.
- Alteração de estratégia desliga o bot antes de aplicar a nova seleção.

### Confiabilidade

- Processos separados para Launcher, UI, Core, Auth Agent e workers.
- Uma única instância do executável portátil e uma única instância por perfil.
- Windows Job Object para encerrar descendentes se o Launcher morrer.
- IPC TCP loopback autenticado e com mensagens enquadradas.
- Persistência SQLite em WAL.
- Intenção, reserva e outbox confirmadas antes do dispatch.
- Estados ambíguos não são reenviados automaticamente.
- Reconciliação de ordens não terminais após reinício.
- Diagnóstico sem bancos, vaults ou credenciais.
- Manifesto SHA-256 da distribuição interna.

## 3. Limitações atuais

### Deriv Real

A conta Real pode ser validada, selecionada e monitorada. Entretanto, o Core cria capacidade de
submissão financeira somente quando o transporte é `live-demo`. Para `live-real`, o worker é
iniciado sem permissão financeira e a interface impede ligar o bot. Portanto, esta versão não
executa ordens com dinheiro real.

### IQ Option

O repositório contém modelos, validadores, um worker de laboratório, cenários simulados e testes de
isolamento. A aba do aplicativo funciona como placeholder/projeção, mas não existe login nem sessão
externa operacional de IQ Option para o usuário.

### Estratégias e rentabilidade

As três estratégias são filtros estatísticos experimentais. Elas não garantem vantagem futura,
assertividade ou lucro. O próprio motor pode se abster, entrar em cooldown de desempenho ou operar
com resultado negativo. A arquitetura mede e limita risco; ela não transforma uma hipótese
estatística em garantia financeira.

### Distribuição

- O executável não possui assinatura Authenticode de produção.
- O instalador Inno Setup está configurado, mas não representa uma publicação comercial.
- A infraestrutura de atualização assinada está implementada como componente, sem canal remoto de
  atualização configurado.
- Não existe backend comercial de identidade/licenciamento ativo.

## 4. Fluxo operacional principal

```text
Abrir o EXE
  → lançador portátil extrai o pacote interno em pasta temporária
  → Launcher verifica instância e integridade da distribuição
  → Core abre/verifica state.db e executa recovery
  → Auth Agent e workers são iniciados
  → UI conecta ao Core
  → bot permanece pausado
  → usuário conecta uma conta Deriv
  → token é validado e protegido por DPAPI
  → Core troca o worker público por Demo ou Real autenticado
  → Demo: análise e capacidade financeira ficam disponíveis
  → Real: somente monitoramento
  → usuário ajusta risco e escolhe estratégia
  → usuário liga explicitamente o bot
```

## 5. Fluxo de uma ordem Demo

```text
Tick Deriv
  → validação e normalização no Deriv Worker
  → frequência e motores estatísticos no Core
  → filtro da estratégia ativa
  → filtro de desempenho e edge mínimo
  → alocação da stake, incluindo martingale delimitado
  → Health Gate e Risk Ledger
  → commit atômico: intenção + reserva + outbox + ordem local
  → proposal e buy no Deriv Worker
  → evento de contrato aberto/liquidado
  → aplicação atômica do estado, P&L e liberação da reserva
  → dashboard atualizado por projeção IPC
```

## 6. Versões e dependências

| Item | Versão/configuração atual |
|---|---|
| Produto | 1.9.11 |
| Python | 3.13 ou superior |
| PySide6 | 6.11.2 |
| websockets | 15.0.1 |
| cryptography | 46.0.5 |
| pytest | 8.4.1, extra de desenvolvimento |
| Ruff | 0.15.22, extra de desenvolvimento |
| mypy | 1.17.1, extra de desenvolvimento |

## 7. Onde ficam os dados

No executável congelado, o perfil padrão fica em:

```text
%LOCALAPPDATA%\TradingLab\profiles\default
```

Principais itens:

```text
default/
├── profile.lock
├── auth/                       # estado do Auth Agent
├── broker_credentials/        # envelopes DPAPI do token/conta Deriv
├── core/
│   ├── state.db               # estado financeiro autoritativo
│   ├── state.db-wal / -shm    # arquivos transitórios do SQLite WAL
│   ├── state.db.expected      # marcador de existência esperada
│   ├── simulated_broker_state.db
│   ├── digit_risk_config.json
│   └── reports/diagnostics/   # pacotes de diagnóstico
└── vault/                     # material protegido do produto, quando aplicável
```

Não edite nem copie esses arquivos enquanto o aplicativo estiver aberto. Não apague locks para
forçar uma segunda instância.

## 8. Termos importantes

- **Core**: processo que possui o estado financeiro, o risco e a coordenação.
- **Worker**: processo isolado que traduz o protocolo de uma corretora.
- **Health Gate**: conjunto de bloqueios que precisa estar aberto para uma nova entrada.
- **Safe Stop**: bloqueio voluntário de novas entradas sem abandonar ordens existentes.
- **Outbox**: comando persistido que aguarda despacho ao worker.
- **Reserva**: exposição contabilizada antes da ordem ser enviada.
- **UNKNOWN**: envio possivelmente aceito, mas ainda sem prova suficiente.
- **Reconciliação**: consulta de evidências externas para resolver estado não terminal.
- **Minor units**: valores monetários inteiros em centavos; evita `float` financeiro.
- **Shadow**: avaliação estatística sem autorização própria para executar.
- **Bounded Martingale**: progressão limitada e subordinada ao Risk Ledger.
