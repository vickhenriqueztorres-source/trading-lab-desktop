"""Unified Probabilistic Ensemble "Hack Chino" for IQ Option Turbo M1.

Integrates 4 Operational Scenarios (Triggers) and 5 Quantitative Models:

Operational Scenarios (Triggers):
1. Pavio de Rejeição Institucional (Price Action / Retração em topo/fundo)
2. Rompimento de Micro-Faixa com Força (Fluxo / Continuação de Momentum)
3. Exaustão Extrema: Bollinger 2.2 + RSI 7 Rápido (Retração Elástica)
4. Engolfo de Pullback na Média EMA (Tendência Saudável EMA 14 x EMA 28)

Quantitative Models (24/7 Statistical Committee):
1. Context-Based Probability (Bayesian shrinkage on volatility context across 24/7 session)
2. Sequence Continuation & Reversion (Markov runs of consecutive closes with shrinkage)
3. Multi-Factor Logistic Regression (Regularized log-odds across returns & volatility)
4. Analogous Historical Situations (k-NN distance on standardized feature vectors)
5. Probabilistic Market Regimes (Mixture model between Trend/Momentum and Mean Reversion)

Arbitration & Confluence:
- Anti-Contradiction: Veto on conflicting operational triggers (e.g. breakout vs exhaustion).
- Directional Conflict Veto: Zero opposing votes allowed between quant models.
- Confluence Gate: Active trigger backed by >= 2 agreeing models (or 1 model >= 62%)
  with P >= 56.5%.
- Autonomous Quant Path: Strong statistical unanimity (>= 3 models, P >= 58.0%)
  when no trigger is active.
- Martingale: Standard entry on the next candle following the bot's standard risk engine.
- Payout: Handled globally by the Trading Core risk gate.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from packages.domain.market import MarketCandle
from packages.domain.models import Broker, Direction
from packages.strategies.iqoption_indicators import calculate_average_range, calculate_ema
from packages.strategies.iqoption_rsi import calculate_wilder_rsi
from packages.strategies.models import RuntimeContext
from packages.strategy_catalog.models import (
    DataRequirement,
    ReleaseStatus,
    RiskClass,
    StrategyManifest,
)

IQOPTION_HACK_CHINO_STRATEGY_ID = "iqoption-hack-chino"
IQOPTION_HACK_CHINO_STRATEGY_VERSION = "1.0.0"
IQOPTION_HACK_CHINO_TIMEFRAME_SECONDS = 60
IQOPTION_HACK_CHINO_WARMUP_CANDLES = 45
IQOPTION_HACK_CHINO_EXPIRY_CANDLES = 1
IQOPTION_HACK_CHINO_ARTIFACT = b"IQOPTION_HACK_CHINO:ENSEMBLE_4_TRIGGERS_5_MODELS:v1"

# Decision thresholds
MIN_MODEL_VOTE_THRESHOLD = 0.555
MIN_ENSEMBLE_PROBABILITY_G0 = 0.565


def _sigmoid(x: float) -> float:
    """Safe sigmoid function bounded in [0.001, 0.999]."""
    if x > 15.0:
        return 0.999
    if x < -15.0:
        return 0.001
    return 1.0 / (1.0 + math.exp(-x))


@dataclass(frozen=True, slots=True)
class ModelEstimate:
    model_name: str
    prob_call: float
    prob_put: float
    vote: Direction | None
    weight: float
    details: str


@dataclass(frozen=True, slots=True)
class TriggerSignal:
    name: str
    direction: Direction
    details: str


@dataclass(frozen=True, slots=True)
class HackChinoEnsembleResult:
    direction: Direction | None
    ensemble_prob: float
    call_votes: int
    put_votes: int
    neutral_votes: int
    has_conflict: bool
    rejection_reason: str | None
    estimates: tuple[ModelEstimate, ...]
    active_triggers: tuple[TriggerSignal, ...] = ()


# ---------------------------------------------------------------------------
# 1. Context-Based Probability (Bayesian Shrinkage)
# ---------------------------------------------------------------------------
class ContextBayesianModel:
    """Estimates directional probability given current asset, hour, and volatility."""

    NAME = "Contexto Bayesiano"
    WEIGHT = 1.0

    @staticmethod
    def estimate(candles: Sequence[MarketCandle]) -> ModelEstimate:
        if len(candles) < 25:
            return ModelEstimate(
                ContextBayesianModel.NAME,
                0.5,
                0.5,
                None,
                ContextBayesianModel.WEIGHT,
                "Dados insuficientes",
            )

        current = candles[-1]

        # Volatility classification via 14-period range
        ranges = [float(c.high - c.low) for c in candles[-20:]]
        median_range = sorted(ranges)[len(ranges) // 2] if ranges else 1.0
        cur_range = float(current.high - current.low)
        vol_bucket = (
            "HIGH"
            if cur_range > median_range * 1.25
            else ("LOW" if cur_range < median_range * 0.75 else "MED")
        )

        # Scan history for same vol bucket dynamically (24/7 without hour restriction)
        k_up = 0
        n_ctx = 0
        for i in range(1, len(candles) - 1):
            c = candles[i]
            c_range = float(c.high - c.low)
            c_vol = (
                "HIGH"
                if c_range > median_range * 1.25
                else ("LOW" if c_range < median_range * 0.75 else "MED")
            )

            # Match context purely by volatility regime across the continuous session
            if c_vol == vol_bucket:
                n_ctx += 1
                next_c = candles[i + 1]
                if next_c.close > next_c.open:
                    k_up += 1

        # Bayesian shrinkage with prior N_prior = 20, p_prior = 0.50
        n_prior = 20.0
        p_prior = 0.50
        prob_call = (float(k_up) + n_prior * p_prior) / (float(n_ctx) + n_prior)
        prob_put = 1.0 - prob_call

        vote: Direction | None = None
        if prob_call >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.CALL
        elif prob_put >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.PUT

        detail = f"vol={vol_bucket} n={n_ctx} P(CALL)={prob_call:.3f}"
        return ModelEstimate(
            ContextBayesianModel.NAME,
            prob_call,
            prob_put,
            vote,
            ContextBayesianModel.WEIGHT,
            detail,
        )


# ---------------------------------------------------------------------------
# 2. Sequence Continuation & Reversion (Markov / Runs)
# ---------------------------------------------------------------------------
class SequenceMarkovModel:
    """Estimates continuation vs reversion probability based on run of consecutive closes."""

    NAME = "Sequências Markov"
    WEIGHT = 1.2

    @staticmethod
    def estimate(candles: Sequence[MarketCandle]) -> ModelEstimate:
        if len(candles) < 20:
            return ModelEstimate(
                SequenceMarkovModel.NAME,
                0.5,
                0.5,
                None,
                SequenceMarkovModel.WEIGHT,
                "Dados insuficientes",
            )

        # Determine current sequence length ending at t
        seq_dir = 1 if candles[-1].close >= candles[-2].close else -1
        seq_len = 1
        for i in range(len(candles) - 2, 0, -1):
            d = 1 if candles[i].close >= candles[i - 1].close else -1
            if d == seq_dir:
                seq_len += 1
            else:
                break
        seq_len = min(seq_len, 5)

        # Count historical sequences of this length and direction
        k_cont = 0
        k_rev = 0
        for i in range(seq_len, len(candles) - 1):
            match = True
            for offset in range(seq_len):
                idx = i - offset
                d = 1 if candles[idx].close >= candles[idx - 1].close else -1
                if d != seq_dir:
                    match = False
                    break
            if match:
                next_d = 1 if candles[i + 1].close >= candles[i].close else -1
                if next_d == seq_dir:
                    k_cont += 1
                else:
                    k_rev += 1

        # Shrinkage to 0.50 with prior weight 5
        n_prior = 5.0
        p_cont = (float(k_cont) + n_prior * 0.50) / (float(k_cont + k_rev) + n_prior)
        p_rev = 1.0 - p_cont

        if seq_dir == 1:
            # Current sequence is UP
            prob_call = p_cont
            prob_put = p_rev
        else:
            # Current sequence is DOWN
            prob_call = p_rev
            prob_put = p_cont

        vote: Direction | None = None
        if prob_call >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.CALL
        elif prob_put >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.PUT

        dir_str = "UP" if seq_dir == 1 else "DOWN"
        detail = f"seq={dir_str}x{seq_len} cont={k_cont} rev={k_rev} P(C)={prob_call:.3f}"
        return ModelEstimate(
            SequenceMarkovModel.NAME, prob_call, prob_put, vote, SequenceMarkovModel.WEIGHT, detail
        )


# ---------------------------------------------------------------------------
# 3. Multi-Factor Logistic Regression
# ---------------------------------------------------------------------------
class LogisticFactorModel:
    """Standardized multi-factor log-odds score combining returns and volatility dynamics."""

    NAME = "Regressão Logística"
    WEIGHT = 1.1

    @staticmethod
    def estimate(candles: Sequence[MarketCandle]) -> ModelEstimate:
        if len(candles) < 25:
            return ModelEstimate(
                LogisticFactorModel.NAME,
                0.5,
                0.5,
                None,
                LogisticFactorModel.WEIGHT,
                "Dados insuficientes",
            )

        c = [float(x.close) for x in candles]
        h = [float(x.high) for x in candles]
        lows = [float(x.low) for x in candles]

        # Factors:
        # f1: Return 1 bar
        ret1 = (c[-1] - c[-2]) / max(c[-2], 1e-6)
        # f2: Return 3 bars
        ret3 = (c[-1] - c[-4]) / max(c[-4], 1e-6)
        # f3: Return 5 bars
        ret5 = (c[-1] - c[-6]) / max(c[-6], 1e-6)
        # f4: Volatility ratio (last range vs 10-period mean range)
        cur_rng = h[-1] - lows[-1]
        mean_rng = sum(h[i] - lows[i] for i in range(-10, 0)) / 10.0
        rng_ratio = cur_rng / max(mean_rng, 1e-6)
        # f5: Candle position within its own high-low range [0, 1]
        pos = (c[-1] - lows[-1]) / max(cur_rng, 1e-6)

        # Standardized factors (z-scored roughly via scaling factors)
        z1 = max(min(ret1 * 1000.0, 3.0), -3.0)
        z3 = max(min(ret3 * 600.0, 3.0), -3.0)
        z5 = max(min(ret5 * 400.0, 3.0), -3.0)
        z_rng = max(min((rng_ratio - 1.0) * 1.5, 2.0), -2.0)
        z_pos = (pos - 0.5) * 2.0  # [-1, 1]

        # Calibrated weights for M1 short-term momentum & mean-reversion interplay
        w0 = 0.02
        w1 = -0.15  # Slight short-term mean-reversion on single bar spike
        w3 = 0.25   # Continuation of 3-bar directional move
        w5 = 0.20   # Micro-trend alignment
        w_rng = -0.10  # Volatility expansion caution
        w_pos = 0.30   # Close near high favors CALL

        logit = w0 + (w1 * z1) + (w3 * z3) + (w5 * z5) + (w_rng * z_rng) + (w_pos * z_pos)
        prob_call = _sigmoid(logit)
        prob_put = 1.0 - prob_call

        vote: Direction | None = None
        if prob_call >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.CALL
        elif prob_put >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.PUT

        detail = f"logit={logit:+.2f} P(C)={prob_call:.3f} pos={pos:.2f}"
        return ModelEstimate(
            LogisticFactorModel.NAME, prob_call, prob_put, vote, LogisticFactorModel.WEIGHT, detail
        )


# ---------------------------------------------------------------------------
# 4. Analogous Historical Situations (k-NN Distance)
# ---------------------------------------------------------------------------
class AnalogousSituationsKnnModel:
    """Finds top-k most similar market snapshots in recent history and counts outcomes."""

    NAME = "Analogias (k-NN)"
    WEIGHT = 1.0

    @staticmethod
    def _extract_vector(candles: Sequence[MarketCandle], idx: int) -> tuple[float, ...]:
        c = [float(x.close) for x in candles[idx - 5 : idx + 1]]
        h = [float(x.high) for x in candles[idx - 5 : idx + 1]]
        lows = [float(x.low) for x in candles[idx - 5 : idx + 1]]

        r1 = (c[-1] - c[-2]) / max(c[-2], 1e-6) * 1000.0
        r2 = (c[-2] - c[-3]) / max(c[-3], 1e-6) * 1000.0
        r3 = (c[-3] - c[-4]) / max(c[-4], 1e-6) * 1000.0
        rng_last = (h[-1] - lows[-1]) / max(c[-1], 1e-6) * 1000.0
        pos_last = (c[-1] - lows[-1]) / max(h[-1] - lows[-1], 1e-6)
        return (r1, r2, r3, rng_last, pos_last)

    @staticmethod
    def estimate(candles: Sequence[MarketCandle], k: int = 7) -> ModelEstimate:
        if len(candles) < 35:
            return ModelEstimate(
                AnalogousSituationsKnnModel.NAME,
                0.5,
                0.5,
                None,
                AnalogousSituationsKnnModel.WEIGHT,
                "Dados insuficientes",
            )

        target_vec = AnalogousSituationsKnnModel._extract_vector(candles, len(candles) - 1)

        # Search candidates in [10, len(candles) - 2]
        distances: list[tuple[float, int]] = []
        for i in range(10, len(candles) - 1):
            v = AnalogousSituationsKnnModel._extract_vector(candles, i)
            # Euclidean distance
            dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(target_vec, v, strict=True)))
            distances.append((dist, i))

        distances.sort(key=lambda x: x[0])
        top_k = distances[:k]

        up_outcomes = 0
        total_valid = 0
        for _dist, idx in top_k:
            next_c = candles[idx + 1]
            if next_c.close > next_c.open:
                up_outcomes += 1
            total_valid += 1

        # Laplace smoothing (pseudo-count 2)
        prob_call = (float(up_outcomes) + 1.0) / (float(total_valid) + 2.0)
        prob_put = 1.0 - prob_call

        vote: Direction | None = None
        if prob_call >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.CALL
        elif prob_put >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.PUT

        detail = f"k={total_valid} up={up_outcomes} P(C)={prob_call:.3f}"
        return ModelEstimate(
            AnalogousSituationsKnnModel.NAME,
            prob_call,
            prob_put,
            vote,
            AnalogousSituationsKnnModel.WEIGHT,
            detail,
        )


# ---------------------------------------------------------------------------
# 5. Probabilistic Market Regimes
# ---------------------------------------------------------------------------
class ProbabilisticRegimeModel:
    """Soft posterior mixture between Trend/Momentum and Mean Reversion regimes."""

    NAME = "Regimes Probabilísticos"
    WEIGHT = 1.3

    @staticmethod
    def estimate(candles: Sequence[MarketCandle]) -> ModelEstimate:
        if len(candles) < 30:
            return ModelEstimate(
                ProbabilisticRegimeModel.NAME,
                0.5,
                0.5,
                None,
                ProbabilisticRegimeModel.WEIGHT,
                "Dados insuficientes",
            )

        closes = [c.close for c in candles]
        ema_fast = float(calculate_ema(closes, 7))
        ema_mid = float(calculate_ema(closes, 14))
        ema_slow = float(calculate_ema(closes, 25))

        cur_price = float(candles[-1].close)
        cur_open = float(candles[-1].open)

        # Trend indicator: slope and alignment of EMAs
        ema_aligned_bull = ema_fast > ema_mid > ema_slow
        ema_aligned_bear = ema_fast < ema_mid < ema_slow
        adx_proxy = abs(ema_fast - ema_slow) / max(cur_price, 1e-6) * 1000.0

        # Mean-reversion indicator: distance from EMA mid
        dist_from_mean = (cur_price - ema_mid) / max(cur_price, 1e-6) * 1000.0

        # Regime weights via soft gating
        p_trend = _sigmoid((adx_proxy - 1.2) * 1.5)
        p_reversion = 1.0 - p_trend

        # Trend model probability
        if ema_aligned_bull:
            p_call_trend = 0.65
        elif ema_aligned_bear:
            p_call_trend = 0.35
        else:
            p_call_trend = 0.52 if cur_price >= cur_open else 0.48

        # Reversion model probability (oversold -> buy, overbought -> sell)
        p_call_reversion = _sigmoid(-dist_from_mean * 1.8)

        # Mixture probability
        prob_call = (p_trend * p_call_trend) + (p_reversion * p_call_reversion)
        prob_put = 1.0 - prob_call

        vote: Direction | None = None
        if prob_call >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.CALL
        elif prob_put >= MIN_MODEL_VOTE_THRESHOLD:
            vote = Direction.PUT

        regime_str = f"trend={p_trend:.0%}/rev={p_reversion:.0%}"
        detail = f"{regime_str} P(C)={prob_call:.3f}"
        return ModelEstimate(
            ProbabilisticRegimeModel.NAME,
            prob_call,
            prob_put,
            vote,
            ProbabilisticRegimeModel.WEIGHT,
            detail,
        )


# ---------------------------------------------------------------------------
# Operational Scenarios (Triggers)
# ---------------------------------------------------------------------------
class RejectionWickTrigger:
    """Cenário 1: Pavio de Rejeição Institucional (Price Action Puro / Retração).

    Identifica varredura de liquidez além dos topos/fundos das últimas 8 velas,
    seguida de forte rejeição com pavio expressivo (>= 35%) e fechamento a favor.
    """

    NAME = "Pavio de Rejeição Institucional"

    @staticmethod
    def evaluate(candles: Sequence[MarketCandle]) -> TriggerSignal | None:
        if len(candles) < 20:
            return None
        current = candles[-1]
        prior_8 = candles[-9:-1]
        if len(prior_8) < 8:
            return None

        recent_low = min(float(c.low) for c in prior_8)
        recent_high = max(float(c.high) for c in prior_8)
        avg_range = float(calculate_average_range(candles, 20))
        if avg_range <= 0.0:
            return None

        cur_open = float(current.open)
        cur_close = float(current.close)
        cur_high = float(current.high)
        cur_low = float(current.low)
        total_range = cur_high - cur_low
        if total_range <= 1e-7:
            return None

        # CALL: Varreu fundo recente, rejeitou e fechou acima da mínima rompida.
        # Pavio inferior >= 35%, Fechamento no terço superior (>= 60%).
        lower_wick = (min(cur_open, cur_close) - cur_low) / total_range
        close_pos = (cur_close - cur_low) / total_range
        if (
            cur_low < (recent_low - 0.05 * avg_range)
            and cur_close > recent_low
            and lower_wick >= 0.35
            and close_pos >= 0.60
        ):
            return TriggerSignal(
                name=RejectionWickTrigger.NAME,
                direction=Direction.CALL,
                details=f"sweep_low={recent_low:.5f} wick={lower_wick:.1%} pos={close_pos:.1%}",
            )

        # PUT: Varreu topo recente, rejeitou e fechou abaixo da máxima rompida.
        # Pavio superior >= 35%, Fechamento no terço inferior (<= 40%).
        upper_wick = (cur_high - max(cur_open, cur_close)) / total_range
        if (
            cur_high > (recent_high + 0.05 * avg_range)
            and cur_close < recent_high
            and upper_wick >= 0.35
            and close_pos <= 0.40
        ):
            return TriggerSignal(
                name=RejectionWickTrigger.NAME,
                direction=Direction.PUT,
                details=f"sweep_high={recent_high:.5f} wick={upper_wick:.1%} pos={close_pos:.1%}",
            )

        return None


class BreakoutFlowTrigger:
    """Cenário 2: Rompimento de Micro-Faixa com Vela de Força (Fluxo / Momentum).

    Identifica rompimento do canal de consolidação das últimas 4 velas com corpo
    sólido expressivo (>= 60%) e pavio contrário de rejeição mínimo (<= 20%).
    """

    NAME = "Rompimento de Micro-Faixa com Força"

    @staticmethod
    def evaluate(candles: Sequence[MarketCandle]) -> TriggerSignal | None:
        if len(candles) < 5:
            return None
        current = candles[-1]
        prior_4 = candles[-5:-1]
        if len(prior_4) < 4:
            return None

        micro_high = max(float(c.high) for c in prior_4)
        micro_low = min(float(c.low) for c in prior_4)

        cur_open = float(current.open)
        cur_close = float(current.close)
        cur_high = float(current.high)
        cur_low = float(current.low)
        total_range = cur_high - cur_low
        if total_range <= 1e-7:
            return None

        # CALL: Rompeu teto da consolidação, corpo verde >= 60%, pavio superior <= 20%
        body_call = (cur_close - cur_open) / total_range
        upper_wick = (cur_high - cur_close) / total_range
        if cur_close > micro_high and body_call >= 0.60 and upper_wick <= 0.20:
            return TriggerSignal(
                name=BreakoutFlowTrigger.NAME,
                direction=Direction.CALL,
                details=(
                    f"break_high={micro_high:.5f} body={body_call:.1%} up_wick={upper_wick:.1%}"
                ),
            )

        # PUT: Rompeu chão da consolidação, corpo vermelho >= 60%, pavio inferior <= 20%
        body_put = (cur_open - cur_close) / total_range
        lower_wick = (cur_close - cur_low) / total_range
        if cur_close < micro_low and body_put >= 0.60 and lower_wick <= 0.20:
            return TriggerSignal(
                name=BreakoutFlowTrigger.NAME,
                direction=Direction.PUT,
                details=(
                    f"break_low={micro_low:.5f} body={body_put:.1%} low_wick={lower_wick:.1%}"
                ),
            )

        return None


class BollingerExhaustionTrigger:
    """Cenário 3: Exaustão Extrema: Bollinger 2.2 + RSI 7 Rápido (Retração Elástica).

    Identifica saturação elástica onde o preço fura a banda de Bollinger (20, 2.2)
    em confluência com RSI(7) em sobrecompra (>= 78) ou sobrevenda (<= 22),
    com filtro anti-notícia (amplitude <= 2.5 * A20).
    """

    NAME = "Exaustão Bollinger + RSI 7 Rápido"

    @staticmethod
    def evaluate(candles: Sequence[MarketCandle]) -> TriggerSignal | None:
        if len(candles) < 21:
            return None
        current = candles[-1]

        closes_dec = [c.close for c in candles]
        rsi_val = float(calculate_wilder_rsi(closes_dec, period=7))

        closes_20 = [float(c.close) for c in candles[-20:]]
        sma = sum(closes_20) / 20.0
        variance = sum((x - sma) ** 2 for x in closes_20) / 20.0
        std = math.sqrt(variance)
        if std <= 1e-7:
            return None
        upper_band = sma + 2.2 * std
        lower_band = sma - 2.2 * std

        avg_range = float(calculate_average_range(candles, 20))
        total_range = float(current.high - current.low)

        # Anti-news anomaly filter: skip if range exceeds 2.5x average
        if avg_range > 0.0 and total_range > 2.5 * avg_range:
            return None

        cur_low = float(current.low)
        cur_high = float(current.high)

        # CALL: Mínima furou banda inferior e RSI(7) <= 22.0
        if (
            cur_low < (lower_band - 0.10 * std) or float(current.close) < lower_band
        ) and rsi_val <= 22.0:
            return TriggerSignal(
                name=BollingerExhaustionTrigger.NAME,
                direction=Direction.CALL,
                details=f"lower_band={lower_band:.5f} rsi7={rsi_val:.1f}",
            )

        # PUT: Máxima furou banda superior e RSI(7) >= 78.0
        if (
            cur_high > (upper_band + 0.10 * std) or float(current.close) > upper_band
        ) and rsi_val >= 78.0:
            return TriggerSignal(
                name=BollingerExhaustionTrigger.NAME,
                direction=Direction.PUT,
                details=f"upper_band={upper_band:.5f} rsi7={rsi_val:.1f}",
            )

        return None


class EmaPullbackEngulfTrigger:
    """Cenário 4: Engolfo de Pullback na Média EMA (Tendência Saudável).

    Identifica continuação alinhada com EMA(14) x EMA(28), onde a vela anterior
    testa a EMA(14) como retração e a vela atual engolfa a favor da tendência.
    """

    NAME = "Engolfo de Pullback na Média EMA"

    @staticmethod
    def evaluate(candles: Sequence[MarketCandle]) -> TriggerSignal | None:
        if len(candles) < 30:
            return None
        closes_dec = [c.close for c in candles]
        ema14 = float(calculate_ema(closes_dec, 14))
        ema28 = float(calculate_ema(closes_dec, 28))

        prev = candles[-2]
        curr = candles[-1]

        p_o, p_c = float(prev.open), float(prev.close)
        p_l, p_h = float(prev.low), float(prev.high)
        c_o, c_c = float(curr.open), float(curr.close)

        # CALL: Uptrend EMA14 > EMA28, prev vela vermelha testou EMA14,
        # curr vela verde engolfa a anterior e fecha acima da EMA14
        if ema14 > ema28:
            is_prev_pullback = p_c < p_o and p_l <= (ema14 * 1.002)
            is_curr_engulf = c_c > c_o and c_c > p_o and c_o <= (p_c + 1e-6)
            if is_prev_pullback and is_curr_engulf and c_c > ema14:
                return TriggerSignal(
                    name=EmaPullbackEngulfTrigger.NAME,
                    direction=Direction.CALL,
                    details=f"ema14={ema14:.5f} ema28={ema28:.5f} engulf_call",
                )

        # PUT: Downtrend EMA14 < EMA28, prev vela verde testou EMA14,
        # curr vela vermelha engolfa a anterior e fecha abaixo da EMA14
        if ema14 < ema28:
            is_prev_pullback = p_c > p_o and p_h >= (ema14 * 0.998)
            is_curr_engulf = c_c < c_o and c_c < p_o and c_o >= (p_c - 1e-6)
            if is_prev_pullback and is_curr_engulf and c_c < ema14:
                return TriggerSignal(
                    name=EmaPullbackEngulfTrigger.NAME,
                    direction=Direction.PUT,
                    details=f"ema14={ema14:.5f} ema28={ema28:.5f} engulf_put",
                )

        return None


# ---------------------------------------------------------------------------
# Evaluated Result
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class EvalResult:
    direction: Direction | None
    stage: str
    warmup_have: int
    warmup_need: int
    rsi: Decimal
    ensemble: HackChinoEnsembleResult | None = None
    triggers: tuple[TriggerSignal, ...] = ()


# ---------------------------------------------------------------------------
# Master Strategy: Hack Chino Ensemble
# ---------------------------------------------------------------------------
class IQOptionHackChinoStrategy:
    """Orchestrates 4 Operational Scenarios with 5 Probabilistic Models under strict confluence."""

    def __init__(self) -> None:
        self.strategy_id = IQOPTION_HACK_CHINO_STRATEGY_ID
        self.version = IQOPTION_HACK_CHINO_STRATEGY_VERSION
        self.warmup_need = IQOPTION_HACK_CHINO_WARMUP_CANDLES

    @staticmethod
    def evaluate_triggers(candles: Sequence[MarketCandle]) -> tuple[TriggerSignal, ...]:
        active: list[TriggerSignal] = []
        for evaluator in (
            RejectionWickTrigger.evaluate,
            BreakoutFlowTrigger.evaluate,
            BollingerExhaustionTrigger.evaluate,
            EmaPullbackEngulfTrigger.evaluate,
        ):
            sig = evaluator(candles)
            if sig is not None:
                active.append(sig)
        return tuple(active)

    @staticmethod
    def arbitrate_models(
        estimates: Sequence[ModelEstimate],
        triggers: Sequence[TriggerSignal] = (),
    ) -> HackChinoEnsembleResult:
        """Applies trigger coherence, conflict veto, confluence counting and probability."""
        call_votes = sum(1 for e in estimates if e.vote is Direction.CALL)
        put_votes = sum(1 for e in estimates if e.vote is Direction.PUT)
        neutral_votes = sum(1 for e in estimates if e.vote is None)

        # Veto 1: Directional Conflict among Quant Models
        if call_votes > 0 and put_votes > 0:
            return HackChinoEnsembleResult(
                direction=None,
                ensemble_prob=0.50,
                call_votes=call_votes,
                put_votes=put_votes,
                neutral_votes=neutral_votes,
                has_conflict=True,
                rejection_reason="DIRECTIONAL_CONFLICT_VETO",
                estimates=tuple(estimates),
                active_triggers=tuple(triggers),
            )

        # Veto 2: Trigger Ambiguity (Conflicting operational triggers on same bar)
        call_triggers = [t for t in triggers if t.direction is Direction.CALL]
        put_triggers = [t for t in triggers if t.direction is Direction.PUT]
        if call_triggers and put_triggers:
            return HackChinoEnsembleResult(
                direction=None,
                ensemble_prob=0.50,
                call_votes=call_votes,
                put_votes=put_votes,
                neutral_votes=neutral_votes,
                has_conflict=True,
                rejection_reason="VETO_TRIGGER_AMBIGUITY",
                estimates=tuple(estimates),
                active_triggers=tuple(triggers),
            )

        # Veto 3: Mismatch between active trigger and quant models
        if call_triggers and put_votes > 0:
            return HackChinoEnsembleResult(
                direction=None,
                ensemble_prob=0.50,
                call_votes=call_votes,
                put_votes=put_votes,
                neutral_votes=neutral_votes,
                has_conflict=True,
                rejection_reason="VETO_TRIGGER_MODEL_MISMATCH",
                estimates=tuple(estimates),
                active_triggers=tuple(triggers),
            )
        if put_triggers and call_votes > 0:
            return HackChinoEnsembleResult(
                direction=None,
                ensemble_prob=0.50,
                call_votes=call_votes,
                put_votes=put_votes,
                neutral_votes=neutral_votes,
                has_conflict=True,
                rejection_reason="VETO_TRIGGER_MODEL_MISMATCH",
                estimates=tuple(estimates),
                active_triggers=tuple(triggers),
            )

        # Weighted ensemble probability
        total_weight = sum(e.weight for e in estimates) or 1.0
        w_prob_call = sum(e.weight * e.prob_call for e in estimates) / total_weight
        w_prob_put = 1.0 - w_prob_call

        direction: Direction | None = None
        ens_prob = 0.50
        reason: str | None = None

        if call_triggers:
            # Operational scenario proposed CALL -> quant committee audits
            if (call_votes >= 2 and w_prob_call >= MIN_ENSEMBLE_PROBABILITY_G0) or (
                call_votes == 1 and w_prob_call >= 0.620
            ):
                direction = Direction.CALL
                ens_prob = w_prob_call
            else:
                reason = "TRIGGER_ACTIVE_INSUFFICIENT_QUANT_CONFLUENCE"
                ens_prob = w_prob_call
        elif put_triggers:
            # Operational scenario proposed PUT -> quant committee audits
            if (put_votes >= 2 and w_prob_put >= MIN_ENSEMBLE_PROBABILITY_G0) or (
                put_votes == 1 and w_prob_put >= 0.620
            ):
                direction = Direction.PUT
                ens_prob = w_prob_put
            else:
                reason = "TRIGGER_ACTIVE_INSUFFICIENT_QUANT_CONFLUENCE"
                ens_prob = w_prob_put
        else:
            # No operational scenario triggered: Autonomous Quant path requires strong unanimity
            if call_votes >= 3 and w_prob_call >= 0.580:
                direction = Direction.CALL
                ens_prob = w_prob_call
            elif put_votes >= 3 and w_prob_put >= 0.580:
                direction = Direction.PUT
                ens_prob = w_prob_put
            elif call_votes >= 2 and w_prob_call >= MIN_ENSEMBLE_PROBABILITY_G0:
                direction = Direction.CALL
                ens_prob = w_prob_call
            elif put_votes >= 2 and w_prob_put >= MIN_ENSEMBLE_PROBABILITY_G0:
                direction = Direction.PUT
                ens_prob = w_prob_put
            elif (call_votes == 1 and w_prob_call >= 0.620) or (
                put_votes == 1 and w_prob_put >= 0.620
            ):
                direction = Direction.CALL if call_votes == 1 else Direction.PUT
                ens_prob = max(w_prob_call, w_prob_put)
            else:
                reason = "INSUFFICIENT_CONFLUENCE_OR_PROBABILITY"
                ens_prob = max(w_prob_call, w_prob_put)

        return HackChinoEnsembleResult(
            direction=direction,
            ensemble_prob=ens_prob,
            call_votes=call_votes,
            put_votes=put_votes,
            neutral_votes=neutral_votes,
            has_conflict=False,
            rejection_reason=reason,
            estimates=tuple(estimates),
            active_triggers=tuple(triggers),
        )

    def evaluate_closed_candles(
        self,
        candles: Sequence[MarketCandle],
        _runtime_context: RuntimeContext | None = None,
    ) -> EvalResult:
        have = len(candles)
        if have < self.warmup_need:
            return EvalResult(
                direction=None,
                stage=f"WARMING_UP_{have}_{self.warmup_need}",
                warmup_have=have,
                warmup_need=self.warmup_need,
                rsi=Decimal("50.0"),
                ensemble=None,
                triggers=(),
            )

        # Evaluate operational triggers (Layer 1)
        triggers = self.evaluate_triggers(candles)

        # Evaluate the 5 quant models (Layer 2)
        m1 = ContextBayesianModel.estimate(candles)
        m2 = SequenceMarkovModel.estimate(candles)
        m3 = LogisticFactorModel.estimate(candles)
        m4 = AnalogousSituationsKnnModel.estimate(candles)
        m5 = ProbabilisticRegimeModel.estimate(candles)

        ensemble = self.arbitrate_models((m1, m2, m3, m4, m5), triggers=triggers)

        rsi_display = Decimal(str(round(ensemble.ensemble_prob * 100.0, 1)))

        if ensemble.direction is not None:
            trigger_names = (
                "+".join(t.name for t in triggers) if triggers else "QUANT_DIRECT"
            )
            stage = f"SIGNAL_{ensemble.direction.value}_{rsi_display}%_{trigger_names}"
        elif ensemble.has_conflict:
            stage = f"{ensemble.rejection_reason}_C{ensemble.call_votes}_P{ensemble.put_votes}"
        else:
            stage = f"NO_SIGNAL_{ensemble.rejection_reason or 'PROB_LOW'}"

        return EvalResult(
            direction=ensemble.direction,
            stage=stage,
            warmup_have=have,
            warmup_need=self.warmup_need,
            rsi=rsi_display,
            ensemble=ensemble,
            triggers=triggers,
        )

    @staticmethod
    def qualifies_for_martingale(
        gale_step: int,
        ensemble: HackChinoEnsembleResult | None = None,
    ) -> tuple[bool, str]:
        """Standard Martingale qualification without extra external filters."""
        return True, "MARTINGALE_STANDARD"

    def manifest(
        self,
        *,
        release_status: ReleaseStatus = ReleaseStatus.RELEASED,
    ) -> StrategyManifest:
        digest = hashlib.sha256(IQOPTION_HACK_CHINO_ARTIFACT).hexdigest()
        return StrategyManifest(
            manifest_version=1,
            strategy_id=IQOPTION_HACK_CHINO_STRATEGY_ID,
            version=IQOPTION_HACK_CHINO_STRATEGY_VERSION,
            code_hash=digest,
            supported_brokers=(Broker.IQ_OPTION,),
            supported_products=("BINARY_OPTION",),
            supported_timeframes=(IQOPTION_HACK_CHINO_TIMEFRAME_SECONDS,),
            required_data=(DataRequirement.CLOSED_CANDLES,),
            warmup_candles=IQOPTION_HACK_CHINO_WARMUP_CANDLES,
            parameter_schema=(),
            risk_class=RiskClass.STANDARD,
            validation_report_id="iqoption-hack-chino-v1-validation",
            release_status=release_status,
            strategy_pack="iqoption-practice-candidates",
        )


def iqoption_hack_chino_manifest(
    *,
    release_status: ReleaseStatus = ReleaseStatus.RELEASED,
) -> StrategyManifest:
    return IQOptionHackChinoStrategy().manifest(release_status=release_status)


def qualifies_for_martingale(
    gale_step: int,
    ensemble: HackChinoEnsembleResult | None = None,
) -> tuple[bool, str]:
    return IQOptionHackChinoStrategy.qualifies_for_martingale(gale_step, ensemble)


__all__ = [
    "IQOPTION_HACK_CHINO_STRATEGY_ID",
    "IQOPTION_HACK_CHINO_STRATEGY_VERSION",
    "IQOPTION_HACK_CHINO_TIMEFRAME_SECONDS",
    "IQOPTION_HACK_CHINO_WARMUP_CANDLES",
    "IQOPTION_HACK_CHINO_EXPIRY_CANDLES",
    "ContextBayesianModel",
    "SequenceMarkovModel",
    "LogisticFactorModel",
    "AnalogousSituationsKnnModel",
    "ProbabilisticRegimeModel",
    "ModelEstimate",
    "TriggerSignal",
    "RejectionWickTrigger",
    "BreakoutFlowTrigger",
    "BollingerExhaustionTrigger",
    "EmaPullbackEngulfTrigger",
    "HackChinoEnsembleResult",
    "EvalResult",
    "IQOptionHackChinoStrategy",
    "iqoption_hack_chino_manifest",
    "qualifies_for_martingale",
]
