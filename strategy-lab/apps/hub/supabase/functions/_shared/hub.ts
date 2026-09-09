import { sha256Hex } from "./canonical.ts";
import { bytesToArrayBuffer } from "./encoding.ts";

export const GLOBAL_RATE_LIMIT_ID = "00000000-0000-4000-8000-000000000000";

export interface ManifestRow {
  manifest_version: number;
  published_at: number;
  expires_at: number;
  storage_path: string;
  sha256: string;
  signature: string;
  primitives_version: string;
  research_run_id: string;
  key_id: string;
}

export type PublicationState = "reserved" | "object_stored" | "committed" | "superseded";

export interface PublicationReservationInput {
  channel: "staging" | "production";
  manifestVersion: number;
  sha256: string;
  storagePath: string;
  researchRunId: string;
  datasetFingerprint: string | null;
  datasetKind: string | null;
  keyId: string;
}

export interface PublicationReservation {
  publication_id: string;
  state: PublicationState;
  idempotent: boolean;
}

export interface PublicationCommitInput {
  publicationId: string;
  publishedAt: number;
  expiresAt: number;
  signature: string;
  primitivesVersion: string;
}

export interface PublicationCommitResult {
  state: "committed" | "superseded";
  became_current: boolean;
  generation: number;
}

export interface ManifestCandidate {
  manifest_version: number;
  storage_path: string;
  sha256: string;
}

export interface MirrorJob {
  publication_id: string;
  manifest_version: number;
  storage_path: string;
  expected_sha256: string;
  attempts: number;
}

export interface OutcomeRow {
  client_id: string;
  strategy_key: string;
  ts: number;
  won: boolean;
  payout_pct: string;
}

export interface OutcomeEventV2 {
  client_id: string;
  event_id: string;
  schema_version: 2;
  strategy_key: string;
  recipe_revision: number;
  manifest_version: number;
  execution_semantics_version: string;
  primitives_version: string;
  asset: string;
  timeframe_s: number;
  product: "turbo" | "binary" | "digital";
  account_environment: "practice" | "real";
  source: "desktop_bot";
  signal_group_id: string;
  ts: number;
  won: boolean;
  payout_pct: string;
  hub_environment: "staging" | "production";
  received_at: number;
}

export interface OutcomeInsertResult {
  received: number;
  inserted: number;
  duplicates: number;
}

export interface OutcomeDatabase {
  consumeRateLimit(
    bucket: string,
    clientId: string,
    windowStart: number,
    limit: number,
  ): Promise<boolean>;
  consumeOutcomeBudgets(
    clientId: string,
    hourStart: number,
    dayStart: number,
    eventCount: number,
  ): Promise<string>;
  insertOutcomes(rows: OutcomeRow[]): Promise<void>;
  insertOutcomeEventsV2(rows: OutcomeEventV2[]): Promise<OutcomeInsertResult>;
}

export interface PublicationDatabase {
  reservePublication(input: PublicationReservationInput): Promise<PublicationReservation>;
  markObjectStored(publicationId: string): Promise<void>;
  commitPublication(input: PublicationCommitInput): Promise<PublicationCommitResult>;
  markProjectionSynced(publicationId: string): Promise<void>;
  recordPublicationFailure(publicationId: string, step: string, code: string): Promise<void>;
  listManifestCandidates(channel: "staging" | "production"): Promise<ManifestCandidate[]>;
  claimMirrorJob(publicationId?: string): Promise<MirrorJob | null>;
  completeMirrorJob(publicationId: string): Promise<void>;
  failMirrorJob(publicationId: string, code: string): Promise<void>;
}

export type HubDatabase = OutcomeDatabase & PublicationDatabase;

export interface HubStorage {
  createImmutableManifest(
    path: string,
    body: Uint8Array,
    contentType: string,
    cacheControl: string,
  ): Promise<"created" | "exists">;
  uploadManifest(
    path: string,
    body: Uint8Array,
    contentType: string,
    cacheControl: string,
  ): Promise<void>;
  downloadManifest(path: string): Promise<Uint8Array>;
}

export interface MirrorTarget {
  put(path: string, body: Uint8Array, contentType: string, cacheControl: string): Promise<void>;
  get(path: string): Promise<Uint8Array>;
}

export class HubOperationError extends Error {
  constructor(public readonly code: string) {
    super(code);
    this.name = "HubOperationError";
  }
}

