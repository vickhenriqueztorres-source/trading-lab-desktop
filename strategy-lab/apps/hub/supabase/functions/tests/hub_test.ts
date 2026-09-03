import { assert, assertEquals } from "jsr:@std/assert@1";
import { canonicalBytes } from "../_shared/canonical.ts";
import type { ManifestKeyEnv } from "../_shared/ed25519.ts";
import type {
  HubDatabase,
  HubStorage,
  ManifestRow,
  MirrorTarget,
  OutcomeRow,
} from "../_shared/hub.ts";
import { signAnonJwt } from "../_shared/jwt.ts";
import { handleClientToken } from "../client_token/index.ts";
import { handleMirror } from "../mirror/index.ts";
import { handleOutcomes } from "../outcomes/index.ts";
import { handlePublish } from "../publish/index.ts";

const ROOT = new URL("../../../../../", import.meta.url);
const MANIFEST_URL = new URL("tests/fixtures/manifest_example.json", ROOT);
const TEST_PUBKEY_URL = new URL("tests/keys/ed25519-test.public.hex", ROOT);
const TEST_PUBLIC_KEY = (await Deno.readTextFile(TEST_PUBKEY_URL)).trim();
const JWT_SECRET = "test-only-local-hub-secret";
const CLIENT_ID = "018f81d6-25d4-4f3f-8e1d-294f5bcdef01";
const NOW_TS = 1_788_350_500;

class FakeDatabase implements HubDatabase {
  insertedManifest: ManifestRow | null = null;
  insertedOutcomes: OutcomeRow[] = [];

  constructor(
    public maxVersion: number | null = null,
    public rateLimitAllowed = true,
  ) {}

  maxManifestVersion(): Promise<number | null> {
    return Promise.resolve(this.maxVersion);
  }

  insertManifest(row: ManifestRow): Promise<void> {
    this.insertedManifest = row;
    this.maxVersion = row.manifest_version;
    return Promise.resolve();
  }

  consumeRateLimit(
    _bucket: string,
    _clientId: string,
    _windowStart: number,
    _limit: number,
  ): Promise<boolean> {
    return Promise.resolve(this.rateLimitAllowed);
  }

  insertOutcomes(rows: OutcomeRow[]): Promise<void> {
    this.insertedOutcomes.push(...rows);
    return Promise.resolve();
  }
}

class FakeStorage implements HubStorage {
  objects = new Map<string, Uint8Array>();

  uploadManifest(
    path: string,
    body: Uint8Array,
    _contentType: string,
    _cacheControl: string,
  ): Promise<void> {
    this.objects.set(path, body);
    return Promise.resolve();
  }

  downloadManifest(path: string): Promise<Uint8Array> {
    const body = this.objects.get(path);
    if (!body) throw new Error("MISSING_FAKE_OBJECT");
    return Promise.resolve(body);
  }
}

class FakeMirrorTarget implements MirrorTarget {
  mirrored = new Map<string, Uint8Array>();

  put(
    path: string,
    body: Uint8Array,
    _contentType: string,
    _cacheControl: string,
  ): Promise<void> {
    this.mirrored.set(path, body);
    return Promise.resolve();
  }
}

Deno.test("publish accepts a manifest signed by key A", async () => {
  const manifest = await loadManifest();
  const db = new FakeDatabase(null);
  const storage = new FakeStorage();
  const mirrored: string[][] = [];
  const response = await handlePublish(postJson(manifest), {
    db,
    storage,
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
    invokeMirror: (paths) => {
      mirrored.push(paths);
      return Promise.resolve();
    },
  });

  assertEquals(response.status, 201);
  assertEquals(db.insertedManifest?.manifest_version, 14);
  assert(storage.objects.has("v14.json"));
  assert(storage.objects.has("current.json"));
  assertEquals(mirrored, [["v14.json", "current.json"]]);
});

Deno.test("publish accepts a manifest signed by key B", async () => {
  const manifest = await loadManifest();
  manifest.key_id = "B";
  manifest.signature =
    "ed25519:rXw61jRA7TRYfYF5kYPCfAJv21haJKkR3K2WUadgWeB93XG5cDJ9Jy4U6Pw7Q4+Up0HNtUdt/R/1lmM36wcyCg==";
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(null),
    storage: new FakeStorage(),
    keyEnv: keyEnv({ manifestPubkeyB: TEST_PUBLIC_KEY }),
  });

  assertEquals(response.status, 201);
});

