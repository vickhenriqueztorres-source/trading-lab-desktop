import {
  jsonResponse,
  type OutcomeDatabase,
  type OutcomeEventV2,
  type OutcomeRow,
  requiredEnv,
  SupabaseRestDatabase,
} from "../_shared/hub.ts";
import { bearerToken, verifyAnonJwt } from "../_shared/jwt.ts";

export const MAX_OUTCOMES_PER_BATCH = 500;
export const MAX_OUTCOME_REQUEST_BYTES = 256 * 1024;
export const OUTCOME_WINDOW_SECONDS = 7 * 86400;
export const OUTCOME_RATE_LIMIT_PER_CLIENT_HOUR = 60;
export const OUTCOME_GLOBAL_RATE_LIMIT_PER_HOUR = 600;
export const OUTCOME_GLOBAL_EVENT_BUDGET_PER_DAY = 5000;
export const OUTCOME_CLIENT_EVENT_BUDGET_PER_DAY = 500;

type HubEnvironment = "staging" | "production";

export interface OutcomesDeps {
  db: OutcomeDatabase;
  jwtSecret: string;
  hubEnv: HubEnvironment;
  nowTs: () => number;
}

export function defaultOutcomesDeps(): OutcomesDeps {
  return {
    db: new SupabaseRestDatabase(
      requiredEnv("SUPABASE_URL"),
      requiredEnv("SUPABASE_SERVICE_ROLE_KEY"),
    ),
    jwtSecret: requiredEnv("HUB_JWT_SECRET"),
    hubEnv: requiredHubEnvironment(),
    nowTs: () => Math.floor(Date.now() / 1000),
  };
}

export async function handleOutcomes(
  request: Request,
  deps: OutcomesDeps = defaultOutcomesDeps(),
): Promise<Response> {
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "METHOD_NOT_ALLOWED" });
  }
  const nowTs = deps.nowTs();
  const token = bearerToken(request);
  const claims = token ? await verifyAnonJwt(token, deps.jwtSecret, nowTs, deps.hubEnv) : null;
  if (!claims) {
    return jsonResponse(401, { error: "CLIENT_TOKEN_INVALID" });
  }

  const contentLength = request.headers.get("content-length");
  if (contentLength !== null) {
    const declared = Number(contentLength);
    if (!Number.isSafeInteger(declared) || declared < 0 || declared > MAX_OUTCOME_REQUEST_BYTES) {
      return jsonResponse(413, { error: "OUTCOME_PAYLOAD_TOO_LARGE" });
    }
  }
  let rawBody: Uint8Array;
  try {
    rawBody = new Uint8Array(await request.arrayBuffer());
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  if (rawBody.byteLength === 0) return jsonResponse(400, { error: "INVALID_JSON" });
  if (rawBody.byteLength > MAX_OUTCOME_REQUEST_BYTES) {
    return jsonResponse(413, { error: "OUTCOME_PAYLOAD_TOO_LARGE" });
  }

  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(rawBody));
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  const batch = normalizeOutcomeBatch(payload);
  if (!batch || batch.outcomes.length < 1 || batch.outcomes.length > MAX_OUTCOMES_PER_BATCH) {
    return jsonResponse(422, { error: "OUTCOME_BATCH_INVALID" });
  }

  const windowStart = Math.floor(nowTs / 3600) * 3600;
  const dayStart = Math.floor(nowTs / 86400) * 86400;
  const budget = await deps.db.consumeOutcomeBudgets(
    claims.client_id,
    windowStart,
    dayStart,
    batch.outcomes.length,
  );
  if (budget !== "ALLOWED") return jsonResponse(429, { error: budget });

  if (batch.schemaVersion === 1) {
    const rows: OutcomeRow[] = [];
    for (const raw of batch.outcomes) {
      const row = normalizeLegacyOutcome(raw, claims.client_id, nowTs);
      if (!row) return jsonResponse(422, { error: "OUTCOME_INVALID" });
      rows.push(row);
    }
    await deps.db.insertOutcomes(rows);
    return jsonResponse(202, {
      schema_version: 1,
      received: rows.length,
      accepted: rows.length,
    });
  }

  if (claims.hub_env !== deps.hubEnv) {
    return jsonResponse(401, { error: "CLIENT_TOKEN_ENVIRONMENT_REQUIRED" });
  }
  const rows: OutcomeEventV2[] = [];
  for (const raw of batch.outcomes) {
    const row = normalizeOutcomeV2(raw, claims.client_id, deps.hubEnv, nowTs);
    if (!row) return jsonResponse(422, { error: "OUTCOME_INVALID" });
    rows.push(row);
  }
  const result = await deps.db.insertOutcomeEventsV2(rows);
  return jsonResponse(202, { schema_version: 2, ...result });
}

interface NormalizedBatch {
  schemaVersion: 1 | 2;
  outcomes: unknown[];
}

function normalizeOutcomeBatch(payload: unknown): NormalizedBatch | null {
  if (Array.isArray(payload)) return { schemaVersion: 1, outcomes: payload };
  if (typeof payload !== "object" || payload === null) return null;
  const value = payload as Record<string, unknown>;
  if (!Array.isArray(value.outcomes)) return null;
  const keys = Object.keys(value);
  if (value.schema_version === undefined) {
    if (keys.length !== 1 || keys[0] !== "outcomes") return null;
    return { schemaVersion: 1, outcomes: value.outcomes };
  }
  if (keys.length !== 2 || !keys.includes("schema_version") || !keys.includes("outcomes")) {
    return null;
  }
  if (value.schema_version !== 1 && value.schema_version !== 2) return null;
  return { schemaVersion: value.schema_version, outcomes: value.outcomes };
}

