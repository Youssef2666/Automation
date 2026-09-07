# D04 test fixtures

- `sample-products.json` - two rows of `GET http://mock-api:8080/products?updated_at_gte=<cursor>&_sort=updated_at&_order=asc`
  (the shape **Split rows** emits), the canonical string **Canonical row** builds from each (`{sku, name, price, stock}`,
  fixed key order, `updated_at` excluded) and the SHA-256 that ends up in `products_mirror.content_hash`.
  Compare with `select id, sku, content_hash from products_mirror where id in (1, 2)` after a run.
- `reset-watermark.sh` - deletes the `mock-api-products` row from `sync_state`, truncates `products_mirror` and
  restarts mock-api (drops any PATCHed product). The next run is a full first sync again (40 inserted).
- `simulate-source-change.sh content|touch` - PATCHes product 20 in the mock API: `content` changes `stock`
  (next run: 1 updated), `touch` only bumps `updated_at` (next run: the row comes back but its hash is equal,
  so it is skipped and nothing is written). Product 20 is patched because it is the seed's newest row, the one
  sitting on the watermark: the boundary re-fetch and the changed row coincide, hence `fetched 1`.

The workflow reads the live mock API, so there is nothing to replay: `python scripts/dev/run-workflow.py D04`
twice in a row is the test (second run: `inserted 0, updated 0, skipped N`). To see the failure path,
`docker compose stop mock-api`, run once (P02 retries, then `Source fetch failed` and P01 logs it), then
`docker compose start mock-api`.
