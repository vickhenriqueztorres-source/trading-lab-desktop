import { canonicalBytes } from "../_shared/canonical.ts";
import {
  keyEnvFromDeno,
  type ManifestKeyEnv,
  verifyManifestSignature,
} from "../_shared/ed25519.ts";
import {
  HubOperationError,
  type HubStorage,
  jsonResponse,
  manifestSha,
  type PublicationCommitResult,
  type PublicationDatabase,
  type PublicationReservation,
  requiredEnv,
  SupabaseRestDatabase,
  SupabaseStorage,
} from "../_shared/hub.ts";
import { parseJsonNoDuplicate, validateManifestSchema } from "../_shared/manifest_schema.ts";

const CACHE_SECONDS = "900";
const CONTENT_TYPE = "application/json";

export interface PublishDeps {
  db: PublicationDatabase;
  storage: HubStorage;
  keyEnv: ManifestKeyEnv;
  invokeMirror?: (publicationId: string) => Promise<boolean>;
}

export function defaultPublishDeps(): PublishDeps {
  const supabaseUrl = requiredEnv("SUPABASE_URL");
  const serviceKey = requiredEnv("SUPABASE_SERVICE_ROLE_KEY");
  return {
    db: new SupabaseRestDatabase(supabaseUrl, serviceKey),
    storage: new SupabaseStorage(supabaseUrl, serviceKey),
    keyEnv: keyEnvFromDeno(),
    invokeMirror: async (publicationId: string) => {
      const response = await fetch(`${supabaseUrl}/functions/v1/mirror`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${serviceKey}`,
          "content-type": CONTENT_TYPE,
        },
        body: JSON.stringify({ publication_id: publicationId }),
      });
      return response.ok;
    },
  };
}

export async function handlePublish(
  request: Request,
  deps: PublishDeps = defaultPublishDeps(),
): Promise<Response> {
  if (request.method !== "POST") return jsonResponse(405, { error: "METHOD_NOT_ALLOWED" });

  let manifest: unknown;
  try {
    manifest = parseJsonNoDuplicate(await request.text());
  } catch {
    return jsonResponse(400, { error: "INVALID_JSON" });
  }
  const schemaProblem = validateManifestSchema(manifest);
  if (schemaProblem) return jsonResponse(422, { error: schemaProblem });
  const record = manifest as Record<string, unknown>;
  const policyProblem = productionPolicyProblem(record, deps.keyEnv.hubEnv);
  if (policyProblem) return jsonResponse(422, { error: policyProblem });
  if (!(await verifyManifestSignature(record, deps.keyEnv))) {
    return jsonResponse(401, { error: "MANIFEST_SIGNATURE_INVALID" });
  }

  const body = canonicalBytes(record);
  const sha256 = await manifestSha(body);
  const manifestVersion = record.manifest_version as number;
  const versionPath = `v${manifestVersion}.json`;
  const dataset = asRecord(record.dataset_evidence);
  const channel = publicationChannel(deps.keyEnv.hubEnv);

  let reservation: PublicationReservation;
  try {
    reservation = await deps.db.reservePublication({
      channel,
      manifestVersion,
      sha256,
      storagePath: versionPath,
      researchRunId: record.research_run_id as string,
      datasetFingerprint: typeof dataset?.fingerprint === "string" ? dataset.fingerprint : null,
      datasetKind: typeof dataset?.kind === "string" ? dataset.kind : null,
      keyId: record.key_id as string,
    });
  } catch (error) {
    return publicationError(error, 503);
  }
  if (reservation.state === "superseded") {
    return jsonResponse(409, { error: "MANIFEST_VERSION_SUPERSEDED" });
  }

  const objectProblem = await ensureImmutableObject(
    deps,
    reservation.publication_id,
    versionPath,
    body,
    sha256,
  );
  if (objectProblem) return objectProblem;

  try {
    await deps.db.markObjectStored(reservation.publication_id);
  } catch (error) {
    await recordFailure(deps.db, reservation.publication_id, "object_mark", "HUB_DB_FAILED");
    return publicationError(error, 503);
  }

  let commit: PublicationCommitResult;
  try {
    commit = await deps.db.commitPublication({
      publicationId: reservation.publication_id,
      publishedAt: record.published_at as number,
      expiresAt: record.expires_at as number,
      signature: record.signature as string,
      primitivesVersion: record.primitives_version as string,
    });
  } catch (error) {
    await recordFailure(deps.db, reservation.publication_id, "db_commit", "HUB_DB_FAILED");
    return publicationError(error, 503);
  }
  if (commit.state === "superseded" || !commit.became_current) {
    return jsonResponse(409, { error: "MANIFEST_VERSION_SUPERSEDED", sha256 });
  }

  const legacyCurrentSynced = await repairLegacyCurrent(deps, channel);
  if (legacyCurrentSynced) {
    try {
      await deps.db.markProjectionSynced(reservation.publication_id);
    } catch {
      await recordFailure(
        deps.db,
        reservation.publication_id,
        "legacy_projection_mark",
        "HUB_CURRENT_REPAIR_PENDING",
      );
    }
  } else {
    await recordFailure(
      deps.db,
      reservation.publication_id,
      "legacy_projection",
      "HUB_CURRENT_REPAIR_PENDING",
    );
  }

  let mirrorCompleted = false;
  if (deps.invokeMirror) {
    try {
      mirrorCompleted = await deps.invokeMirror(reservation.publication_id);
    } catch {
      mirrorCompleted = false;
    }
  }
  return jsonResponse(reservation.idempotent ? 200 : 201, {
    channel,
    generation: commit.generation,
    legacy_current_synced: legacyCurrentSynced,
    mirror_completed: mirrorCompleted,
    publication_id: reservation.publication_id,
    sha256,
  });
}

async function ensureImmutableObject(
  deps: PublishDeps,
  publicationId: string,
  path: string,
  body: Uint8Array,
  expectedSha: string,
): Promise<Response | null> {
  try {
    await deps.storage.createImmutableManifest(path, body, CONTENT_TYPE, CACHE_SECONDS);
    const stored = await deps.storage.downloadManifest(path);
    if (await manifestSha(stored) !== expectedSha) {
      await recordFailure(deps.db, publicationId, "object_verify", "HUB_OBJECT_HASH_MISMATCH");
      return jsonResponse(409, { error: "HUB_OBJECT_HASH_MISMATCH" });
    }
    return null;
  } catch {
    await recordFailure(deps.db, publicationId, "object_upload", "HUB_STORAGE_FAILED");
    return jsonResponse(503, { error: "HUB_STORAGE_FAILED" });
  }
}

async function repairLegacyCurrent(
  deps: PublishDeps,
  channel: "staging" | "production",
): Promise<boolean> {
  try {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      const before = (await deps.db.listManifestCandidates(channel))[0];
      if (!before) return false;
      const body = await deps.storage.downloadManifest(before.storage_path);
      if (await manifestSha(body) !== before.sha256) return false;
      await deps.storage.uploadManifest("current.json", body, CONTENT_TYPE, CACHE_SECONDS);
      const after = (await deps.db.listManifestCandidates(channel))[0];
      if (after?.manifest_version === before.manifest_version && after.sha256 === before.sha256) {
        return true;
      }
    }
  } catch {
    return false;
  }
  return false;
}

async function recordFailure(
  db: PublicationDatabase,
  publicationId: string,
  step: string,
  code: string,
): Promise<void> {
  try {
    await db.recordPublicationFailure(publicationId, step, code);
  } catch {
    // The original durable operation failed; telemetry must not mask its fail-closed response.
  }
}

function publicationError(error: unknown, fallbackStatus = 409): Response {
  const code = error instanceof HubOperationError ? error.code : "HUB_DB_FAILED";
  const conflictCodes = new Set([
    "HUB_MANIFEST_VERSION_CONFLICT",
    "HUB_MANIFEST_VERSION_REGRESSION",
  ]);
  const evidenceCodes = new Set([
    "HUB_PRODUCTION_EVIDENCE_REQUIRED",
    "HUB_RESEARCH_RUN_NOT_SEALED",
    "HUB_DATASET_EVIDENCE_INCOMPATIBLE",
    "HUB_APPROVED_RESEARCH_EVIDENCE_REQUIRED",
    "HUB_PORTFOLIO_EVIDENCE_REQUIRED",
  ]);
  if (conflictCodes.has(code)) return jsonResponse(409, { error: code });
  if (evidenceCodes.has(code)) return jsonResponse(422, { error: code });
  return jsonResponse(fallbackStatus, { error: code });
}

function productionPolicyProblem(
  manifest: Record<string, unknown>,
  hubEnv: string,
): string | null {
  if (hubEnv === "staging") return null;
  const dataset = asRecord(manifest.dataset_evidence);
  if (manifest.schema_revision !== "1.2" || dataset?.kind !== "real_market") {
    return "HUB_PRODUCTION_EVIDENCE_REQUIRED";
  }
  return null;
}

function publicationChannel(hubEnv: string): "staging" | "production" {
  return hubEnv === "staging" ? "staging" : "production";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

if (import.meta.main) {
  Deno.serve((request) => handlePublish(request));
}
