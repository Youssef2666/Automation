# D03 test fixtures

- `sample-products.json` - two rows of the json-server `/products` shape (`curl 'localhost:8080/products?_page=1&_limit=2'`)
  and what `normalize()` turns them into.
- `sample-catalog-page.json` - page 1 of the HTML catalog as returned by `curl 'localhost:8080/catalog?page=1'`
  (14 `article.product` cards, `data-pages="3"`), plus the selectors the HTML node uses.

The workflow reads the live mock API and the seeded `products` table, so there is nothing to replay: run
`python scripts/dev/run-workflow.py D03` and compare `data/out/catalog-normalized.csv` with these shapes.
To simulate a broken source, `docker compose stop mock-api` and run once - P02 retries five times, then the
workflow stops with `Products source failed` and P01 logs it. `docker compose start mock-api` afterwards.
