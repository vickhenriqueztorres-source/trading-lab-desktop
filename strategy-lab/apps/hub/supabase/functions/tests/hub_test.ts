import { assert, assertEquals } from "jsr:@std/assert@1";
import { canonicalBytes } from "../_shared/canonical.ts";
import { type ManifestKeyEnv, verifyManifestSignature } from "../_shared/ed25519.ts";
import {
  type HubDatabase,
  HubOperationError,
  type HubStorage,
  type ManifestCandidate,
  manifestSha,
  type MirrorJob,
  type MirrorTarget,
  type OutcomeEventV2,
  type OutcomeInsertResult,
  type OutcomeRow,
  type PublicationCommitInput,
  type PublicationCommitResult,
  type PublicationReservation,
  type PublicationReservationInput,
  storageCreateHeaders,
  storageUploadHeaders,
} from "../_shared/hub.ts";
import { signAnonJwt } from "../_shared/jwt.ts";
import { validateManifestSchema } from "../_shared/manifest_schema.ts";
import { handleClientToken } from "../client_token/index.ts";
import { handleManifestCurrent } from "../manifest_current/index.ts";
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
const VALID_OUTCOME_TS = NOW_TS - (NOW_TS % 60);

Deno.test("Supabase upload headers distinguish immutable create from legacy projection", () => {
  const mutable = new Headers(storageUploadHeaders("key", "application/json", "900"));
  const immutable = new Headers(storageCreateHeaders("key", "application/json", "900"));
  assertEquals(mutable.get("x-upsert"), "true");
  assertEquals(immutable.get("x-upsert"), "false");
  assertEquals(immutable.get("cache-control"), "max-age=900");
});

Deno.test("additive v1.1 requires integer warmup and preserves legacy input", async () => {
  const manifest = await loadManifest();
  assertEquals(validateManifestSchema(manifest), null);
  manifest.schema_revision = "1.1";
  assertEquals(validateManifestSchema(manifest), "MANIFEST_WARMUP_REQUIRED");
  for (const strategy of manifest.strategies as Record<string, unknown>[]) {
    strategy.warmup_required = 28;
  }
  assertEquals(validateManifestSchema(manifest), null);
  (manifest.strategies as Record<string, unknown>[])[0].warmup_required = "28";
  assertEquals(validateManifestSchema(manifest), "STRATEGY_WARMUP");
});

type Journal = {
  input: PublicationReservationInput;
  state: "reserved" | "object_stored" | "committed" | "superseded";
};

class FakeDatabase implements HubDatabase {
  insertedOutcomes: OutcomeRow[] = [];
  insertedOutcomeEvents = new Map<string, OutcomeEventV2>();
  outcomeSignalReports = new Map<string, number>();
  journals = new Map<string, Journal>();
  candidates: ManifestCandidate[] = [];
  mirrorJobs = new Map<string, MirrorJob & { status: string; last_error?: string }>();
  failures: Array<{ publicationId: string; step: string; code: string }> = [];
  pointer: ManifestCandidate | null = null;
  generation = 0;
  rateLimitAllowed = true;
  outcomeBudgetResult = "ALLOWED";
  deniedRateBuckets = new Set<string>();
  rateLimitCalls: string[] = [];
  failReserveOnce = false;
  failCommitOnce = false;
  failProjectionMarkOnce = false;
  failClaimOnce = false;

  reservePublication(input: PublicationReservationInput): Promise<PublicationReservation> {
    if (this.failReserveOnce) {
      this.failReserveOnce = false;
      return Promise.reject(new HubOperationError("HUB_DB_FAILED"));
    }
    const id = `${input.channel}:${input.manifestVersion}`;
    const existing = this.journals.get(id);
    if (existing) {
      if (existing.input.sha256 !== input.sha256) {
        return Promise.reject(new HubOperationError("HUB_MANIFEST_VERSION_CONFLICT"));
      }
      return Promise.resolve({ publication_id: id, state: existing.state, idempotent: true });
    }
    const greatest = Math.max(
      this.pointer?.manifest_version ?? 0,
      ...[...this.journals.values()].map((row) => row.input.manifestVersion),
    );
    if (input.manifestVersion <= greatest) {
      return Promise.reject(new HubOperationError("HUB_MANIFEST_VERSION_REGRESSION"));
    }
    this.journals.set(id, { input, state: "reserved" });
    return Promise.resolve({ publication_id: id, state: "reserved", idempotent: false });
  }

