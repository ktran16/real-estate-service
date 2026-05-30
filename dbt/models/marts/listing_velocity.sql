-- New-vs-removed listing velocity per district per week — a market-supply pulse.
--   new      = listings whose `posted_at` falls in the week.
--   removed  = listings marked inactive, bucketed by `scraped_at` (≈ when the rescraper found
--              them gone). Removal time is approximate (rescrape cadence), so read weekly trends
--              rather than exact daily counts.
WITH new_listings AS (
    SELECT
        DATE_TRUNC('week', posted_at) AS week_start,
        district,
        COUNT(*) AS new_listings
    FROM {{ ref('int_listings_geocoded') }}
    WHERE posted_at IS NOT NULL AND district IS NOT NULL
    GROUP BY 1, 2
),
removed_listings AS (
    SELECT
        DATE_TRUNC('week', scraped_at) AS week_start,
        district,
        COUNT(*) AS removed_listings
    FROM {{ ref('int_listings_geocoded') }}
    WHERE is_active = FALSE AND scraped_at IS NOT NULL AND district IS NOT NULL
    GROUP BY 1, 2
)
SELECT
    COALESCE(n.week_start, r.week_start) AS week_start,
    COALESCE(n.district, r.district) AS district,
    COALESCE(n.new_listings, 0) AS new_listings,
    COALESCE(r.removed_listings, 0) AS removed_listings,
    COALESCE(n.new_listings, 0) - COALESCE(r.removed_listings, 0) AS net_change
FROM new_listings n
FULL OUTER JOIN removed_listings r
    ON n.week_start = r.week_start AND n.district = r.district
