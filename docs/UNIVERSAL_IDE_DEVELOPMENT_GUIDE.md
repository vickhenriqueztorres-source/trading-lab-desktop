# Guia Universal de Desenvolvimento e Otimização em Qualquer IDE

**Versão do Projeto:** Trading Lab Desktop v1.9.11
**Ambiente Recomendado:** Python 3.12 (64-bit) no Windows 10/11
**IDEs Suportadas:** Visual Studio Code, Cursor, Windsurf, PyCharm, Claude Code, Antigravity, Neovim / Vim, Sublime Text.

---

## 1. Visão Geral do Repositório

O Trading Lab Desktop é um ecossistema modular construído em Python e PySide6 com arquitetura distribuída em processos isolados:

```text
Trading Lab Desktop
├── apps/
│   ├── launcher/       -> Inicializador com Job Object, mutex único e supervisão de processos
│   ├── ui/             -> Interface gráfica Qt/PySide6 (Dark Theme, Painéis, Radars, Gráficos)
│   ├── core/           -> Motor central de trading, risk ledger, auto traders e banco de dados SQLite/WAL
│   ├── deriv_worker/   -> Worker isolado para conexão com a Deriv
│   └── iqoption_worker/-> Worker isolado para conexão oficial e não oficial com a IQ Option
├── packages/
│   ├── domain/         -> Modelos fundamentais de domínio (Money, Candle, Order, Direction)
│   ├── protocol/       -> Mensagens tipadas e serialização IPC v1 (Framed JSON sobre Loopback TCP)
│   ├── strategies/     -> Implementações de estratégias (RSI, Digit Edge, Moving Averages, etc.)
│   ├── risk/           -> Gerenciamento de risco, drawdowns e stop limits
│   ├── persistence/    -> Camada SQLite com writer único e logs de auditoria
│   └── security/       -> Cofre DPAPI, hashing HMAC e scanners de segurança
├── tests/              -> Suíte de testes (unit, integration, contract, chaos, e2e)
├── build_scripts/      -> Scripts de empacotamento (PyInstaller, Portable Single Exe)
└── docs/               -> Documentação técnica completa
```

---

## 2. Configuração Rápida em Qualquer IDE

### 2.1 Passo a Passo no Terminal (PowerShell / CMD / Bash)

1. **Clonar ou abrir a pasta raiz do projeto:**
   ```powershell
   cd "c:\caminho\para\trading-lab-desktop"
   ```

2. **Criar e Ativar o Ambiente Virtual:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. **Instalar as Dependências (Modo Editável com Ferramentas de Desenvolvimento):**
   ```powershell
   pip install -e ".[dev]"
   ```

---

## 3. Configurações Específicas por IDE

### 3.1 Visual Studio Code / Cursor / Windsurf
A pasta `.vscode/` já está configurada no repositório com:
- `.vscode/settings.json`: Seleciona automaticamente o interpretador `.venv`, ativa `ruff` como formatador no salvamento e auto-importação.
- `.vscode/launch.json`: Contém alvos de debug prontos:
  - `🚀 Iniciar Trading Lab Desktop (App Completo)` (Pressione **F5**)
  - `🧪 Executar Testes IQ Option`
  - `⚡ Executar Todos os Testes Unitários`
  - `🔨 Compilar Executável (PyInstaller + Portable)`

### 3.2 PyCharm / IntelliJ IDEA
1. Abra o diretório do projeto no PyCharm.
2. Vá em **Settings / Preferences** (`Ctrl + Alt + S`) -> **Project: trading-lab-desktop** -> **Python Interpreter**.
3. Selecione **Add Interpreter** -> **Existing Environment** e aponte para `.venv/Scripts/python.exe`.
4. Em **Project Structure**, marque as pastas `apps` e `packages` como **Sources Root**.
5. Configure o Runner de Testes padrão para `pytest` em **Tools** -> **Python Integrated Tools**.

### 3.3 Claude Code / Cursor Agent / Terminais CLI
Você pode executar comandos diretamente via linha de comando ou pedir para o assistente rodar:
```powershell
# Executar a aplicação
.\.venv\Scripts\python.exe -m apps.launcher

# Executar testes com cobertura
.\.venv\Scripts\python.exe -m pytest -v

# Formatação e checagem de código
.\.venv\Scripts\python.exe -m ruff format apps packages tests
.\.venv\Scripts\python.exe -m ruff check --fix apps packages tests
```

---

## 4. Comandos Canônicos de Desenvolvimento

| Ação | Comando |
|---|---|
| **Executar Aplicativo** | `python -m apps.launcher` |
| **Rodar Testes Rápidos** | `python -m pytest tests/unit/ -v` |
| **Rodar Testes IQ Option** | `python -m pytest tests/unit/test_iqoption_*.py tests/integration/test_iqoption_*.py -v` |
| **Rodar Todos os Testes** | `python -m pytest` |
| **Formatar Código** | `python -m ruff format apps packages tests` |
| **Checar Linters** | `python -m ruff check apps packages tests` |
| **Verificar Tipagem** | `python -m mypy apps packages` |
| **Compilar Distribuição** | `python build_scripts/compile_trading_lab.py --output-dir dist_iqoption_demo` |

---

## 5. Como Otimizar e Modificar o Código com Segurança

1. **Estado Centralizado no Core:** O Core é o proprietário das decisões de risco, ordens e estado financeiro.
2. **Workers Desacoplados:** A comunicação entre UI, Core e Workers é feita via IPC com TCP loopback e envelopes JSON tipados em `packages/protocol/`.
3. **Novas Estratégias:** Devem ser adicionadas em `packages/strategies/` implementando a interface de decisão isolada (sem dependências externas de rede).
4. **Persistência Atômica:** Qualquer nova configuração de usuário deve ser persistida via classes Stores atômicas em `apps/core/` (como `IqOptionRiskConfigStore`).
