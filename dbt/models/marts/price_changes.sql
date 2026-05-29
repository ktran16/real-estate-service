SELECT
    h.listing_id,
    h.source,
    l.title,
    l.district,
    l.ward,
    l.property_type,
    l.transaction_type,
    h.price as current_price,
    h.previous_price,
    h.price_change,
    h.price_change_pct,
    h.observed_at
FROM {{ source('raw', 'listing_price_history') }} h
JOIN {{ ref('listings') }} l ON h.listing_id = l.listing_id AND h.source = l.source
WHERE h.price_change_pct IS NOT NULL
  AND ABS(h.price_change_pct) >= 0.05
