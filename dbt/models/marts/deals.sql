-- Under-market "deals": active listings priced materially below the typical price-per-sqm for
-- comparable properties (same district × property type × transaction type). The benchmark is the
-- district-level median from `price_by_district`, not the ward median, because wards are usually
-- too sparse for a stable benchmark; `deal_min_comparables` guards against thin comparison groups.
-- Thresholds are dbt vars so they can be tuned without editing SQL.
{% set threshold = var('deal_discount_threshold', 0.20) %}
{% set min_comparables = var('deal_min_comparables', 3) %}

WITH benchmarks AS (
    SELECT
        district,
        property_type,
        transaction_type,
        median_price_per_sqm AS benchmark_price_per_sqm,
        listing_count AS comparable_count
    FROM {{ ref('price_by_district') }}
)
SELECT
    l.listing_id,
    l.source,
    l.title,
    l.url,
    l.district,
    l.ward,
    l.property_type,
    l.transaction_type,
    l.price,
    l.area_sqm,
    l.price_per_sqm,
    b.benchmark_price_per_sqm,
    b.comparable_count,
    ROUND(
        (b.benchmark_price_per_sqm - l.price_per_sqm) / b.benchmark_price_per_sqm, 4
    ) AS discount_pct
FROM {{ ref('listings') }} l
JOIN benchmarks b
    ON l.district = b.district
   AND l.property_type = b.property_type
   AND l.transaction_type = b.transaction_type
WHERE l.is_active = TRUE
  AND l.price_per_sqm IS NOT NULL
  AND b.benchmark_price_per_sqm > 0
  AND b.comparable_count >= {{ min_comparables }}
  AND (b.benchmark_price_per_sqm - l.price_per_sqm) / b.benchmark_price_per_sqm >= {{ threshold }}