  markObjectStored(publicationId: string): Promise<void> {
    const journal = this.requireJournal(publicationId);
    if (journal.state === "reserved") journal.state = "object_stored";
    return Promise.resolve();
  }

  commitPublication(input: PublicationCommitInput): Promise<PublicationCommitResult> {
    if (this.failCommitOnce) {
      this.failCommitOnce = false;
      throw new HubOperationError("HUB_DB_FAILED");
    }
    const journal = this.requireJournal(input.publicationId);
    if (journal.state === "reserved") throw new HubOperationError("HUB_OBJECT_NOT_CONFIRMED");
    if (journal.state === "committed" || journal.state === "superseded") {
      return Promise.resolve({
        state: journal.state,
        became_current: this.pointer?.manifest_version === journal.input.manifestVersion,
        generation: this.generation,
      });
    }
    const candidate = {
      manifest_version: journal.input.manifestVersion,
      storage_path: journal.input.storagePath,
      sha256: journal.input.sha256,
    };
    if (!this.pointer || candidate.manifest_version > this.pointer.manifest_version) {
      this.pointer = candidate;
      this.generation += 1;
      journal.state = "committed";
      this.candidates = [
        candidate,
        ...this.candidates.filter((row) => row.manifest_version !== candidate.manifest_version),
      ];
      this.mirrorJobs.set(input.publicationId, {
        publication_id: input.publicationId,
        manifest_version: candidate.manifest_version,
        storage_path: candidate.storage_path,
        expected_sha256: candidate.sha256,
        attempts: 0,
        status: "pending",
      });
      return Promise.resolve({
        state: "committed",
        became_current: true,
        generation: this.generation,
      });
    }
    journal.state = "superseded";
    return Promise.resolve({
      state: "superseded",
      became_current: false,
      generation: this.generation,
    });
  }

  markProjectionSynced(_publicationId: string): Promise<void> {
    if (this.failProjectionMarkOnce) {
      this.failProjectionMarkOnce = false;
      throw new HubOperationError("HUB_DB_FAILED");
    }
    return Promise.resolve();
  }

  recordPublicationFailure(publicationId: string, step: string, code: string): Promise<void> {
    this.failures.push({ publicationId, step, code });
    return Promise.resolve();
  }

  listManifestCandidates(_channel: "staging" | "production"): Promise<ManifestCandidate[]> {
    if (!this.pointer) return Promise.resolve([]);
    return Promise.resolve([
      this.pointer,
      ...this.candidates.filter((row) => row.manifest_version !== this.pointer?.manifest_version),
    ]);
  }

  claimMirrorJob(publicationId?: string): Promise<MirrorJob | null> {
    if (this.failClaimOnce) {
      this.failClaimOnce = false;
      throw new HubOperationError("HUB_DB_FAILED");
    }
    const job = [...this.mirrorJobs.values()].find((row) =>
      (!publicationId || row.publication_id === publicationId) &&
      (row.status === "pending" || row.status === "failed") && row.attempts < 5
    );
    if (!job) return Promise.resolve(null);
    job.status = "processing";
    job.attempts += 1;
    return Promise.resolve(job);
  }

  completeMirrorJob(publicationId: string): Promise<void> {
    const job = this.mirrorJobs.get(publicationId);
    if (job) job.status = "done";
    return Promise.resolve();
  }

  failMirrorJob(publicationId: string, code: string): Promise<void> {
    const job = this.mirrorJobs.get(publicationId);
    if (job) {
      job.status = "failed";
      job.last_error = code;
    }
    return Promise.resolve();
  }

