-- Broker concentration per district: how much of the active supply is broker-listed and how
-- dominant the single largest account is. Successive daily snapshots of this mart give the
-- concentration *trend* in Metabase.
--   broker_share      = share of active listings flagged is_broker.
--   top_account_share = largest single account's listings / active listings (a simple
--                       concentration proxy; → 1.0 means one account dominates the district).
WITH active AS (
    SELECT
        district,
        account_id,
        COALESCE(is_broker, FALSE) AS is_broker
    FROM {{ ref('int_listings_geocoded') }}
    WHERE is_active = TRUE AND district IS NOT NULL
),
per_account AS (
    SELECT district, account_id, COUNT(*) AS account_listings
    FROM active
    WHERE account_id IS NOT NULL
    GROUP BY 1, 2
)
SELECT
    a.district,
    COUNT(*) AS active_listings,
    SUM(CASE WHEN a.is_broker THEN 1 ELSE 0 END) AS broker_listings,
    ROUND(SUM(CASE WHEN a.is_broker THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 4) AS broker_share,
    COUNT(DISTINCT a.account_id) AS distinct_accounts,
    MAX(pa.account_listings) AS top_account_listings,
    ROUND(MAX(pa.account_listings) * 1.0 / COUNT(*), 4) AS top_account_share
FROM active a
LEFT JOIN per_account pa
    ON a.district = pa.district AND a.account_id = pa.account_id
GROUP BY 1
