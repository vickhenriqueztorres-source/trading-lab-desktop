# Guia Completo de Implementação, Estratégias e Execução na IQ Option

**Versão:** v1.9.11
**Módulos Envolvidos:** `apps/core/`, `apps/iqoption_worker/`, `apps/ui/`, `packages/strategies/`, `packages/protocol/`

---

## 1. Visão Geral da Arquitetura IQ Option

A integração com a IQ Option foi desenhada para oferecer velocidade de execução, isolamento de falhas e flexibilidade total para novas estratégias e configurações.

```text
[UI - PySide6]
   ▲  │ 1. Salvar Configuração (Ativo / Stake / Stop Loss / Take Profit / Estratégia)
   │  │ 2. Ligar / Desligar Bot (Arm / Disarm)
   │  ▼
[Core Lifecycle & Risk Ledger]
   │
   ├──► [IqOptionAutoTrader] (Engine de Execução em Background)
   │       ├── 1. Varredura Multi-Ativos (EUR/USD, GBP/USD, OTCs, etc.)
   │       ├── 2. Cálculo dos Indicadores (RSI, Bollinger, Médias, MACD, etc.)
   │       ├── 3. Seleção Automática do Ativo com Gatilho
   │       ├── 4. Validação de Risco (Stake, Stop Diário, Max Trades)
   │       └── 5. Disparo Imediato da Ordem
   ▼
[IQ Option Worker Supervisor & Adapter]
   ├── Practice Mode (Conta de Treinamento / Demonstração)
   └── Real Mode (Conta Real com Saldo Real)
```

---

## 2. Como Adicionar Qualquer Nova Estratégia

Para plugar qualquer novo indicador ou estratégia (ex: Bandas de Bollinger, Cruzamento de Médias Móveis, MACD, Price Action, Soros/Martingale):

### Passo 1: Criar a Classe da Estratégia em `packages/strategies/`
Crie um arquivo como `packages/strategies/iqoption_bollinger.py`:

```python
from decimal import Decimal
from packages.domain.market import MarketCandle
from packages.domain.models import Direction
from packages.strategies.models import StrategyDecision, RuntimeContext

class IQOptionBollingerStrategy:
    """Estratégia de Reversão em Bandas de Bollinger (20 períodos, 2 desvios)."""

    def evaluate_decision(
        self, candles: list[MarketCandle], context: RuntimeContext
    ) -> StrategyDecision:
        if len(candles) < 20:
            return StrategyDecision(direction=None, rsi=Decimal("50.0"), score=Decimal("0.0"))

        closes = [float(c.close) for c in candles[-20:]]
        mean = sum(closes) / 20.0
        variance = sum((x - mean) ** 2 for x in closes) / 20.0
        std = variance ** 0.5
        upper_band = Decimal(str(round(mean + (2 * std), 5)))
        lower_band = Decimal(str(round(mean - (2 * std), 5)))
        last_close = candles[-1].close

        # Regra de Entrada:
        direction = None
        if last_close <= lower_band:
            direction = Direction.CALL  # Preço bateu na banda inferior -> Compra
        elif last_close >= upper_band:
            direction = Direction.PUT   # Preço bateu na banda superior -> Venda

        return StrategyDecision(
            direction=direction,
            rsi=Decimal(str(round(last_close, 2))),
            score=Decimal("0.85") if direction else Decimal("0.0"),
        )
```

### Passo 2: Registrar a Estratégia em `apps/core/iqoption_risk_config.py`
Adicione o identificador da sua nova estratégia na lista de estratégias suportadas:
```python
IQOPTION_SUPPORTED_STRATEGIES = frozenset({
    "iqoption-rsi-demo",
    "iqoption-bollinger-bands",
    "iqoption-moving-averages",
})
```

### Passo 3: Adicionar a Estratégia na Interface do Usuário (`apps/ui/components/iqoption_strategy_panel.py`)
No método `__init__`, adicione a nova opção no ComboBox de estratégias:
```python
self._strategy.addItem("Bandas de Bollinger (20, 2) — Reversão", "iqoption-bollinger-bands")
```

---

## 3. Como Configurar e Otimizar Parâmetros

A aba **Configuração** da IQ Option no aplicativo permite alterar e testar dinamicamente todos os parâmetros operacionais:

