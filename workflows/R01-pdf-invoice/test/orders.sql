-- Paid orders with their line-item count: pick any order_number for Config.order_number.
select o.order_number, o.status, o.total, c.name, c.email,
       (select count(*) from order_items i where i.order_id = o.id) as items
from orders o join customers c on c.id = o.customer_id
where o.status in ('paid', 'shipped')
order by o.id limit 10;

-- The exact query the workflow runs for ORD-2026-0001:
select o.order_number, o.status, o.total::float as total, o.currency, c.name as customer_name, c.email as customer_email,
       coalesce((select json_agg(json_build_object('sku', p.sku, 'name', p.name, 'qty', i.qty,
                 'unit_price', i.unit_price::float, 'amount', (i.qty * i.unit_price)::float) order by i.id)
                 from order_items i join products p on p.id = i.product_id where i.order_id = o.id), '[]'::json) as items
from orders o join customers c on c.id = o.customer_id where o.order_number = 'ORD-2026-0001';
