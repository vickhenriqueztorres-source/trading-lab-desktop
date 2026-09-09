// CAT-17 / R-HUB-7: the Edge function controls jobs but never archives or deletes rows.
import { type ArchiveControlDatabase, handleArchive } from "../archive/index.ts";

function assertEquals(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`assertion failed: ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`);
  }
}

class FakeArchiveDatabase implements ArchiveControlDatabase {
  planned = 0;

  async plan(): Promise<string> {
    this.planned += 1;
    return await Promise.resolve("00000000-0000-4000-8000-000000000017");
  }

  async backlog(): Promise<
    { requested: number; claimed: number; verified: number; failed: number }
  > {
    return await Promise.resolve({ requested: 8, claimed: 0, verified: 0, failed: 0 });
  }
}

Deno.test("CAT-17 archive endpoint rejects missing control token", async () => {
  const db = new FakeArchiveDatabase();
  const response = await handleArchive(new Request("http://local/archive", { method: "POST" }), {
    db,
    controlToken: "secret",
    backlogAlertThreshold: 7,
  });
  assertEquals(response.status, 401);
  assertEquals(db.planned, 0);
});

Deno.test("CAT-17 archive endpoint plans and reports executor backlog without deleting", async () => {
  const db = new FakeArchiveDatabase();
  const response = await handleArchive(
    new Request("http://local/archive", {
      method: "POST",
      headers: { "x-archive-control-token": "secret" },
    }),
    { db, controlToken: "secret", backlogAlertThreshold: 7 },
  );
  const body = await response.json();

  assertEquals(response.status, 200);
  assertEquals(db.planned, 1);
  assertEquals(body.executor_required, true);
  assertEquals(body.alert, "ARCHIVE_EXECUTOR_BACKLOG");
  assertEquals(body.deletion_performed, false);
});
