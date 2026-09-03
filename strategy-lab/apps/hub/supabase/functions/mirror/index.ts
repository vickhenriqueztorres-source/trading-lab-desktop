import {
  type HubStorage,
  jsonResponse,
  type MirrorTarget,
  requiredEnv,
  SupabaseStorage,
} from "../_shared/hub.ts";
import { r2ConfigFromDeno, R2MirrorTarget } from "../_shared/r2.ts";

const MIRROR_CACHE_SECONDS = "900";
const MIRROR_CONTENT_TYPE = "application/json";

export interface MirrorDeps {
  storage: HubStorage;
  target: MirrorTarget;
}

export function defaultMirrorDeps(): MirrorDeps {
  return {
    storage: new SupabaseStorage(
      requiredEnv("SUPABASE_URL"),
      requiredEnv("SUPABASE_SERVICE_ROLE_KEY"),
    ),
    target: new R2MirrorTarget(r2ConfigFromDeno()),
  };
}

export async function handleMirror(
  request: Request,
  deps: MirrorDeps = defaultMirrorDeps(),
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
    return jsonResponse(422, { error: "MIRROR_PAYLOAD_INVALID" });
  }
  const paths = (payload as Record<string, unknown>).paths;
  if (!Array.isArray(paths) || paths.length !== 2 || !paths.every(isSafeManifestPath)) {
    return jsonResponse(422, { error: "MIRROR_PATHS_INVALID" });
  }
  for (const path of paths as string[]) {
    const body = await deps.storage.downloadManifest(path);
    await deps.target.put(path, body, MIRROR_CONTENT_TYPE, MIRROR_CACHE_SECONDS);
  }
  return jsonResponse(200, { mirrored: paths.length });
}

function isSafeManifestPath(value: unknown): value is string {
  return typeof value === "string" &&
    (value === "current.json" || /^v[1-9][0-9]*\.json$/.test(value));
}

if (import.meta.main) {
  Deno.serve((request) => handleMirror(request));
}