Deno.test("publish rejects an invalid signature", async () => {
  const manifest = await loadManifest();
  manifest.signature = `${manifest.signature as string}A`;
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(null),
    storage: new FakeStorage(),
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });

  assertEquals(response.status, 401);
});

Deno.test("publish rejects the test trust root outside staging", async () => {
  const manifest = await loadManifest();
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(null),
    storage: new FakeStorage(),
    keyEnv: { hubEnv: "production", manifestTestPubkey: TEST_PUBLIC_KEY },
  });

  assertEquals(response.status, 401);
});

Deno.test("publish rejects regressive manifest versions", async () => {
  const manifest = await loadManifest();
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(14),
    storage: new FakeStorage(),
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });

  assertEquals(response.status, 409);
});

Deno.test("publish rejects invalid schema before any write", async () => {
  const manifest = await loadManifest();
  delete manifest.strategies;
  const storage = new FakeStorage();
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(null),
    storage,
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });

  assertEquals(response.status, 422);
  assertEquals(storage.objects.size, 0);
});

Deno.test("Python signed fixture verifies in Deno using the same canonical bytes", async () => {
  const manifest = await loadManifest();
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(null),
    storage: new FakeStorage(),
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });

  assertEquals(response.status, 201);
  assert(canonicalBytes(manifest).length > 0);
});

Deno.test("outcomes rejects future timestamps", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS);
  const response = await handleOutcomes(
    postJson(
      {
        outcomes: [{ strategy_key: "f1", ts: NOW_TS + 1, won: true, payout_pct: "0.87" }],
      },
      token,
    ),
    { db: new FakeDatabase(null), jwtSecret: JWT_SECRET, nowTs: () => NOW_TS },
  );

  assertEquals(response.status, 422);
});

Deno.test("outcomes rate limits by client and fails closed", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS);
  const response = await handleOutcomes(
    postJson(
      {
        outcomes: [{ strategy_key: "f1", ts: NOW_TS, won: true, payout_pct: "0.87" }],
      },
      token,
    ),
    { db: new FakeDatabase(null, false), jwtSecret: JWT_SECRET, nowTs: () => NOW_TS },
  );

  assertEquals(response.status, 429);
});

Deno.test("outcomes injects client_id from JWT and never trusts body client_id", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS);
  const db = new FakeDatabase(null, true);
  const response = await handleOutcomes(
    postJson(
      {
        outcomes: [
          {
            client_id: "11111111-1111-4111-8111-111111111111",
            strategy_key: "f1",
            ts: NOW_TS,
            won: false,
            payout_pct: "0.00",
          },
        ],
      },
      token,
    ),
    { db, jwtSecret: JWT_SECRET, nowTs: () => NOW_TS },
  );

  assertEquals(response.status, 202);
  assertEquals(db.insertedOutcomes[0].client_id, CLIENT_ID);
});

Deno.test("client_token issues a one year anonymous token for a bot UUID", async () => {
  const response = await handleClientToken(postJson({ client_id: CLIENT_ID }), {
    jwtSecret: JWT_SECRET,
    nowTs: () => NOW_TS,
  });
  const payload = await response.json();

  assertEquals(response.status, 201);
  assertEquals(typeof payload.token, "string");
  assertEquals(payload.expires_in, 365 * 86400);
});

Deno.test("mirror copies versioned and current manifests to the configured target", async () => {
  const storage = new FakeStorage();
  storage.objects.set("v14.json", new TextEncoder().encode('{"ok":true}'));
  storage.objects.set("current.json", new TextEncoder().encode('{"ok":true}'));
  const target = new FakeMirrorTarget();
  const response = await handleMirror(postJson({ paths: ["v14.json", "current.json"] }), {
    storage,
    target,
  });

  assertEquals(response.status, 200);
  assert(target.mirrored.has("v14.json"));
  assert(target.mirrored.has("current.json"));
});

async function loadManifest(): Promise<Record<string, unknown>> {
  return JSON.parse(await Deno.readTextFile(MANIFEST_URL)) as Record<string, unknown>;
}

function postJson(payload: unknown, token?: string): Request {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request("http://localhost", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });
}

function keyEnv(values: Partial<ManifestKeyEnv>): ManifestKeyEnv {
  return { hubEnv: "production", ...values };
}
