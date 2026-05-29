SELECT
    district,
    property_type,
    transaction_type,
    COUNT(*) as listing_count,
    ROUND(AVG(price)) as avg_price,
    ROUND(MEDIAN(price)) as median_price,
    ROUND(AVG(price_per_sqm), 2) as avg_price_per_sqm,
    ROUND(MEDIAN(price_per_sqm), 2) as median_price_per_sqm
FROM {{ ref('int_listings_geocoded') }}
WHERE district IS NOT NULL AND price IS NOT NULL
GROUP BY 1, 2, 3
