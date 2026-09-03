import {
  type HubDatabase,
  jsonResponse,
  type OutcomeRow,
  requiredEnv,
  SupabaseRestDatabase,
} from "../_shared/hub.ts";
import { bearerToken, verifyAnonJwt } from "../_shared/jwt.ts";

const MAX_OUTCOMES_PER_BATCH = 500;
const OUTCOME_WINDOW_SECONDS = 7 * 86400;
const OUTCOME_RATE_LIMIT_PER_HOUR = 60;

export interface OutcomesDeps {
  db: HubDatabase;
  jwtSecret: string;
  nowTs: () => number;
}

export function defaultOutcomesDeps(): OutcomesDeps {
  return {
    db: new SupabaseRestDatabase(
      requiredEnv("SUPABASE_URL"),
      requiredEnv("SUPABASE_SERVICE_ROLE_KEY"),
    ),
    jwtSecret: requiredEnv("HUB_JWT_SECRET"),
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
  const claims = token ? await verifyAnonJwt(token, deps.jwtSecret, nowTs) : null;
  if (!claims) {
    return jsonResponse(401, { error: "CLIENT_TOKEN_INVALID" });
  }

  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  const rawOutcomes = normalizeOutcomeBatch(payload);
  if (!rawOutcomes || rawOutcomes.length > MAX_OUTCOMES_PER_BATCH) {
    return jsonResponse(422, { error: "OUTCOME_BATCH_INVALID" });
  }
  const rows: OutcomeRow[] = [];
  for (const raw of rawOutcomes) {
    const row = normalizeOutcome(raw, claims.client_id, nowTs);
    if (!row) {
      return jsonResponse(422, { error: "OUTCOME_INVALID" });
    }
    rows.push(row);
  }

  const windowStart = Math.floor(nowTs / 3600) * 3600;
  const allowed = await deps.db.consumeRateLimit(
    "outcomes",
    claims.client_id,
    windowStart,
    OUTCOME_RATE_LIMIT_PER_HOUR,
  );
  if (!allowed) {
    return jsonResponse(429, { error: "OUTCOME_RATE_LIMITED" });
  }
  await deps.db.insertOutcomes(rows);
  return jsonResponse(202, { accepted: rows.length });
}

function normalizeOutcomeBatch(payload: unknown): unknown[] | null {
  if (Array.isArray(payload)) return payload;
  if (
    typeof payload === "object" &&
    payload !== null &&
    Array.isArray((payload as Record<string, unknown>).outcomes)
  ) {
    return (payload as Record<string, unknown>).outcomes as unknown[];
  }
  return null;
}

function normalizeOutcome(raw: unknown, clientId: string, nowTs: number): OutcomeRow | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  if (typeof value.strategy_key !== "string" || value.strategy_key.length > 120) return null;
  if (!Number.isSafeInteger(value.ts)) return null;
  const ts = value.ts as number;
  if (ts > nowTs || ts < nowTs - OUTCOME_WINDOW_SECONDS) return null;
  if (typeof value.won !== "boolean") return null;
  if (typeof value.payout_pct !== "string" || !/^-?[0-9]+(\.[0-9]+)?$/.test(value.payout_pct)) {
    return null;
  }
  return {
    client_id: clientId,
    strategy_key: value.strategy_key,
    ts,
    won: value.won,
    payout_pct: value.payout_pct,
  };
}

if (import.meta.main) {
  Deno.serve((request) => handleOutcomes(request));
}
