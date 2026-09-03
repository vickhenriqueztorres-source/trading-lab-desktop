// P05 / R-HUB-7: archive Edge Function stub.
// P06 will implement Storage export and call complete_archive_job() only after
// verifying the exported row count.

Deno.serve(() => {
  return new Response(
    JSON.stringify({ event: "strategy_lab_archive_stub", status: "not_implemented" }),
    {
      status: 501,
      headers: { "content-type": "application/json" },
    },
  );
});
