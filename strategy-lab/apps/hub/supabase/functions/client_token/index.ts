import { isUuid, signAnonJwt } from "../_shared/jwt.ts";
import { jsonResponse, requiredEnv } from "../_shared/hub.ts";

export interface ClientTokenDeps {
  jwtSecret: string;
  nowTs: () => number;
}

export function defaultClientTokenDeps(): ClientTokenDeps {
  return {
    jwtSecret: requiredEnv("HUB_JWT_SECRET"),
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
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return jsonResponse(422, { error: "CLIENT_ID_INVALID" });
  }
  const clientId = (payload as Record<string, unknown>).client_id;
  if (typeof clientId !== "string" || !isUuid(clientId)) {
    return jsonResponse(422, { error: "CLIENT_ID_INVALID" });
  }
  const token = await signAnonJwt(clientId, deps.jwtSecret, deps.nowTs());
  return jsonResponse(201, { token, token_type: "Bearer", expires_in: 365 * 86400 });
}

if (import.meta.main) {
  Deno.serve((request) => handleClientToken(request));
}