export function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function requiredEnv(name: string): string {
  const value = Deno.env.get(name);
  if (!value) {
    throw new Error("HUB_ENV_MISSING");
  }
  return value;
}

export function serviceHeaders(serviceKey: string): HeadersInit {
  return {
    apikey: serviceKey,
    authorization: `Bearer ${serviceKey}`,
    "content-type": "application/json",
  };
}

export function storageUploadHeaders(
  serviceKey: string,
  contentType: string,
  cacheControlSeconds: string,
): HeadersInit {
  return {
    apikey: serviceKey,
    authorization: `Bearer ${serviceKey}`,
    "content-type": contentType,
    // Match the official storage-js raw-body upload contract.
    "cache-control": `max-age=${cacheControlSeconds}`,
    "x-upsert": "true",
  };
}

export function storageCreateHeaders(
  serviceKey: string,
  contentType: string,
  cacheControlSeconds: string,
): HeadersInit {
  return {
    apikey: serviceKey,
    authorization: `Bearer ${serviceKey}`,
    "content-type": contentType,
    "cache-control": `max-age=${cacheControlSeconds}`,
    "x-upsert": "false",
  };
}

export class SupabaseRestDatabase implements HubDatabase {
  constructor(private readonly baseUrl: string, private readonly serviceKey: string) {}

  async reservePublication(
    input: PublicationReservationInput,
  ): Promise<PublicationReservation> {
    return await this.rpc<PublicationReservation>("reserve_manifest_publication", {
      p_channel: input.channel,
      p_manifest_version: input.manifestVersion,
      p_sha256: input.sha256,
      p_storage_path: input.storagePath,
      p_research_run_id: input.researchRunId,
      p_dataset_fingerprint: input.datasetFingerprint,
      p_dataset_kind: input.datasetKind,
      p_key_id: input.keyId,
    });
  }

  async markObjectStored(publicationId: string): Promise<void> {
    await this.rpc<null>("mark_manifest_object_stored", {
      p_publication_id: publicationId,
    });
  }

  async commitPublication(input: PublicationCommitInput): Promise<PublicationCommitResult> {
    return await this.rpc<PublicationCommitResult>("commit_manifest_publication", {
      p_publication_id: input.publicationId,
      p_published_at: input.publishedAt,
      p_expires_at: input.expiresAt,
      p_signature: input.signature,
      p_primitives_version: input.primitivesVersion,
    });
  }

  async markProjectionSynced(publicationId: string): Promise<void> {
    await this.rpc<null>("mark_manifest_projection_synced", {
      p_publication_id: publicationId,
    });
  }

  async recordPublicationFailure(
    publicationId: string,
    step: string,
    code: string,
  ): Promise<void> {
    await this.rpc<null>("record_manifest_publication_failure", {
      p_publication_id: publicationId,
      p_step: step,
      p_code: code,
    });
  }