  consumeRateLimit(
    bucket: string,
    _clientId: string,
    _windowStart: number,
    _limit: number,
  ): Promise<boolean> {
    this.rateLimitCalls.push(bucket);
    return Promise.resolve(this.rateLimitAllowed && !this.deniedRateBuckets.has(bucket));
  }

  insertOutcomes(rows: OutcomeRow[]): Promise<void> {
    this.insertedOutcomes.push(...rows);
    return Promise.resolve();
  }

  consumeOutcomeBudgets(
    _clientId: string,
    _hourStart: number,
    _dayStart: number,
    _eventCount: number,
  ): Promise<string> {
    return Promise.resolve(this.outcomeBudgetResult);
  }

  insertOutcomeEventsV2(rows: OutcomeEventV2[]): Promise<OutcomeInsertResult> {
    let inserted = 0;
    for (const row of rows) {
      const eventKey = `${row.client_id}:${row.event_id}`;
      const clientSignalKey = [
        row.client_id,
        row.strategy_key,
        row.recipe_revision,
        row.signal_group_id,
      ].join(":");
      if (
        this.insertedOutcomeEvents.has(eventKey) || this.outcomeSignalReports.has(clientSignalKey)
      ) {
        continue;
      }
      this.insertedOutcomeEvents.set(eventKey, row);
      this.outcomeSignalReports.set(clientSignalKey, 1);
      inserted += 1;
    }
    return Promise.resolve({ received: rows.length, inserted, duplicates: rows.length - inserted });
  }

  private requireJournal(publicationId: string): Journal {
    const journal = this.journals.get(publicationId);
    if (!journal) throw new HubOperationError("HUB_PUBLICATION_NOT_FOUND");
    return journal;
  }
}

class FakeStorage implements HubStorage {
  objects = new Map<string, Uint8Array>();
  failCreateOnce = false;
  failMutableUploadOnce = false;

  createImmutableManifest(
    path: string,
    body: Uint8Array,
    _contentType: string,
    _cacheControl: string,
  ): Promise<"created" | "exists"> {
    if (this.failCreateOnce) {
      this.failCreateOnce = false;
      throw new HubOperationError("HUB_STORAGE_FAILED");
    }
    if (this.objects.has(path)) return Promise.resolve("exists");
    this.objects.set(path, body.slice());
    return Promise.resolve("created");
  }

  uploadManifest(
    path: string,
    body: Uint8Array,
    _contentType: string,
    _cacheControl: string,
  ): Promise<void> {
    if (this.failMutableUploadOnce) {
      this.failMutableUploadOnce = false;
      throw new HubOperationError("HUB_STORAGE_FAILED");
    }
    this.objects.set(path, body.slice());
    return Promise.resolve();
  }

  downloadManifest(path: string): Promise<Uint8Array> {
    const body = this.objects.get(path);
    if (!body) throw new HubOperationError("HUB_STORAGE_FAILED");
    return Promise.resolve(body.slice());
  }
}

class FakeMirrorTarget implements MirrorTarget {
  mirrored = new Map<string, Uint8Array>();
  tamperRead = false;

  put(
    path: string,
    body: Uint8Array,
    _contentType: string,
    _cacheControl: string,
  ): Promise<void> {
    this.mirrored.set(path, body.slice());
    return Promise.resolve();
  }

  get(path: string): Promise<Uint8Array> {
    if (this.tamperRead) return Promise.resolve(new TextEncoder().encode("tampered"));
    const body = this.mirrored.get(path);
    if (!body) throw new Error("MIRROR_MISSING");
    return Promise.resolve(body.slice());
  }
}

