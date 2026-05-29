WITH deduplicated AS (
    SELECT
        *,
        ROW_NUMBER() OVER (PARTITION BY listing_id, source ORDER BY scraped_at DESC) as rn
    FROM {{ source('raw', 'raw_listings') }}
)
SELECT
    listing_id,
    source,
    url,
    title,
    description,
    category,
    property_type,
    transaction_type,
    price,
    price_per_sqm,
    area_sqm,
    bedrooms,
    bathrooms,
    num_floors,
    direction,
    direction_code,
    legal_status,
    legal_status_code,
    furniture,
    address_raw,
    ward_code,
    district,
    ward,
    lat,
    lng,
    account_id,
    account_name,
    phone,
    is_broker,
    images,
    posted_at,
    scraped_at,
    is_active
FROM deduplicated
WHERE rn = 1
