// Automation Lab mock API
//
// json-server 0.17 serves the collections in db.json with the usual query syntax
// (`?_page=1&_limit=20`, `?_sort=id&_order=asc`, `?id_gte=120`, `?q=...`).
// middleware.js adds endpoints that behave like real-world APIs misbehave:
// flaky 503s, 429 rate limits, slow responses, an RSS feed, a paginated HTML catalog,
// jittered FX rates and a webhook sink. Everything is deterministic enough to demo against.
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const jsonServer = require('json-server');
const custom = require('./middleware');

const PORT = Number(process.env.PORT || 8080);

// Writable dataset, deterministic across restarts.
//
// `db.json` is baked into the image as a read-only seed (root-owned; the container runs as `node`).
// json-server runs with readOnly:false, so POST/PUT/PATCH/DELETE make lowdb rewrite the whole
// database file - which failed with EACCES against /app/db.json. Instead of granting the runtime
// user write access to the seed, copy the seed to a writable path on every start and serve that.
//
// Trade-off: mutations only live as long as the process, so `docker compose restart mock-api`
// (or a recreate) resets the dataset back to the seed. That is deliberate: workflow READMEs
// document exact responses (row counts, cursors, prices), and a db.json that accumulated writes
// across restarts would make those demos non-reproducible. The simpler `COPY --chown=node:node
// db.json` was rejected for exactly that reason - it would persist mutations in the container
// layer and silently drift from the documented dataset.
const SEED_DB = path.join(__dirname, 'db.json');
const RUNTIME_DB = process.env.MOCK_API_DB || path.join(os.tmpdir(), 'mock-api-db.json');

fs.mkdirSync(path.dirname(RUNTIME_DB), { recursive: true });
fs.copyFileSync(SEED_DB, RUNTIME_DB);

const server = jsonServer.create();
const router = jsonServer.router(RUNTIME_DB);

server.use(jsonServer.defaults({ logger: false, noCors: false, readOnly: false }));
server.use(jsonServer.bodyParser);
server.use(custom(router.db));

// /api/* is an alias so workflows can show a "versioned base URL" without changing anything else.
server.use(jsonServer.rewriter({ '/api/*': '/$1' }));
server.use(router);

// IPv4 wildcard only - the compose healthcheck must therefore probe 127.0.0.1, not localhost
// (BusyBox wget in node:20-alpine tries ::1 first and does not fall back).
server.listen(PORT, '0.0.0.0', () => {
  console.log(`mock-api listening on :${PORT} (dataset ${RUNTIME_DB}, reset from the seed on every start)`);
});