const LEGACY_FIELDS = new Set(["client_id", "strategy_key", "ts", "won", "payout_pct"]);
const V2_FIELDS = new Set([
  "client_id",
  "event_id",
  "strategy_key",
  "recipe_revision",
  "manifest_version",
  "execution_semantics_version",
  "primitives_version",
  "asset",
  "timeframe_s",
  "product",
  "account_environment",
  "source",
  "signal_group_id",
  "ts",
  "won",
  "payout_pct",
]);

function normalizeLegacyOutcome(raw: unknown, clientId: string, nowTs: number): OutcomeRow | null {
  if (!isPlainObject(raw) || !hasOnlyKeys(raw, LEGACY_FIELDS)) return null;
  if (!isStrategyKey(raw.strategy_key) || !isValidOutcomeTs(raw.ts, nowTs)) return null;
  if (typeof raw.won !== "boolean" || !isPayoutPercent(raw.payout_pct)) return null;
  return {
    client_id: clientId,
    strategy_key: raw.strategy_key,
    ts: raw.ts,
    won: raw.won,
    payout_pct: raw.payout_pct,
  };
}

function normalizeOutcomeV2(
  raw: unknown,
  clientId: string,
  hubEnv: HubEnvironment,
  nowTs: number,
): OutcomeEventV2 | null {
  if (!isPlainObject(raw) || !hasOnlyKeys(raw, V2_FIELDS)) return null;
  if (!isUuid(raw.event_id) || !isStrategyKey(raw.strategy_key)) return null;
  if (!positiveSafeInteger(raw.recipe_revision) || !positiveSafeInteger(raw.manifest_version)) {
    return null;
  }
  if (!isContractId(raw.execution_semantics_version)) return null;
  if (
    typeof raw.primitives_version !== "string" || !/^\d+\.\d+\.\d+$/.test(raw.primitives_version)
  ) {
    return null;
  }
  if (typeof raw.asset !== "string" || !/^[A-Z0-9][A-Z0-9._-]{0,39}$/.test(raw.asset)) {
    return null;
  }
  if (raw.timeframe_s !== 60 && raw.timeframe_s !== 300 && raw.timeframe_s !== 900) return null;
  if (raw.product !== "turbo" && raw.product !== "binary" && raw.product !== "digital") {
    return null;
  }
  if (raw.account_environment !== "practice" && raw.account_environment !== "real") return null;
  if (raw.source !== "desktop_bot") return null;
  if (
    typeof raw.signal_group_id !== "string" || !/^sha256:[0-9a-f]{64}$/.test(raw.signal_group_id)
  ) {
    return null;
  }
  if (!isValidOutcomeTs(raw.ts, nowTs)) return null;
  if (typeof raw.won !== "boolean" || !isPayoutPercent(raw.payout_pct)) return null;
  return {
    client_id: clientId,
    event_id: raw.event_id,
    schema_version: 2,
    strategy_key: raw.strategy_key,
    recipe_revision: raw.recipe_revision,
    manifest_version: raw.manifest_version,
    execution_semantics_version: raw.execution_semantics_version,
    primitives_version: raw.primitives_version,
    asset: raw.asset,
    timeframe_s: raw.timeframe_s,
    product: raw.product,
    account_environment: raw.account_environment,
    source: raw.source,
    signal_group_id: raw.signal_group_id,
    ts: raw.ts,
    won: raw.won,
    payout_pct: raw.payout_pct,
    hub_environment: hubEnv,
    received_at: nowTs,
  };
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: Set<string>): boolean {
  return Object.keys(value).every((key) => allowed.has(key));
}

function isStrategyKey(value: unknown): value is string {
  return typeof value === "string" && value.length >= 1 && value.length <= 120 &&
    /^[A-Za-z0-9_:.-]+$/.test(value);
}

function isContractId(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/.test(value);
}

function positiveSafeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) > 0;
}

function isValidOutcomeTs(value: unknown, nowTs: number): value is number {
  if (!Number.isSafeInteger(value)) return false;
  const ts = value as number;
  return ts % 60 === 0 && ts <= nowTs && ts >= nowTs - OUTCOME_WINDOW_SECONDS;
}

function isPayoutPercent(value: unknown): value is string {
  if (typeof value !== "string" || !/^(?:0|[1-9][0-9]{0,2})(\.[0-9]{1,2})?$/.test(value)) {
    return false;
  }
  const [whole, fraction = ""] = value.split(".");
  const normalizedWhole = whole.replace(/^0+(?=\d)/, "");
  const integer = BigInt(normalizedWhole);
  return integer < 100n || (integer === 100n && !/[1-9]/.test(fraction));
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

function requiredHubEnvironment(): HubEnvironment {
  const value = requiredEnv("HUB_ENV");
  if (value !== "staging" && value !== "production") throw new Error("HUB_ENV_INVALID");
  return value;
}

if (import.meta.main) {
  Deno.serve((request) => handleOutcomes(request));
}
