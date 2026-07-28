-- Keep only the initial factor and dates where the stored cumulative factor
-- changes. Query paths use an as-of lookup, so daily repetition is redundant.
WITH factor_history AS MATERIALIZED (
    SELECT stock_id,
           effective_date,
           adj_factor,
           LAG(adj_factor) OVER (
               PARTITION BY stock_id
               ORDER BY effective_date
           ) AS previous_factor
      FROM market.adjustment_factor
),
redundant AS (
    SELECT stock_id, effective_date
      FROM factor_history
     WHERE previous_factor IS NOT NULL
       AND adj_factor = previous_factor
)
DELETE FROM market.adjustment_factor f
USING redundant r
WHERE f.stock_id = r.stock_id
  AND f.effective_date = r.effective_date;

ANALYZE market.adjustment_factor;
