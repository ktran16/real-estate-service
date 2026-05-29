SELECT
    DATE_TRUNC('week', posted_at) as week_start,
    district,
    property_type,
    transaction_type,
    COUNT(*) as listing_count,
    ROUND(MEDIAN(price)) as median_price,
    ROUND(MEDIAN(price_per_sqm), 2) as median_price_per_sqm
FROM {{ ref('int_listings_geocoded') }}
WHERE district IS NOT NULL AND posted_at IS NOT NULL
GROUP BY 1, 2, 3, 4
