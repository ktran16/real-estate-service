-- Shares are ratios of counts, so they must stay within [0, 1], and the broker subset can
-- never exceed the active total.
SELECT district, broker_share, top_account_share, broker_listings, active_listings
FROM {{ ref('broker_concentration') }}
WHERE broker_share < 0 OR broker_share > 1
   OR top_account_share < 0 OR top_account_share > 1
   OR broker_listings > active_listings