  async listManifestCandidates(
    channel: "staging" | "production",
  ): Promise<ManifestCandidate[]> {
    const pointerResponse = await fetch(
      `${this.baseUrl}/rest/v1/manifest_pointers?channel=eq.${channel}` +
        "&select=manifest_version,storage_path,sha256",
      { headers: serviceHeaders(this.serviceKey) },
    );
    if (!pointerResponse.ok) throw new HubOperationError("HUB_DB_FAILED");
    const pointerRows = await pointerResponse.json() as ManifestCandidate[];
    const currentVersion = pointerRows[0]?.manifest_version;
    const historyResponse = await fetch(
      `${this.baseUrl}/rest/v1/manifests?select=manifest_version,storage_path,sha256` +
        "&order=manifest_version.desc&limit=20",
      { headers: serviceHeaders(this.serviceKey) },
    );
    if (!historyResponse.ok) throw new HubOperationError("HUB_DB_FAILED");
    const history = (await historyResponse.json() as ManifestCandidate[]).map((row) => ({
      ...row,
      storage_path: row.storage_path.replace(/^manifests\//, ""),
    }));
    return [
      ...pointerRows,
      ...history.filter((row) => row.manifest_version !== currentVersion),
    ];
  }

  async claimMirrorJob(publicationId?: string): Promise<MirrorJob | null> {
    return await this.rpc<MirrorJob | null>("claim_manifest_mirror_job", {
      p_publication_id: publicationId ?? null,
    });
  }

  async completeMirrorJob(publicationId: string): Promise<void> {
    await this.rpc<null>("complete_manifest_mirror_job", {
      p_publication_id: publicationId,
    });
  }

  async failMirrorJob(publicationId: string, code: string): Promise<void> {
    await this.rpc<null>("fail_manifest_mirror_job", {
      p_publication_id: publicationId,
      p_error: code,
    });
  }

  async consumeRateLimit(
    bucket: string,
    clientId: string,
    windowStart: number,
    limit: number,
  ): Promise<boolean> {
    const response = await fetch(`${this.baseUrl}/rest/v1/rpc/consume_rate_limit`, {
      method: "POST",
      headers: serviceHeaders(this.serviceKey),
      body: JSON.stringify({
        rate_bucket: bucket,
        rate_client_id: clientId,
        rate_window_start: windowStart,
        rate_limit: limit,
      }),
    });
    if (!response.ok) throw new Error("HUB_DB_FAILED");
    return await response.json() as boolean;
  }

  async consumeOutcomeBudgets(
    clientId: string,
    hourStart: number,
    dayStart: number,
    eventCount: number,
  ): Promise<string> {
    return await this.rpc<string>("consume_outcome_budgets", {
      rate_client_id: clientId,
      rate_hour_start: hourStart,
      rate_day_start: dayStart,
      event_count: eventCount,
    });
  }

  async insertOutcomes(rows: OutcomeRow[]): Promise<void> {
    const response = await fetch(
      `${this.baseUrl}/rest/v1/live_outcomes?on_conflict=client_id,strategy_key,ts`,
      {
        method: "POST",
        headers: {
          ...serviceHeaders(this.serviceKey),
          prefer: "resolution=ignore-duplicates,return=minimal",
        },
        body: JSON.stringify(rows),
      },
    );
    if (!response.ok) throw new Error("HUB_DB_FAILED");
  }

  async insertOutcomeEventsV2(rows: OutcomeEventV2[]): Promise<OutcomeInsertResult> {
    return await this.rpc<OutcomeInsertResult>("ingest_live_outcomes_v2", { p_rows: rows });
  }

  private async rpc<T>(name: string, body: Record<string, unknown>): Promise<T> {
    const response = await fetch(`${this.baseUrl}/rest/v1/rpc/${name}`, {
      method: "POST",
      headers: serviceHeaders(this.serviceKey),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const text = await response.text();
      const match = text.match(/HUB_[A-Z0-9_]+/);
      throw new HubOperationError(match?.[0] ?? "HUB_DB_FAILED");
    }
    if (response.status === 204) return null as T;
    return await response.json() as T;
  }
}

export class SupabaseStorage implements HubStorage {
  constructor(private readonly baseUrl: string, private readonly serviceKey: string) {}

  async createImmutableManifest(
    path: string,
    body: Uint8Array,
    contentType: string,
    cacheControl: string,
  ): Promise<"created" | "exists"> {
    const response = await fetch(`${this.baseUrl}/storage/v1/object/manifests/${path}`, {
      method: "POST",
      headers: storageCreateHeaders(this.serviceKey, contentType, cacheControl),
      body: bytesToArrayBuffer(body),
    });
    if (response.ok) return "created";
    if (response.status === 400 || response.status === 409) return "exists";
    throw new HubOperationError("HUB_STORAGE_FAILED");
  }

  async uploadManifest(
    path: string,
    body: Uint8Array,
    contentType: string,
    cacheControl: string,
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}/storage/v1/object/manifests/${path}`, {
      method: "PUT",
      headers: storageUploadHeaders(this.serviceKey, contentType, cacheControl),
      body: bytesToArrayBuffer(body),
    });
    if (!response.ok) throw new HubOperationError("HUB_STORAGE_FAILED");
  }

  async downloadManifest(path: string): Promise<Uint8Array> {
    const response = await fetch(`${this.baseUrl}/storage/v1/object/manifests/${path}`, {
      headers: {
        apikey: this.serviceKey,
        authorization: `Bearer ${this.serviceKey}`,
      },
    });
    if (!response.ok) throw new HubOperationError("HUB_STORAGE_FAILED");
    return new Uint8Array(await response.arrayBuffer());
  }
}

export async function manifestSha(body: Uint8Array): Promise<string> {
  return await sha256Hex(body);
}
