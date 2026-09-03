import { sha256Hex } from "./canonical.ts";
import { bytesToArrayBuffer } from "./encoding.ts";

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

export interface OutcomeRow {
  client_id: string;
  strategy_key: string;
  ts: number;
  won: boolean;
  payout_pct: string;
}

export interface HubDatabase {
  maxManifestVersion(): Promise<number | null>;
  insertManifest(row: ManifestRow): Promise<void>;
  consumeRateLimit(
    bucket: string,
    clientId: string,
    windowStart: number,
    limit: number,
  ): Promise<boolean>;
  insertOutcomes(rows: OutcomeRow[]): Promise<void>;
}

export interface HubStorage {
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

export class SupabaseRestDatabase implements HubDatabase {
  constructor(private readonly baseUrl: string, private readonly serviceKey: string) {}

  async maxManifestVersion(): Promise<number | null> {
    const response = await fetch(
      `${this.baseUrl}/rest/v1/manifests?select=manifest_version&order=manifest_version.desc&limit=1`,
      { headers: serviceHeaders(this.serviceKey) },
    );
    if (!response.ok) throw new Error("HUB_DB_FAILED");
    const rows = await response.json() as Array<{ manifest_version: number }>;
    return rows.length === 0 ? null : rows[0].manifest_version;
  }

  async insertManifest(row: ManifestRow): Promise<void> {
    const response = await fetch(`${this.baseUrl}/rest/v1/manifests`, {
      method: "POST",
      headers: { ...serviceHeaders(this.serviceKey), prefer: "return=minimal" },
      body: JSON.stringify(row),
    });
    if (!response.ok) throw new Error("HUB_DB_FAILED");
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
}

export class SupabaseStorage implements HubStorage {
  constructor(private readonly baseUrl: string, private readonly serviceKey: string) {}

  async uploadManifest(
    path: string,
    body: Uint8Array,
    contentType: string,
    cacheControl: string,
  ): Promise<void> {
    const response = await fetch(`${this.baseUrl}/storage/v1/object/manifests/${path}`, {
      method: "PUT",
      headers: {
        apikey: this.serviceKey,
        authorization: `Bearer ${this.serviceKey}`,
        "content-type": contentType,
        "cache-control": `max-age=${cacheControl}`,
        "x-upsert": "true",
      },
      body: bytesToArrayBuffer(body),
    });
    if (!response.ok) throw new Error("HUB_STORAGE_FAILED");
  }

  async downloadManifest(path: string): Promise<Uint8Array> {
    const response = await fetch(`${this.baseUrl}/storage/v1/object/manifests/${path}`, {
      headers: {
        apikey: this.serviceKey,
        authorization: `Bearer ${this.serviceKey}`,
      },
    });
    if (!response.ok) throw new Error("HUB_STORAGE_FAILED");
    return new Uint8Array(await response.arrayBuffer());
  }
}

export async function manifestSha(body: Uint8Array): Promise<string> {
  return await sha256Hex(body);
}
