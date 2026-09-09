import {
  type HubStorage,
  jsonResponse,
  manifestSha,
  type MirrorJob,
  type MirrorTarget,
  type PublicationDatabase,
  requiredEnv,
  SupabaseRestDatabase,
  SupabaseStorage,
} from "../_shared/hub.ts";
import { r2ConfigFromDeno, R2MirrorTarget } from "../_shared/r2.ts";

const MIRROR_CACHE_SECONDS = "31536000";
const MIRROR_CONTENT_TYPE = "application/json";

export interface MirrorDeps {
  db: PublicationDatabase;
  storage: HubStorage;
  target: MirrorTarget;
}

export function defaultMirrorDeps(): MirrorDeps {
  const supabaseUrl = requiredEnv("SUPABASE_URL");
  const serviceKey = requiredEnv("SUPABASE_SERVICE_ROLE_KEY");
  return {
    db: new SupabaseRestDatabase(supabaseUrl, serviceKey),
    storage: new SupabaseStorage(supabaseUrl, serviceKey),
    target: new R2MirrorTarget(r2ConfigFromDeno()),
  };
}

export async function handleMirror(
  request: Request,
  deps: MirrorDeps = defaultMirrorDeps(),
): Promise<Response> {
  if (request.method !== "POST") return jsonResponse(405, { error: "METHOD_NOT_ALLOWED" });
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) {
    return jsonResponse(422, { error: "MIRROR_PAYLOAD_INVALID" });
  }
  const requestedId = (payload as Record<string, unknown>).publication_id;
  if (requestedId !== undefined && (typeof requestedId !== "string" || !requestedId)) {
    return jsonResponse(422, { error: "MIRROR_PUBLICATION_ID_INVALID" });
  }

  let job: MirrorJob | null;
  try {
    job = await deps.db.claimMirrorJob(requestedId as string | undefined);
  } catch {
    return jsonResponse(503, { error: "HUB_DB_FAILED" });
  }
  if (!job) return new Response(null, { status: 204 });
  try {
    const body = await deps.storage.downloadManifest(job.storage_path);
    if (await manifestSha(body) !== job.expected_sha256) {
      throw new Error("HUB_MIRROR_SOURCE_HASH_MISMATCH");
    }
    await deps.target.put(
      job.storage_path,
      body,
      MIRROR_CONTENT_TYPE,
      MIRROR_CACHE_SECONDS,
    );
    const mirrored = await deps.target.get(job.storage_path);
    if (await manifestSha(mirrored) !== job.expected_sha256) {
      throw new Error("HUB_MIRROR_TARGET_HASH_MISMATCH");
    }
    await deps.db.completeMirrorJob(job.publication_id);
    return jsonResponse(200, {
      attempts: job.attempts,
      mirrored: 1,
      publication_id: job.publication_id,
      sha256: job.expected_sha256,
    });
  } catch (error) {
    const code = error instanceof Error && /^HUB_[A-Z0-9_]+$/.test(error.message)
      ? error.message
      : "HUB_MIRROR_FAILED";
    await deps.db.failMirrorJob(job.publication_id, code);
    return jsonResponse(503, { error: code, publication_id: job.publication_id });
  }
}

if (import.meta.main) {
  Deno.serve((request) => handleMirror(request));
}
