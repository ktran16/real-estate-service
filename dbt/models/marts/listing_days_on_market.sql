-- Days a listing has been (or was) on the market.
-- `scraped_at` is bumped every time the listing is seen and frozen when the rescraper marks it
-- inactive, so for active listings this is "days listed so far" and for inactive ones it's the
-- total time from posting to removal. Negative spans (bad source timestamps) are clamped to 0.
SELECT
    listing_id,
    source,
    district,
    ward,
    property_type,
    transaction_type,
    is_active,
    posted_at,
    scraped_at AS last_seen_at,
    GREATEST(DATE_DIFF('day', posted_at, scraped_at), 0) AS days_on_market,
    CASE
        WHEN GREATEST(DATE_DIFF('day', posted_at, scraped_at), 0) <= 7 THEN '0-7'
        WHEN GREATEST(DATE_DIFF('day', posted_at, scraped_at), 0) <= 30 THEN '8-30'
        WHEN GREATEST(DATE_DIFF('day', posted_at, scraped_at), 0) <= 90 THEN '31-90'
        ELSE '90+'
    END AS dom_bucket
FROM {{ ref('int_listings_geocoded') }}
WHERE posted_at IS NOT NULL AND scraped_at IS NOT NULL
