SELECT
    account_id,
    account_name,
    phone,
    COUNT(*) as active_listing_count,
    ROUND(AVG(price)) as avg_listing_price
FROM {{ ref('listings') }}
WHERE is_active = TRUE AND account_id IS NOT NULL
GROUP BY 1, 2, 3
HAVING COUNT(*) >= 5
