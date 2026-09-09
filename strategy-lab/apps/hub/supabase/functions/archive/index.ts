// CAT-17 / R-HUB-7: archive control-plane only.
// Parquet conversion, Storage verification and exact deletion run in the isolated Lab process.

interface ArchiveBacklog {
  requested: number;
  claimed: number;
  verified: number;
  failed: number;
}

export interface ArchiveControlDatabase {
  plan(): Promise<string | null>;
  backlog(): Promise<ArchiveBacklog>;
}

class SupabaseArchiveControlDatabase implements ArchiveControlDatabase {
  constructor(private readonly baseUrl: string, private readonly serviceKey: string) {}

  async plan(): Promise<string | null> {
    const response = await fetch(`${this.baseUrl}/rest/v1/rpc/archive_old_candles`, {
      method: "POST",
      headers: this.headers(),
      body: "{}",
    });
    if (!response.ok) throw new Error("ARCHIVE_PLAN_FAILED");
    const value: unknown = await response.json();
    return typeof value === "string" ? value : null;
  }

  async backlog(): Promise<ArchiveBacklog> {
    const response = await fetch(
      `${this.baseUrl}/rest/v1/cold_archive_jobs?select=status&status=neq.completed`,
      { method: "GET", headers: this.headers() },
    );
    if (!response.ok) throw new Error("ARCHIVE_BACKLOG_FAILED");
    const rows = await response.json() as Array<{ status: string }>;
    const result: ArchiveBacklog = { requested: 0, claimed: 0, verified: 0, failed: 0 };
    for (const row of rows) {
      if (row.status in result) result[row.status as keyof ArchiveBacklog] += 1;
    }
    return result;
  }

  private headers(): HeadersInit {
    return {
      apikey: this.serviceKey,
      authorization: `Bearer ${this.serviceKey}`,
      "content-type": "application/json",
    };
  }
}

export interface ArchiveDeps {
  db: ArchiveControlDatabase;
  controlToken: string;
  backlogAlertThreshold: number;
}

function defaultDeps(): ArchiveDeps {
  const baseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const controlToken = Deno.env.get("ARCHIVE_CONTROL_TOKEN") ?? "";
  if (!baseUrl || !serviceKey || !controlToken) throw new Error("ARCHIVE_ENV_MISSING");
  return {
    db: new SupabaseArchiveControlDatabase(baseUrl, serviceKey),
    controlToken,
    backlogAlertThreshold: 7,
  };
}

export async function handleArchive(
  request: Request,
  deps: ArchiveDeps = defaultDeps(),
): Promise<Response> {
  if (!tokenMatches(request.headers.get("x-archive-control-token") ?? "", deps.controlToken)) {
    return json(401, { error: "ARCHIVE_UNAUTHORIZED" });
  }
  if (request.method !== "POST" && request.method !== "GET") {
    return json(405, { error: "METHOD_NOT_ALLOWED" });
  }
  try {
    const jobId = request.method === "POST" ? await deps.db.plan() : null;
    const backlog = await deps.db.backlog();
    const pending = backlog.requested + backlog.claimed + backlog.verified;
    return json(200, {
      event: "strategy_lab_archive_control",
      job_id: jobId,
      backlog,
      executor_required: pending > 0,
      alert: pending >= deps.backlogAlertThreshold ? "ARCHIVE_EXECUTOR_BACKLOG" : null,
      deletion_performed: false,
    });
  } catch {
    return json(503, { error: "ARCHIVE_CONTROL_UNAVAILABLE" });
  }
}

function tokenMatches(received: string, expected: string): boolean {
  if (!received || received.length !== expected.length) return false;
  let difference = 0;
  for (let index = 0; index < received.length; index += 1) {
    difference |= received.charCodeAt(index) ^ expected.charCodeAt(index);
  }
  return difference === 0;
}

function json(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

if (import.meta.main) Deno.serve((request) => handleArchive(request));
