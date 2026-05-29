-- Prices and price-per-sqm should never be negative. Fails on any offending row.
SELECT listing_id, source, price, price_per_sqm
FROM {{ ref('listings') }}
WHERE price < 0
   OR price_per_sqm < 0
