-- Record positions closed by hand in Alpaca as filled sleeve sells, so replayed holdings match
-- the broker again and reconcile clears. Books each lot at the latest daily close (or the last fill price when
-- no daily bar is stored; P&L is approximate).
-- Per-account books live in schema acct_<id>; use the active account's, as the services do.
SELECT set_config('search_path', COALESCE((SELECT v::json #>> '{}' FROM public.kv WHERE k = 'broker.account_schema'), 'public') || ', public', false) AS search_path;
BEGIN;
WITH net AS (
  SELECT strategy_hash, sid, symbol,
         SUM(CASE WHEN side='buy' THEN filled_qty ELSE -filled_qty END) AS qty
  FROM portfolio_orders WHERE filled_qty > 0 AND avg_price IS NOT NULL
  GROUP BY strategy_hash, sid, symbol)
INSERT INTO portfolio_orders(strategy_hash, sid, day, ts, symbol, side, qty, ref_price, reason, status, mode,
                             client_id, filled_qty, avg_price, created_wall, decided_by, decided_wall, note)
SELECT n.strategy_hash, n.sid, to_char(now() AT TIME ZONE 'America/New_York', 'YYYY-MM-DD'), extract(epoch FROM now()),
       n.symbol, 'sell', round(n.qty)::int, p.c, 'manual close in Alpaca', 'filled', 'paper',
       'manual-close-' || left(n.strategy_hash, 10) || '-' || n.symbol || '-' || floor(extract(epoch FROM now()))::bigint,
       n.qty, p.c, extract(epoch FROM now()), 'owner', extract(epoch FROM now()),
       'Reconciliation adjustment: position was closed outside llamaOS; booked at latest daily close (or last fill price).'
FROM net n
CROSS JOIN LATERAL (SELECT COALESCE(
  (SELECT c FROM daily d WHERE d.symbol = n.symbol ORDER BY day DESC LIMIT 1),
  (SELECT avg_price FROM portfolio_orders o WHERE o.symbol = n.symbol AND o.filled_qty > 0 AND o.avg_price IS NOT NULL ORDER BY id DESC LIMIT 1)) AS c) p
WHERE n.qty > 1e-6 AND p.c IS NOT NULL
RETURNING sid, symbol, qty, avg_price;
COMMIT;
