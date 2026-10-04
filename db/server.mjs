// Local embedded PostgreSQL for MFDataIndia.
//
// PGlite is real PostgreSQL (18) compiled to WASM, running in-process — no
// container or system install needed. Data persists under data/pglite/.
//
// This is the *local* engine. For a durable/production deployment, run real
// PostgreSQL via `docker compose up` and point MFDATAINDIA_DSN at it instead.
//
//   cd db && npm install && node server.mjs
//
// Env: MF_PG_PORT (default 5433), MF_PG_DATA (default ../data/pglite)

import { PGlite } from "@electric-sql/pglite";
import { PGLiteSocketServer } from "@electric-sql/pglite-socket";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const dataDir = process.env.MF_PG_DATA || join(root, "data", "pglite");
const port = Number(process.env.MF_PG_PORT || 5433);
const migrations = [
  "001_core_schema.sql",
  "002_nav_and_views.sql",
  "003_enrichment_recon.sql",
  "004_groww_enrichment.sql",
  "005_groww_deep_enrichment.sql",
];

const db = await PGlite.create({ dataDir });

// Migrations are idempotent (IF NOT EXISTS), so re-applying on every boot is safe.
for (const f of migrations) {
  await db.exec(readFileSync(join(root, "sql", f), "utf8"));
  console.log(`[mfdataindia-db] applied ${f}`);
}

const server = new PGLiteSocketServer({
  db,
  host: "127.0.0.1",
  port,
  // PGlite is single-writer under the hood; the socket bridge serialises queries
  // across connections. The default of 1 would reject the API's connection pool,
  // so allow a few concurrent client connections.
  maxConnections: 16,
  // Reap only truly-abandoned connections. Too short and it would drop the API's
  // legitimately-held connection between requests; 60s is long enough for active
  // use and short enough to recover slots from a client that died without closing.
  idleTimeout: 60_000,
});

// A client that disconnects mid-query (ECONNRESET / EPIPE) must not take the
// server down. These are routine in a web app (browser tab closed, request
// cancelled) and should be logged, not fatal.
process.on("uncaughtException", (err) => {
  if (err && (err.code === "ECONNRESET" || err.code === "EPIPE" || err.code === "ECONNABORTED")) {
    console.error(`[mfdataindia-db] client connection dropped (ignored): ${err.code}`);
    return;
  }
  console.error("[mfdataindia-db] fatal:", err);
  process.exit(1);
});
process.on("unhandledRejection", (reason) => {
  console.error("[mfdataindia-db] unhandled rejection (ignored):", reason);
});

await server.start();
console.log(`[mfdataindia-db] listening on 127.0.0.1:${port} (data: ${dataDir})`);
console.log(`[mfdataindia-db] DSN: host=127.0.0.1 port=${port} user=postgres dbname=postgres sslmode=disable`);

process.on("SIGINT", async () => { await server.stop(); await db.close(); process.exit(0); });
process.on("SIGTERM", async () => { await server.stop(); await db.close(); process.exit(0); });
