import { canonicalBytes } from "../_shared/canonical.ts";
import {
  keyEnvFromDeno,
  type ManifestKeyEnv,
  verifyManifestSignature,
} from "../_shared/ed25519.ts";
import {
  type HubDatabase,
  type HubStorage,
  jsonResponse,
  manifestSha,
  requiredEnv,
  SupabaseRestDatabase,
  SupabaseStorage,
} from "../_shared/hub.ts";
import { parseJsonNoDuplicate, validateManifestSchema } from "../_shared/manifest_schema.ts";

export interface PublishDeps {
  db: HubDatabase;
  storage: HubStorage;
  keyEnv: ManifestKeyEnv;
  invokeMirror?: (paths: string[]) => Promise<void>;
}

export function defaultPublishDeps(): PublishDeps {
  const supabaseUrl = requiredEnv("SUPABASE_URL");
  const serviceKey = requiredEnv("SUPABASE_SERVICE_ROLE_KEY");
  return {
    db: new SupabaseRestDatabase(supabaseUrl, serviceKey),
    storage: new SupabaseStorage(supabaseUrl, serviceKey),
    keyEnv: keyEnvFromDeno(),
    invokeMirror: async (paths: string[]) => {
      const response = await fetch(`${supabaseUrl}/functions/v1/mirror`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${serviceKey}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({ paths }),
      });
      if (!response.ok) {
        console.log(JSON.stringify({ event: "mirror_invoke_failed", status: response.status }));
      }
    },
  };
}

export async function handlePublish(
  request: Request,
  deps: PublishDeps = defaultPublishDeps(),
): Promise<Response> {
  if (request.method !== "POST") {
    return jsonResponse(405, { error: "METHOD_NOT_ALLOWED" });
  }

  let manifest: unknown;
  try {
    manifest = parseJsonNoDuplicate(await request.text());
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  const schemaProblem = validateManifestSchema(manifest);
  if (schemaProblem) {
    return jsonResponse(422, { error: schemaProblem });
  }
  const manifestRecord = manifest as Record<string, unknown>;
  if (!(await verifyManifestSignature(manifestRecord, deps.keyEnv))) {
    return jsonResponse(401, { error: "MANIFEST_SIGNATURE_INVALID" });
  }

  const manifestVersion = manifestRecord.manifest_version as number;
  const currentVersion = await deps.db.maxManifestVersion();
  if (currentVersion !== null && manifestVersion <= currentVersion) {
    return jsonResponse(409, { error: "MANIFEST_VERSION_NOT_NEWER" });
  }

  const body = canonicalBytes(manifestRecord);
  const sha256 = await manifestSha(body);
  const versionPath = `v${manifestVersion}.json`;
  await deps.storage.uploadManifest(versionPath, body, "application/json", "900");
  await deps.storage.uploadManifest("current.json", body, "application/json", "900");
  await deps.db.insertManifest({
    manifest_version: manifestVersion,
    published_at: manifestRecord.published_at as number,
    expires_at: manifestRecord.expires_at as number,
    storage_path: `manifests/${versionPath}`,
    sha256,
    signature: manifestRecord.signature as string,
    primitives_version: manifestRecord.primitives_version as string,
    research_run_id: manifestRecord.research_run_id as string,
    key_id: manifestRecord.key_id as string,
  });
  deps.invokeMirror?.([versionPath, "current.json"]).catch((error: unknown) => {
    const reason = error instanceof Error ? error.message : "unknown";
    console.log(JSON.stringify({ event: "mirror_invoke_failed", reason }));
  });
  return jsonResponse(201, { sha256 });
}

if (import.meta.main) {
  Deno.serve((request) => handlePublish(request));
}