Deno.test("publish accepts keys A and B in staging", async () => {
  const a = await publishFixture({ manifestPubkeyA: TEST_PUBLIC_KEY });
  assertEquals(a.response.status, 201);
  assertEquals(a.db.pointer?.manifest_version, 14);
  assert(a.storage.objects.has("v14.json"));
  assert(a.storage.objects.has("current.json"));

  const manifestB = await loadManifest();
  manifestB.key_id = "B";
  manifestB.signature =
    "ed25519:rXw61jRA7TRYfYF5kYPCfAJv21haJKkR3K2WUadgWeB93XG5cDJ9Jy4U6Pw7Q4+Up0HNtUdt/R/1lmM36wcyCg==";
  const responseB = await handlePublish(postJson(manifestB), {
    db: new FakeDatabase(),
    storage: new FakeStorage(),
    keyEnv: keyEnv({ manifestPubkeyB: TEST_PUBLIC_KEY }),
  });
  assertEquals(responseB.status, 201);
});

Deno.test("publish rejects invalid signature and test trust root in production", async () => {
  const manifest = await loadManifest();
  manifest.signature = `${manifest.signature as string}A`;
  const invalid = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(),
    storage: new FakeStorage(),
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });
  assertEquals(invalid.status, 401);

  const original = await loadManifest();
  assertEquals(
    await verifyManifestSignature(original, {
      hubEnv: "production",
      manifestTestPubkey: TEST_PUBLIC_KEY,
    }),
    false,
  );
});

Deno.test("production publication requires the sealed real-market evidence contract", async () => {
  const result = await publishFixture({ manifestPubkeyA: TEST_PUBLIC_KEY }, "production");
  assertEquals(result.response.status, 422);
  assertEquals(result.storage.objects.size, 0);
  assertEquals(result.db.journals.size, 0);
});

Deno.test("publish retry is idempotent and immutable version bytes never change", async () => {
  const manifest = await loadManifest();
  const db = new FakeDatabase();
  const storage = new FakeStorage();
  const deps = { db, storage, keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }) };
  const first = await handlePublish(postJson(manifest), deps);
  const original = storage.objects.get("v14.json")?.slice();
  const second = await handlePublish(postJson(manifest), deps);
  assertEquals(first.status, 201);
  assertEquals(second.status, 200);
  assertEquals(storage.objects.get("v14.json"), original);
  assertEquals(db.generation, 1);
});

Deno.test("regressive and same-version different-hash reservations are conflicts", async () => {
  const db = new FakeDatabase();
  const base = reservationInput(14, "a".repeat(64));
  await db.reservePublication(base);
  await assertRejectCode(
    () => db.reservePublication({ ...base, sha256: "b".repeat(64) }),
    "HUB_MANIFEST_VERSION_CONFLICT",
  );
  await assertRejectCode(
    () => db.reservePublication(reservationInput(13, "c".repeat(64))),
    "HUB_MANIFEST_VERSION_REGRESSION",
  );
});

Deno.test("out-of-order commits cannot regress the authoritative pointer", async () => {
  const db = new FakeDatabase();
  const r14 = await db.reservePublication(reservationInput(14, "a".repeat(64)));
  const r15 = await db.reservePublication(reservationInput(15, "b".repeat(64)));
  await db.markObjectStored(r14.publication_id);
  await db.markObjectStored(r15.publication_id);
  const newest = await db.commitPublication(commitInput(r15.publication_id));
  const older = await db.commitPublication(commitInput(r14.publication_id));
  assertEquals(newest.became_current, true);
  assertEquals(older.state, "superseded");
  assertEquals(db.pointer?.manifest_version, 15);
  assertEquals(db.generation, 1);
});

Deno.test("storage interruption and DB commit interruption recover idempotently", async () => {
  const manifest = await loadManifest();
  const db = new FakeDatabase();
  const storage = new FakeStorage();
  storage.failCreateOnce = true;
  const deps = { db, storage, keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }) };
  assertEquals((await handlePublish(postJson(manifest), deps)).status, 503);
  assertEquals((await handlePublish(postJson(manifest), deps)).status, 200);
  assertEquals(db.pointer?.manifest_version, 14);

  const db2 = new FakeDatabase();
  const storage2 = new FakeStorage();
  db2.failCommitOnce = true;
  const deps2 = { db: db2, storage: storage2, keyEnv: deps.keyEnv };
  assertEquals((await handlePublish(postJson(manifest), deps2)).status, 503);
  assertEquals((await handlePublish(postJson(manifest), deps2)).status, 200);
  assertEquals(db2.pointer?.manifest_version, 14);
});

