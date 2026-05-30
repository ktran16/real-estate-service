-- Price-per-sqm distribution per ward (finer grain than price_by_district). Percentiles are
-- more robust to outliers than the mean and let dashboards show the spread, not just a point
-- estimate. Sparse wards (few listings) still produce a row; treat low listing_count with care.
SELECT
    district,
    ward,
    transaction_type,
    property_type,
    COUNT(*) AS listing_count,
    ROUND(MIN(price_per_sqm), 2) AS min_price_per_sqm,
    ROUND(QUANTILE_CONT(price_per_sqm, 0.25), 2) AS p25_price_per_sqm,
    ROUND(MEDIAN(price_per_sqm), 2) AS median_price_per_sqm,
    ROUND(QUANTILE_CONT(price_per_sqm, 0.75), 2) AS p75_price_per_sqm,
    ROUND(QUANTILE_CONT(price_per_sqm, 0.90), 2) AS p90_price_per_sqm,
    ROUND(MAX(price_per_sqm), 2) AS max_price_per_sqm
FROM {{ ref('int_listings_geocoded') }}
WHERE ward IS NOT NULL AND price_per_sqm IS NOT NULL
GROUP BY 1, 2, 3, 4
