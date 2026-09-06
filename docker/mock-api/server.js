// Automation Lab mock API
//
// json-server 0.17 serves the collections in db.json with the usual query syntax
// (`?_page=1&_limit=20`, `?_sort=id&_order=asc`, `?id_gte=120`, `?q=...`).
// middleware.js adds endpoints that behave like real-world APIs misbehave:
// flaky 503s, 429 rate limits, slow responses, an RSS feed, a paginated HTML catalog,
// jittered FX rates and a webhook sink. Everything is deterministic enough to demo against.
'use strict';

const path = require('path');
const jsonServer = require('json-server');
const custom = require('./middleware');

const PORT = Number(process.env.PORT || 8080);
const server = jsonServer.create();
const router = jsonServer.router(path.join(__dirname, 'db.json'));

server.use(jsonServer.defaults({ logger: false, noCors: false, readOnly: false }));
server.use(jsonServer.bodyParser);
server.use(custom(router.db));

// /api/* is an alias so workflows can show a "versioned base URL" without changing anything else.
server.use(jsonServer.rewriter({ '/api/*': '/$1' }));
server.use(router);

server.listen(PORT, '0.0.0.0', () => {
  console.log(`mock-api listening on :${PORT}`);
});