Deno.test("existing immutable object with wrong hash fails closed", async () => {
  const manifest = await loadManifest();
  const storage = new FakeStorage();
  storage.objects.set("v14.json", new TextEncoder().encode("wrong"));
  const response = await handlePublish(postJson(manifest), {
    db: new FakeDatabase(),
    storage,
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });
  assertEquals(response.status, 409);
  assertEquals((await response.json()).error, "HUB_OBJECT_HASH_MISMATCH");
});

Deno.test("legacy current failure does not roll back authority and retry repairs it", async () => {
  const manifest = await loadManifest();
  const db = new FakeDatabase();
  const storage = new FakeStorage();
  storage.failMutableUploadOnce = true;
  const deps = { db, storage, keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }) };
  const first = await handlePublish(postJson(manifest), deps);
  assertEquals(first.status, 201);
  assertEquals((await first.json()).legacy_current_synced, false);
  assertEquals(db.pointer?.manifest_version, 14);
  const retry = await handlePublish(postJson(manifest), deps);
  assertEquals(retry.status, 200);
  assertEquals((await retry.json()).legacy_current_synced, true);
  assert(storage.objects.has("current.json"));
});

Deno.test("publication database outage fails closed before storage write", async () => {
  const manifest = await loadManifest();
  const db = new FakeDatabase();
  const storage = new FakeStorage();
  db.failReserveOnce = true;
  const response = await handlePublish(postJson(manifest), {
    db,
    storage,
    keyEnv: keyEnv({ manifestPubkeyA: TEST_PUBLIC_KEY }),
  });
  assertEquals(response.status, 503);
  assertEquals(storage.objects.size, 0);
});

Deno.test("manifest_current resolves pointer, ETag and last-good fallback", async () => {
  const db = new FakeDatabase();
  const storage = new FakeStorage();
  const good = new TextEncoder().encode('{"version":14}');
  const corrupt = new TextEncoder().encode('{"version":15,"corrupt":true}');
  const goodSha = await manifestSha(good);
  db.pointer = { manifest_version: 15, storage_path: "v15.json", sha256: "f".repeat(64) };
  db.candidates = [
    db.pointer,
    { manifest_version: 14, storage_path: "v14.json", sha256: goodSha },
  ];
  storage.objects.set("v15.json", corrupt);
  storage.objects.set("v14.json", good);
  const deps = { db, storage, channel: "staging" as const };
  const response = await handleManifestCurrent(new Request("http://localhost"), deps);
  assertEquals(response.status, 200);
  assertEquals(response.headers.get("x-manifest-version"), "14");
  assertEquals(response.headers.get("x-manifest-fallback"), "true");
  assertEquals(await response.text(), '{"version":14}');
  const cached = await handleManifestCurrent(
    new Request("http://localhost", { headers: { "if-none-match": `"${goodSha}"` } }),
    deps,
  );
  assertEquals(cached.status, 304);
});

