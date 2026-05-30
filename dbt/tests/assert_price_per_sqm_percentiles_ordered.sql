-- Percentiles must be monotonically non-decreasing: min <= p25 <= median <= p75 <= p90 <= max.
-- Any row that violates this points to a bug in the aggregation.
SELECT *
FROM {{ ref('price_per_sqm_by_ward') }}
WHERE NOT (
    min_price_per_sqm <= p25_price_per_sqm
    AND p25_price_per_sqm <= median_price_per_sqm
    AND median_price_per_sqm <= p75_price_per_sqm
    AND p75_price_per_sqm <= p90_price_per_sqm
    AND p90_price_per_sqm <= max_price_per_sqm
)
