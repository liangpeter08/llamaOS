-- Read-only: what each portfolio sleeve still thinks it holds, and the price the fix would book.
-- Per-account books live in schema acct_<id>; use the active account's, as the services do.
SELECT set_config('search_path', COALESCE((SELECT v::json #>> '{}' FROM public.kv WHERE k = 'broker.account_schema'), 'public') || ', public', false) AS search_path;
WITH net AS (
  SELECT strategy_hash, sid, symbol,
         SUM(CASE WHEN side='buy' THEN filled_qty ELSE -filled_qty END) AS qty
  FROM portfolio_orders WHERE filled_qty > 0 AND avg_price IS NOT NULL
  GROUP BY strategy_hash, sid, symbol)
SELECT n.sid, left(n.strategy_hash, 10) AS hash, n.symbol, n.qty,
       (SELECT c FROM daily d WHERE d.symbol = n.symbol ORDER BY day DESC LIMIT 1) AS book_price,
       (SELECT day FROM daily d WHERE d.symbol = n.symbol ORDER BY day DESC LIMIT 1) AS price_day,
       (SELECT avg_price FROM portfolio_orders o WHERE o.symbol = n.symbol AND o.filled_qty > 0 AND o.avg_price IS NOT NULL ORDER BY id DESC LIMIT 1) AS last_fill
FROM net n WHERE n.qty > 1e-6 ORDER BY n.sid, n.symbol;