Deno.test("durable mirror verifies source and destination hashes", async () => {
  const body = new TextEncoder().encode('{"ok":true}');
  const sha = await manifestSha(body);
  const db = new FakeDatabase();
  db.mirrorJobs.set("staging:14", {
    publication_id: "staging:14",
    manifest_version: 14,
    storage_path: "v14.json",
    expected_sha256: sha,
    attempts: 0,
    status: "pending",
  });
  const storage = new FakeStorage();
  storage.objects.set("v14.json", body);
  const target = new FakeMirrorTarget();
  target.tamperRead = true;
  const failed = await handleMirror(postJson({ publication_id: "staging:14" }), {
    db,
    storage,
    target,
  });
  assertEquals(failed.status, 503);
  assertEquals(db.mirrorJobs.get("staging:14")?.last_error, "HUB_MIRROR_TARGET_HASH_MISMATCH");
  target.tamperRead = false;
  const recovered = await handleMirror(postJson({ publication_id: "staging:14" }), {
    db,
    storage,
    target,
  });
  assertEquals(recovered.status, 200);
  assertEquals(db.mirrorJobs.get("staging:14")?.status, "done");
  assertEquals(await target.get("v14.json"), body);
});

Deno.test("mirror reports database claim failure and empty queue", async () => {
  const db = new FakeDatabase();
  db.failClaimOnce = true;
  const deps = { db, storage: new FakeStorage(), target: new FakeMirrorTarget() };
  assertEquals((await handleMirror(postJson({}), deps)).status, 503);
  assertEquals((await handleMirror(postJson({}), deps)).status, 204);
});

Deno.test("publish rejects invalid schema before any write", async () => {
  const manifest = await loadManifest();
  delete manifest.strategies;
  const result = await publishPayload(manifest);
  assertEquals(result.response.status, 422);
  assertEquals(result.storage.objects.size, 0);
});

Deno.test("Python signed fixture verifies in Deno using the same canonical bytes", async () => {
  const manifest = await loadManifest();
  const result = await publishPayload(manifest);
  assertEquals(result.response.status, 201);
  assert(canonicalBytes(manifest).length > 0);
});

Deno.test("outcomes reject invalid timestamps and enforce rate limits", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS);
  const future = await handleOutcomes(
    postJson(
      { outcomes: [{ strategy_key: "f1", ts: NOW_TS + 1, won: true, payout_pct: "0.87" }] },
      token,
    ),
    { db: new FakeDatabase(), jwtSecret: JWT_SECRET, hubEnv: "staging", nowTs: () => NOW_TS },
  );
  assertEquals(future.status, 422);
  const unaligned = await handleOutcomes(
    postJson({
      outcomes: [{ strategy_key: "f1", ts: VALID_OUTCOME_TS + 1, won: true, payout_pct: "0.87" }],
    }, token),
    { db: new FakeDatabase(), jwtSecret: JWT_SECRET, hubEnv: "staging", nowTs: () => NOW_TS },
  );
  assertEquals(unaligned.status, 422);
  const db = new FakeDatabase();
  db.outcomeBudgetResult = "OUTCOME_RATE_LIMITED";
  const limited = await handleOutcomes(
    postJson({
      outcomes: [{ strategy_key: "f1", ts: VALID_OUTCOME_TS, won: true, payout_pct: "0.87" }],
    }, token),
    { db, jwtSecret: JWT_SECRET, hubEnv: "staging", nowTs: () => NOW_TS },
  );
  assertEquals(limited.status, 429);
});

Deno.test("outcomes inject client_id from JWT and never trust body client_id", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS);
  const db = new FakeDatabase();
  const response = await handleOutcomes(
    postJson({
      outcomes: [{
        client_id: "11111111-1111-4111-8111-111111111111",
        strategy_key: "f1",
        ts: VALID_OUTCOME_TS,
        won: false,
        payout_pct: "0.00",
      }],
    }, token),
    { db, jwtSecret: JWT_SECRET, hubEnv: "staging", nowTs: () => NOW_TS },
  );
  assertEquals(response.status, 202);
  assertEquals(db.insertedOutcomes[0].client_id, CLIENT_ID);
});

Deno.test("client_token issues a one year anonymous token for a bot UUID", async () => {
  const db = new FakeDatabase();
  const response = await handleClientToken(postJson({ client_id: CLIENT_ID }), {
    db,
    jwtSecret: JWT_SECRET,
    hubEnv: "staging",
    nowTs: () => NOW_TS,
  });
  const payload = await response.json();
  assertEquals(response.status, 201);
  assertEquals(typeof payload.token, "string");
  assertEquals(payload.expires_in, 365 * 86400);
  assertEquals(db.rateLimitCalls, ["client_token_staging"]);
});