| Parâmetro | Descrição | Valores Típicos |
|---|---|---|
| **Estratégia** | Algoritmo em execução | RSI 14, Bandas de Bollinger, etc. |
| **Ativo** | Par de moedas ou seleção automática | `AUTO` (Todos), `EURUSD-OTC`, `GBPUSD`, etc. |
| **Valor por Ordem (Stake)** | Valor investido por operação em USD | `$1.00`, `$2.00`, `$5.00` |
| **Stop Loss Diário** | Perda máxima permitida no dia | `$10.00`, `$50.00`, `$100.00` |
| **Take Profit Diário** | Meta de lucro para parar no dia | `$10.00`, `$50.00`, `$100.00` |
| **Limite de Perdas Consecutivas** | Quantas perdas seguidas acionam pausa | `2`, `3`, `5` |
| **Cooldown Pós-Perda** | Tempo de espera (em segundos) após loss | `30s`, `60s`, `120s` |
| **Limite de Trades Diários** | Máximo de entradas executadas no dia | `10`, `20`, `50` |

As configurações são salvas atomicamente no arquivo `iqoption-risk-config.json` no perfil do usuário e carregadas instantaneamente sem reiniciar o programa.

---

## 4. Como Ativar e Operar em Conta Real (`REAL`)

O Trading Lab Desktop possui suporte nativo tanto para a conta de treinamento (**PRACTICE**) quanto para a conta com capital real (**REAL**).

### 4.1 Seleção de Modo de Conta na UI
Na aba **Configuração** da IQ Option:
1. O usuário seleciona o modo de conta desejado:
   - **Conta Demo / Treinamento (PRACTICE)**: Utiliza o saldo fictício da corretora para validação e testes de estratégias.
   - **Conta Real (REAL)**: Conecta-se à conta real do usuário na IQ Option.
2. Ao clicar em **Conectar / Login**, o worker valida as credenciais criptografadas via cofre DPAPI do Windows e obtém o saldo e moeda da conta selecionada.

### 4.2 Habilitação de Execução em Conta Real
Para autorizar o envio de ordens na Conta Real:
1. O adapter `packages/brokers/iqoption_adapter.py` utiliza o parâmetro `force_execution=True` configurado no motor de execução.
2. O `IqOptionAutoTrader` executa as ordens na conta ativa (conforme selecionada pelo usuário).
3. Todas as travas de segurança do **Risk Ledger** (Stop Loss, Take Profit e Máximo de Perdas Consecutivas) permanecem ativas para proteger o capital do operador.

---

## 5. Como Testar e Validar em Qualquer IDE

Você pode testar e validar qualquer alteração com os seguintes comandos:

```powershell
# 1. Testar o motor multi-ativos e auto trader
.\.venv\Scripts\python.exe -m pytest tests/unit/test_iqoption_multi_asset_radar.py tests/unit/test_iqoption_auto_trader.py -v

# 2. Testar controles de risco e limites de stop
.\.venv\Scripts\python.exe -m pytest tests/unit/test_iqoption_risk_controls.py -v

# 3. Testar a conexão e projeção de saldo
.\.venv\Scripts\python.exe -m pytest tests/integration/test_iqoption_connection_projection.py -v
```

---

## 6. Fluxo de Recompilação do Executável

Sempre que concluir uma nova funcionalidade ou otimização:
```powershell
# Compilar a distribuição completa
.\.venv\Scripts\python.exe build_scripts/compile_trading_lab.py --output-dir dist_iqoption_demo

# Gerar o executável único portátil
powershell -ExecutionPolicy Bypass -Command "& { Add-Type -AssemblyName System.IO.Compression.FileSystem; $csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'; $zip = 'dist_iqoption_demo\TradingLab.payload.zip'; Remove-Item $zip -ErrorAction SilentlyContinue; [System.IO.Compression.ZipFile]::CreateFromDirectory('dist_iqoption_demo\TradingLab', $zip); & $csc /target:winexe /optimize+ /platform:x64 /r:System.IO.Compression.FileSystem.dll '/resource:$zip,TradingLab.payload.zip' '/out:dist_iqoption_demo\TradingLab-Desktop-v1.9.11-RSI-AUTO.exe' 'build_scripts\PortableLauncher.cs'; }"
```
