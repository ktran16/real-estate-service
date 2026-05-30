-- Every row in `deals` must be a genuine discount: discount_pct in (0, 1], price-per-sqm
-- strictly below the benchmark, and enough comparables to trust the benchmark.
SELECT listing_id, discount_pct, price_per_sqm, benchmark_price_per_sqm, comparable_count
FROM {{ ref('deals') }}
WHERE discount_pct <= 0
   OR discount_pct > 1
   OR price_per_sqm >= benchmark_price_per_sqm
   OR comparable_count < 3