Deno.test("outcomes v2 is exactly-once and groups dependent client reports", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS, "staging");
  const db = new FakeDatabase();
  const item = outcomeV2();
  const deps = { db, jwtSecret: JWT_SECRET, hubEnv: "staging" as const, nowTs: () => NOW_TS };
  const first = await handleOutcomes(
    postJson({ schema_version: 2, outcomes: [item] }, token),
    deps,
  );
  const retry = await handleOutcomes(
    postJson({ schema_version: 2, outcomes: [item] }, token),
    deps,
  );
  const sameSignalNewEvent = await handleOutcomes(
    postJson({
      schema_version: 2,
      outcomes: [{ ...item, event_id: "018f81d6-25d4-4f3f-8e1d-294f5bcdef03" }],
    }, token),
    deps,
  );
  assertEquals(first.status, 202);
  assertEquals(await first.json(), { schema_version: 2, received: 1, inserted: 1, duplicates: 0 });
  assertEquals(await retry.json(), { schema_version: 2, received: 1, inserted: 0, duplicates: 1 });
  assertEquals(await sameSignalNewEvent.json(), {
    schema_version: 2,
    received: 1,
    inserted: 0,
    duplicates: 1,
  });
  assertEquals(db.insertedOutcomeEvents.size, 1);
});

Deno.test("outcomes v2 rejects PII, invalid signature, and cross-environment token", async () => {
  const stagingToken = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS, "staging");
  const deps = {
    db: new FakeDatabase(),
    jwtSecret: JWT_SECRET,
    hubEnv: "staging" as const,
    nowTs: () => NOW_TS,
  };
  const withPii = await handleOutcomes(
    postJson(
      { schema_version: 2, outcomes: [{ ...outcomeV2(), account_id: "forbidden" }] },
      stagingToken,
    ),
    deps,
  );
  assertEquals(withPii.status, 422);
  const invalidSignature = `${stagingToken.slice(0, -1)}x`;
  assertEquals(
    (await handleOutcomes(
      postJson({ schema_version: 2, outcomes: [outcomeV2()] }, invalidSignature),
      deps,
    )).status,
    401,
  );
  assertEquals(
    (await handleOutcomes(
      postJson({ schema_version: 2, outcomes: [outcomeV2()] }, stagingToken),
      { ...deps, hubEnv: "production" as const },
    )).status,
    401,
  );
});

Deno.test("global quotas survive rotating client UUIDs", async () => {
  const db = new FakeDatabase();
  const first = await handleClientToken(postJson({ client_id: CLIENT_ID }), {
    db,
    jwtSecret: JWT_SECRET,
    hubEnv: "staging",
    nowTs: () => NOW_TS,
  });
  assertEquals(first.status, 201);
  db.deniedRateBuckets.add("client_token_staging");
  const rotated = await handleClientToken(
    postJson({ client_id: "018f81d6-25d4-4f3f-8e1d-294f5bcdef02" }),
    { db, jwtSecret: JWT_SECRET, hubEnv: "staging", nowTs: () => NOW_TS },
  );
  assertEquals(rotated.status, 429);

  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS, "staging");
  db.outcomeBudgetResult = "OUTCOME_GLOBAL_RATE_LIMITED";
  const outcome = await handleOutcomes(
    postJson({ schema_version: 2, outcomes: [outcomeV2()] }, token),
    { db, jwtSecret: JWT_SECRET, hubEnv: "staging", nowTs: () => NOW_TS },
  );
  assertEquals(outcome.status, 429);
  assertEquals(db.insertedOutcomeEvents.size, 0);
});

