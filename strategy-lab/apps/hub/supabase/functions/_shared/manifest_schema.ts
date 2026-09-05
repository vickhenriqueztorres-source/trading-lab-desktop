export const MANIFEST_SCHEMA_VERSION = 1;

const decimalPattern = /^-?[0-9]+(\.[0-9]+)?$/;
const hashPattern = /^sha256:[0-9a-f]{64}$/;

export function parseJsonNoDuplicate(text: string): unknown {
  const seen = new Set<string>();
  const stack: string[] = [];
  let index = 0;
  let inString = false;
  let escape = false;
  let token = "";
  while (index < text.length) {
    const char = text[index];
    if (inString) {
      if (escape) {
        escape = false;
      } else if (char === "\\") {
        escape = true;
      } else if (char === '"') {
        inString = false;
        const next = text.slice(index + 1).match(/^\s*:/);
        if (next) {
          const path = `${stack.join("/")}/${token}`;
          if (seen.has(path)) {
            throw new Error("JSON_DUPLICATE_KEY");
          }
          seen.add(path);
        }
      } else {
        token += char;
      }
    } else if (char === '"') {
      inString = true;
      token = "";
    } else if (char === "{") {
      stack.push(String(index));
    } else if (char === "}") {
      stack.pop();
    }
    index += 1;
  }
  return JSON.parse(text);
}

export function validateManifestSchema(value: unknown): string | null {
  if (!isRecord(value)) return "MANIFEST_NOT_OBJECT";
  if (value.schema_version !== MANIFEST_SCHEMA_VERSION) return "MANIFEST_SCHEMA_VERSION";
  if (value.schema_revision !== undefined && value.schema_revision !== "1.1") {
    return "MANIFEST_SCHEMA_REVISION";
  }
  if (!isSafePositiveInt(value.manifest_version)) return "MANIFEST_VERSION";
  if (!isSafeEpoch(value.published_at) || !isSafeEpoch(value.expires_at)) return "MANIFEST_EPOCH";
  if ((value.expires_at as number) - (value.published_at as number) > 45 * 86400) {
    return "MANIFEST_EXPIRATION";
  }
  if (value.key_id !== "A" && value.key_id !== "B") return "MANIFEST_KEY_ID";
  if (typeof value.signature !== "string" || !value.signature.startsWith("ed25519:")) {
    return "MANIFEST_SIGNATURE";
  }
  if (typeof value.primitives_version !== "string") return "MANIFEST_PRIMITIVES_VERSION";
  if (
    typeof value.primitives_parity_sha256 !== "string" ||
    !hashPattern.test(value.primitives_parity_sha256)
  ) {
    return "MANIFEST_PRIMITIVES_HASH";
  }
  if (typeof value.research_run_id !== "string") return "MANIFEST_RESEARCH_RUN";
  if (!Array.isArray(value.strategies) || value.strategies.length > 5000) {
    return "MANIFEST_STRATEGIES";
  }
  const keys = new Set<string>();
  for (const strategy of value.strategies) {
    if (
      value.schema_revision === "1.1" &&
      (!isRecord(strategy) || strategy.warmup_required == null)
    ) {
      return "MANIFEST_WARMUP_REQUIRED";
    }
    const problem = validateStrategy(strategy, keys);
    if (problem) return problem;
  }
  return null;
}

function validateStrategy(value: unknown, keys: Set<string>): string | null {
  if (!isRecord(value)) return "STRATEGY_NOT_OBJECT";
  if (typeof value.key !== "string" || keys.has(value.key)) return "STRATEGY_KEY";
  keys.add(value.key);
  if (!["F1", "F2", "F3", "F4", "F5"].includes(String(value.family))) return "STRATEGY_FAMILY";
  if (typeof value.display_name_pt !== "string" || value.display_name_pt.length === 0) {
    return "STRATEGY_DISPLAY_NAME";
  }
  if (typeof value.asset !== "string" || !/^[A-Z0-9][A-Z0-9._-]{0,39}$/.test(value.asset)) {
    return "STRATEGY_ASSET";
  }
  if (!["M1", "M5", "M15"].includes(String(value.timeframe))) return "STRATEGY_TIMEFRAME";
  if (!Array.isArray(value.hours_utc) || value.hours_utc.length !== 2) return "STRATEGY_HOURS";
  if (!value.hours_utc.every((item) => Number.isSafeInteger(item) && item >= 0 && item <= 24)) {
    return "STRATEGY_HOURS";
  }
  if ((value.hours_utc[0] as number) >= (value.hours_utc[1] as number)) return "STRATEGY_HOURS";
  if (!isRecord(value.params)) return "STRATEGY_PARAMS";
  if (
    value.warmup_required != null &&
    (!isSafePositiveInt(value.warmup_required) || (value.warmup_required as number) > 10000)
  ) {
    return "STRATEGY_WARMUP";
  }
  for (const paramValue of Object.values(value.params)) {
    if (typeof paramValue !== "string" || !decimalPattern.test(paramValue)) {
      return "STRATEGY_PARAM_DECIMAL";
    }
  }
  if (!validateValidated(value.validated)) return "STRATEGY_VALIDATED";
  if (!validateManagement(value.management)) return "STRATEGY_MANAGEMENT";
  if (!["approved", "observation", "rejected"].includes(String(value.status))) {
    return "STRATEGY_STATUS";
  }
  if (value.status === "rejected" && typeof value.reason_pt !== "string") {
    return "STRATEGY_REJECT_REASON";
  }
  return null;
}

function validateValidated(value: unknown): boolean {
  if (!isRecord(value)) return false;
  for (
    const key of [
      "ops_per_day",
      "p_hat",
      "p_min_at_validation",
      "payout_min",
      "result_1000_ops_stake10",
      "wilson_lower",
      "windows_passed",
    ]
  ) {
    if (typeof value[key] !== "string") return false;
  }
  return typeof value.holdout_passed === "boolean" &&
    isSafePositiveInt(value.n) &&
    Number.isSafeInteger(value.worst_streak);
}

function validateManagement(value: unknown): boolean {
  return isRecord(value) &&
    typeof value.stake_pct === "string" &&
    decimalPattern.test(value.stake_pct) &&
    Number.isSafeInteger(value.martingale_steps_max) &&
    typeof value.paroli === "boolean";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafePositiveInt(value: unknown): boolean {
  return Number.isSafeInteger(value) && (value as number) > 0;
}

function isSafeEpoch(value: unknown): boolean {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}
