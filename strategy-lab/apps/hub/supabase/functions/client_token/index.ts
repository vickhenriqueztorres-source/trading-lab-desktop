import { isUuid, signAnonJwt } from "../_shared/jwt.ts";
import {
  GLOBAL_RATE_LIMIT_ID,
  jsonResponse,
  type OutcomeDatabase,
  requiredEnv,
  SupabaseRestDatabase,
} from "../_shared/hub.ts";

const MAX_CLIENT_TOKEN_REQUEST_BYTES = 4096;
const CLIENT_TOKEN_GLOBAL_RATE_LIMIT_PER_HOUR = 60;
type HubEnvironment = "staging" | "production";

export interface ClientTokenDeps {
  db: OutcomeDatabase;
  jwtSecret: string;
  hubEnv: HubEnvironment;
  nowTs: () => number;
}

export function defaultClientTokenDeps(): ClientTokenDeps {
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

export async function handleClientToken(
  request: Request,
  deps: ClientTokenDeps = defaultClientTokenDeps(),
): Promise<Response> {
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "METHOD_NOT_ALLOWED" });
  }
  const body = new Uint8Array(await request.arrayBuffer());
  if (body.byteLength === 0) return jsonResponse(400, { error: "INVALID_JSON" });
  if (body.byteLength > MAX_CLIENT_TOKEN_REQUEST_BYTES) {
    return jsonResponse(413, { error: "CLIENT_TOKEN_PAYLOAD_TOO_LARGE" });
  }
  let payload: unknown;
  try {
    payload = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return jsonResponse(422, { error: "CLIENT_ID_INVALID" });
  }
  const value = payload as Record<string, unknown>;
  if (
    Object.keys(value).length !== 1 || typeof value.client_id !== "string" ||
    !isUuid(value.client_id)
  ) {
    return jsonResponse(422, { error: "CLIENT_ID_INVALID" });
  }
  const nowTs = deps.nowTs();
  const windowStart = Math.floor(nowTs / 3600) * 3600;
  const allowed = await deps.db.consumeRateLimit(
    `client_token_${deps.hubEnv}`,
    GLOBAL_RATE_LIMIT_ID,
    windowStart,
    CLIENT_TOKEN_GLOBAL_RATE_LIMIT_PER_HOUR,
  );
  if (!allowed) return jsonResponse(429, { error: "CLIENT_TOKEN_GLOBAL_RATE_LIMITED" });
  const token = await signAnonJwt(value.client_id, deps.jwtSecret, nowTs, deps.hubEnv);
  return jsonResponse(201, { token, token_type: "Bearer", expires_in: 365 * 86400 });
}

function requiredHubEnvironment(): HubEnvironment {
  const value = requiredEnv("HUB_ENV");
  if (value !== "staging" && value !== "production") throw new Error("HUB_ENV_INVALID");
  return value;
}

if (import.meta.main) {
  Deno.serve((request) => handleClientToken(request));
}
