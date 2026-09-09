import {
  type HubStorage,
  manifestSha,
  type PublicationDatabase,
  requiredEnv,
  SupabaseRestDatabase,
  SupabaseStorage,
} from "../_shared/hub.ts";

export interface ManifestCurrentDeps {
  db: PublicationDatabase;
  storage: HubStorage;
  channel: "staging" | "production";
}

export function defaultManifestCurrentDeps(): ManifestCurrentDeps {
  const supabaseUrl = requiredEnv("SUPABASE_URL");
  const serviceKey = requiredEnv("SUPABASE_SERVICE_ROLE_KEY");
  return {
    db: new SupabaseRestDatabase(supabaseUrl, serviceKey),
    storage: new SupabaseStorage(supabaseUrl, serviceKey),
    channel: Deno.env.get("HUB_ENV") === "staging" ? "staging" : "production",
  };
}

export async function handleManifestCurrent(
  request: Request,
  deps: ManifestCurrentDeps = defaultManifestCurrentDeps(),
): Promise<Response> {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response(JSON.stringify({ error: "METHOD_NOT_ALLOWED" }), {
      status: 405,
      headers: { "content-type": "application/json" },
    });
  }
  try {
    const candidates = await deps.db.listManifestCandidates(deps.channel);
    for (const [index, candidate] of candidates.entries()) {
      try {
        const body = await deps.storage.downloadManifest(candidate.storage_path);
        if (await manifestSha(body) !== candidate.sha256) continue;
        const headers = new Headers({
          "cache-control": "public,max-age=60,stale-if-error=86400",
          "content-type": "application/json",
          etag: `"${candidate.sha256}"`,
          "x-manifest-fallback": index === 0 ? "false" : "true",
          "x-manifest-version": String(candidate.manifest_version),
        });
        if (request.headers.get("if-none-match") === headers.get("etag")) {
          return new Response(null, { status: 304, headers });
        }
        let responseBody: BodyInit | null = null;
        if (request.method !== "HEAD") {
          const immutableBytes = new Uint8Array(body.byteLength);
          immutableBytes.set(body);
          responseBody = immutableBytes.buffer;
        }
        return new Response(responseBody, { status: 200, headers });
      } catch {
        // Try the next immutable, committed object as last-good fallback.
      }
    }
    return new Response(JSON.stringify({ error: "HUB_MANIFEST_LAST_GOOD_UNAVAILABLE" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    });
  } catch {
    return new Response(JSON.stringify({ error: "HUB_DB_FAILED" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    });
  }
}

if (import.meta.main) {
  Deno.serve((request) => handleManifestCurrent(request));
}