Deno.test("outcomes rejects empty and oversized payloads before database writes", async () => {
  const token = await signAnonJwt(CLIENT_ID, JWT_SECRET, NOW_TS, "staging");
  const db = new FakeDatabase();
  const deps = { db, jwtSecret: JWT_SECRET, hubEnv: "staging" as const, nowTs: () => NOW_TS };
  assertEquals((await handleOutcomes(postJson([], token), deps)).status, 422);
  const oversized = new Request("http://localhost", {
    method: "POST",
    headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: JSON.stringify({ outcomes: [], padding: "x".repeat(256 * 1024) }),
  });
  assertEquals((await handleOutcomes(oversized, deps)).status, 413);
  assertEquals(db.rateLimitCalls.length, 0);
});

Deno.test("CAT-15 migration removes direct anon insert and cannot promote strategies", async () => {
  const migration = await Deno.readTextFile(
    new URL("apps/hub/supabase/migrations/0010_outcomes_v2.sql", ROOT),
  );
  assert(migration.includes("revoke insert on public.live_outcomes from anon"));
  assert(migration.includes("revoke all on public.live_outcome_events_v2 from anon"));
  assert(migration.includes("on conflict do nothing"));
  assert(migration.includes("live_outcome_signal_aggregates"));
  assertEquals(/update\s+public\.(manifests|research_attempts)/i.test(migration), false);
});

async function loadManifest(): Promise<Record<string, unknown>> {
  return JSON.parse(await Deno.readTextFile(MANIFEST_URL)) as Record<string, unknown>;
}

async function publishFixture(
  keys: Partial<ManifestKeyEnv>,
  env = "staging",
): Promise<{ response: Response; db: FakeDatabase; storage: FakeStorage }> {
  return await publishPayload(await loadManifest(), keys, env);
}

async function publishPayload(
  manifest: Record<string, unknown>,
  keys: Partial<ManifestKeyEnv> = { manifestPubkeyA: TEST_PUBLIC_KEY },
  env = "staging",
): Promise<{ response: Response; db: FakeDatabase; storage: FakeStorage }> {
  const db = new FakeDatabase();
  const storage = new FakeStorage();
  const response = await handlePublish(postJson(manifest), {
    db,
    storage,
    keyEnv: { hubEnv: env, ...keys },
  });
  return { response, db, storage };
}

function reservationInput(version: number, sha256: string): PublicationReservationInput {
  return {
    channel: "staging",
    manifestVersion: version,
    sha256,
    storagePath: `v${version}.json`,
    researchRunId: "fixture-run",
    datasetFingerprint: null,
    datasetKind: null,
    keyId: "A",
  };
}

function commitInput(publicationId: string): PublicationCommitInput {
  return {
    publicationId,
    publishedAt: NOW_TS,
    expiresAt: NOW_TS + 86400,
    signature: "test-signature",
    primitivesVersion: "1.0.0",
  };
}

async function assertRejectCode(action: () => Promise<unknown>, code: string): Promise<void> {
  try {
    await action();
    throw new Error("EXPECTED_REJECTION");
  } catch (error) {
    assert(error instanceof HubOperationError);
    assertEquals(error.code, code);
  }
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

function outcomeV2(): Record<string, unknown> {
  return {
    client_id: "11111111-1111-4111-8111-111111111111",
    event_id: "018f81d6-25d4-4f3f-8e1d-294f5bcdef02",
    strategy_key: "f1_reversal:EURUSD-OTC:M1:00-06",
    recipe_revision: 2,
    manifest_version: 14,
    execution_semantics_version: "tl.candle-close.v2",
    primitives_version: "1.0.0",
    asset: "EURUSD-OTC",
    timeframe_s: 60,
    product: "turbo",
    account_environment: "practice",
    source: "desktop_bot",
    signal_group_id: `sha256:${"a".repeat(64)}`,
    ts: VALID_OUTCOME_TS,
    won: true,
    payout_pct: "87.00",
  };
}

function keyEnv(values: Partial<ManifestKeyEnv>): ManifestKeyEnv {
  return { hubEnv: "staging", ...values };
}
