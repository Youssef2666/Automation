# R01 test inputs

R01 reads the seeded `orders` / `order_items` / `customers` tables. `orders.sql` lists candidate orders and shows
the exact query the workflow runs (`docker compose exec -T postgres psql -U n8n -d demo < workflows/R01-pdf-invoice/test/orders.sql`).
Default: `ORD-2026-0001` (2 items, USD 48.00) -> `data/out/invoice-ORD-2026-0001.pdf`.
