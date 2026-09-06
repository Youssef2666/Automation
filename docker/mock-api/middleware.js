// Custom endpoints layered in front of json-server's router.
// Each one exists to make a specific workflow or pattern demonstrable offline:
//   /health, /health/down          M01 uptime monitor (one target that is always down)
//   /flaky                         P02 retry with backoff (about half the calls fail with 503)
//   /ratelimited                   P04 rate limiting (429 above 5 requests per 10 s window per client)
//   /slow?ms=3000                  timeouts
//   /feed.xml                      M03 RSS digest (built from the `posts` collection)
//   /catalog?page=N                D02 web scraping with pagination (HTML product cards)
//   /rates/latest?base=USD         M05 exchange-rate watcher (jittered per minute)
//   /companies/lookup?domain=...   B01 lead enrichment
//   /webhooks/sink                 generic notification target that echoes what it received
'use strict';

const HTML_PAGE_SIZE = 14;
const RATE_WINDOW_MS = 10_000;
const RATE_LIMIT = 5;
const rateBuckets = new Map(); // ip -> [timestamps]
let flakyCounter = 0;
const sink = [];

function escapeXml(s) {
  return String(s).replace(/[<>&'"]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c]));
}

function escapeHtml(s) {
  return escapeXml(s);
}

// Deterministic pseudo-random in [0,1) from a string seed (so rates only change per minute, not per call).
function seeded(seed) {
  let h = 2166136261;
  for (const ch of String(seed)) {
    h ^= ch.charCodeAt(0);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return (h % 10_000) / 10_000;
}

const BASE_RATES = { USD: 1, EUR: 0.92, GBP: 0.79, LYD: 4.85, EGP: 48.6, TRY: 34.1, AED: 3.67, SAR: 3.75 };

module.exports = function customMiddleware(db) {
  return function (req, res, next) {
    const url = new URL(req.url, 'http://mock-api');
    const p = url.pathname;

    if (p === '/health') return res.json({ status: 'ok', service: 'mock-api', time: new Date().toISOString() });
    if (p === '/health/down') return res.status(503).json({ status: 'down', reason: 'simulated outage' });

    if (p === '/flaky') {
      flakyCounter += 1;
      if (flakyCounter % 2 === 1) {
        res.set('Retry-After', '1');
        return res.status(503).json({ error: 'temporarily unavailable', attempt: flakyCounter });
      }
      return res.json({ ok: true, attempt: flakyCounter, message: 'succeeded after retry' });
    }

    if (p === '/ratelimited') {
      const key = req.ip || 'anon';
      const now = Date.now();
      const stamps = (rateBuckets.get(key) || []).filter((t) => now - t < RATE_WINDOW_MS);
      if (stamps.length >= RATE_LIMIT) {
        const retry = Math.ceil((RATE_WINDOW_MS - (now - stamps[0])) / 1000);
        res.set('Retry-After', String(retry));
        res.set('X-RateLimit-Limit', String(RATE_LIMIT));
        res.set('X-RateLimit-Remaining', '0');
        return res.status(429).json({ error: 'rate limit exceeded', retry_after_seconds: retry });
      }
      stamps.push(now);
      rateBuckets.set(key, stamps);
      res.set('X-RateLimit-Limit', String(RATE_LIMIT));
      res.set('X-RateLimit-Remaining', String(RATE_LIMIT - stamps.length));
      return res.json({ ok: true, remaining: RATE_LIMIT - stamps.length });
    }

    if (p === '/slow') {
      const ms = Math.min(Number(url.searchParams.get('ms') || 3000), 30_000);
      return setTimeout(() => res.json({ ok: true, delayed_ms: ms }), ms);
    }

    if (p === '/feed.xml') {
      const posts = db.get('posts').value() || [];
      const items = posts
        .slice()
        .sort((a, b) => (a.published_at < b.published_at ? 1 : -1))
        .map(
          (post) => `    <item>
      <title>${escapeXml(post.title)}</title>
      <link>http://mock-api:8080/posts/${post.id}</link>
      <guid isPermaLink="false">post-${post.id}</guid>
      <pubDate>${new Date(post.published_at).toUTCString()}</pubDate>
      <description>${escapeXml(post.excerpt || '')}</description>
      <category>${escapeXml((post.tags || []).join(', '))}</category>
    </item>`,
        )
        .join('\n');
      res.type('application/rss+xml');
      return res.send(`<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Automation Lab Blog (mock)</title>
    <link>http://mock-api:8080/posts</link>
    <description>Synthetic posts for RSS digest demos</description>
${items}
  </channel>
</rss>
`);
    }

    if (p === '/catalog') {
      const products = db.get('products').value() || [];
      const pages = Math.max(1, Math.ceil(products.length / HTML_PAGE_SIZE));
      const page = Math.min(Math.max(Number(url.searchParams.get('page') || 1), 1), pages);
      const slice = products.slice((page - 1) * HTML_PAGE_SIZE, page * HTML_PAGE_SIZE);
      const cards = slice
        .map(
          (pr) => `      <article class="product" data-sku="${escapeHtml(pr.sku)}">
        <h3 class="name">${escapeHtml(pr.name)}</h3>
        <span class="category">${escapeHtml(pr.category)}</span>
        <span class="price" data-currency="${escapeHtml(pr.currency)}">${Number(pr.price).toFixed(2)}</span>
        <span class="stock">${pr.stock > 0 ? `${pr.stock} in stock` : 'out of stock'}</span>
      </article>`,
        )
        .join('\n');
      const nextLink = page < pages ? `<a rel="next" href="/catalog?page=${page + 1}">Next</a>` : '';
      res.type('html');
      return res.send(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Catalog page ${page}</title></head>
<body>
  <main id="catalog" data-page="${page}" data-pages="${pages}">
    <h1>Product catalog</h1>
${cards}
    <nav class="pagination">
      <span class="page">Page ${page} of ${pages}</span>
      ${nextLink}
    </nav>
  </main>
</body></html>
`);
    }

    if (p === '/rates/latest') {
      const base = (url.searchParams.get('base') || 'USD').toUpperCase();
      if (!BASE_RATES[base]) return res.status(400).json({ error: `unknown base ${base}` });
      const minute = new Date().toISOString().slice(0, 16);
      const rates = {};
      for (const [code, r] of Object.entries(BASE_RATES)) {
        if (code === base) continue;
        const jitter = (seeded(`${minute}:${code}`) - 0.5) * 0.06; // +/- 3 %
        rates[code] = Number(((r / BASE_RATES[base]) * (1 + jitter)).toFixed(6));
      }
      return res.json({ base, date: minute, rates, provider: 'mock-api' });
    }

    if (p === '/companies/lookup') {
      const domain = (url.searchParams.get('domain') || '').toLowerCase();
      const hit = (db.get('companies').value() || []).find((c) => c.domain === domain);
      if (!hit) return res.status(404).json({ error: 'not found', domain });
      return res.json(hit);
    }

    if (p === '/webhooks/sink') {
      if (req.method === 'POST') {
        const entry = { received_at: new Date().toISOString(), headers: req.headers, body: req.body };
        sink.unshift(entry);
        sink.splice(200);
        return res.status(202).json({ accepted: true, stored: sink.length });
      }
      return res.json(sink);
    }

    return next();
  };
};
