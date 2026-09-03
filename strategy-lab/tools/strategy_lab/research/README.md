# Research Pipeline — Portões Estatísticos

Documentação formal dos portões estatísticos sequenciais do Strategy Lab, atendendo a **R-RES-7**, **R-RES-8** e **R-RES-10**.

---

## 1. Visão Geral

O objetivo deste subsistema é aplicar um filtro estatístico rigoroso, fail-closed e determinístico sobre os candidatos gerados pela gramática de estratégias. Ele assegura que **nenhuma estratégia originada de ruído aleatório ou sobreajuste (data snooping / p-hacking) seja aprovada**.

O pipeline opera em **curto-circuito**: a falha em qualquer portão encerra a avaliação imediatamente, registrando o motivo específico da reprovação e poupando recursos computacionais.

---

## 2. Ordem Fixa dos Portões

A ordem de avaliação é determinística e imutável:

$$\text{Walk-Forward Ancorado} \longrightarrow \text{Estabilidade} \longrightarrow \text{Múltiplas Comparações (FDR + Permutação)} \longrightarrow \text{Vizinhança Paramétrica} \longrightarrow \text{PBO (CSCV)}$$

### 2.1 Portão 1: Walk-Forward Ancorado (`walk_forward.py`)
- **Motivo:** Avaliar a capacidade preditiva puramente fora da amostra (out-of-sample).
- **Estrutura:** Janela ancorada inicial de treino de 6 meses ($t_0 \dots t_0 + 6\text{m}$) e janela de teste de 2 meses ($t_0 + 6\text{m} \dots t_0 + 8\text{m}$). As janelas subsequentes expandem a base ancorada e avançam o teste em blocos de 2 meses.
- **Métrica:** Produz a taxa de acerto $\hat{p}_i$ para cada janela de teste $i$.

### 2.2 Portão 2: Estabilidade Intertemporal (`walk_forward.py`)
- **Motivo:** Garantir consistência e rejeitar estratégias cujo edge tenha desaparecido ou concentrado em regimes anômalos isolados.
- **Regras:**
  1. Nenhuma janela de teste pode apresentar $\hat{p}_i < p_{min}$ (taxa mínima de break-even).
  2. O desvio-padrão da taxa de acerto entre as janelas de teste deve ser estritamente menor que 3 pontos percentuais ($\sigma_{\hat{p}} < 3\text{ pp} = 0,03$).

### 2.3 Portão 3: Controle de Múltiplas Comparações (`multiple_testing.py`)
- **Motivo:** Em uma rodada onde milhares de candidatos são testados simultaneamente, o acaso produzirá estratégias aparentemente vencedoras por pura variabilidade amostral.
- **Regras:**
  1. **Benjamini-Hochberg (FDR a 5%):** Calcula o $p$-valor binomial sob a hipótese nula $H_0: p \le p_{min}$ e aplica o limiar crítico $(k / N) \times 0,05$, onde $N$ é o número **total** de candidatos avaliados na rodada.
  2. **Teste de Permutação Monte Carlo 1.000×:** Embaralha a série de resultados 1.000 vezes sob a hipótese nula para gerar a distribuição empírica nula. Exige que a taxa de acerto real seja estritamente superior ao percentil 99 ($\hat{p} > P_{99}$).

### 2.4 Portão 4: Vizinhança Paramétrica (`neighborhood.py`)
- **Motivo:** Detectar "spikes" de sobreajuste, onde a estratégia só funciona em um ponto exato do hiperparâmetro (fragilidade e instabilidade numérica).
- **Regras:**
  - Perturba cada hiperparâmetro em $\pm 15\%$, ajustado à grade canônica de `param_spec`.
  - Re-simula cada vizinho via replay incremental.
  - Exige que a mediana da vizinhança passe com folga de segurança: $\text{mediana}(\hat{p}_{vizinhos}) \ge p_{min} + 1,5\text{ pp}$ ($0,015$).

### 2.5 Portão 5: Probabilidade de Sobreajuste de Backtest — PBO (`pbo.py`)
- **Motivo:** Quantificar a probabilidade de que a melhor configuração in-sample tenha sido selecionada por sorte (López de Prado / Bailey et al.).
- **Regras:**
  - Aplica Combinatorially Symmetric Cross-Validation (CSCV) dividindo a série temporal em 16 blocos contíguos.
  - Avalia todas as $\binom{16}{8} = 12.870$ combinações possíveis de partição treino/teste.
  - Computa a proporção de vezes em que a melhor variante in-sample performa abaixo da mediana out-of-sample.
  - Exige $\text{PBO} < 20\%$ ($0,20$).

---

## 3. Critério Final de Aprovação (`approve.py`, R-RES-8)

Um candidato só é **aprovado** se atender cumulativamente a:

1. **Aprovação integral:** Todos os 5 portões acima aprovados com sucesso (`all_gates_passed == True`).
2. **Tamanho amostral suficiente:** $n \ge 500$ operações fora da amostra ($n_{oos} \ge 500$).
3. **Limite Inferior de Wilson 95%:**
   - Aplica penalidade pessimista de atraso de $-1,0\text{ pp}$ sobre a taxa observada ($\hat{p}_{pessimista} = \hat{p} - 0,010$).
   - Calcula o limite inferior do intervalo de pontuação de Wilson (com $z = 1,959964$):
     $$\text{wilson\_lower}(\hat{p}_{pessimista}, n) \ge p_{min} + 1,5\text{ pp}$$

---

## 4. Teste da Moeda em CI (R-RES-10)

Como prova empírica de robustez:
- O teste intocável `test_coin_flip_approves_zero` submete 2.000 candidatos aleatórios a passeios aleatórios com 3 seeds fixos.
- O resultado deve ser **exatamente 0 candidatos aprovados**, comprovando a eficácia anti-ruído dos portões.
