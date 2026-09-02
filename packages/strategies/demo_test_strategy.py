"""Compatibility entry point for the first controlled IQ Option Practice strategy."""

from packages.strategies.iqoption_rsi import (
    IQOPTION_RSI_ARTIFACT,
    IQOPTION_RSI_LOWER,
    IQOPTION_RSI_PERIOD,
    IQOPTION_RSI_STRATEGY_ID,
    IQOPTION_RSI_STRATEGY_VERSION,
    IQOPTION_RSI_TIMEFRAME_SECONDS,
    IQOPTION_RSI_UPPER,
    IQOptionRsiDemoStrategy,
    RsiDecision,
    calculate_wilder_rsi,
    iqoption_rsi_manifest,
)

DemoTestStrategy = IQOptionRsiDemoStrategy

__all__ = [
    "DemoTestStrategy",
    "IQOPTION_RSI_ARTIFACT",
    "IQOPTION_RSI_LOWER",
    "IQOPTION_RSI_PERIOD",
    "IQOPTION_RSI_STRATEGY_ID",
    "IQOPTION_RSI_STRATEGY_VERSION",
    "IQOPTION_RSI_TIMEFRAME_SECONDS",
    "IQOPTION_RSI_UPPER",
    "RsiDecision",
    "calculate_wilder_rsi",
    "iqoption_rsi_manifest",
    "main",
]


def main() -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="IQ Option Demo Test Strategy")
    parser.add_argument("--config", default="config/demo_force_config.yaml", help="Config file")
    args = parser.parse_args()

    manifest = iqoption_rsi_manifest()
    config_file = Path(args.config)
    config_name = config_file.name if config_file.exists() else args.config
    print(
        f"[DemoTestStrategy] Initialized strategy '{manifest.strategy_id}' "
        f"v{manifest.version} using config={config_name}"
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
