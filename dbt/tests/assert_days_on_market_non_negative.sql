-- Days on market is clamped to >= 0 in the model; this guards that invariant.
SELECT listing_id, days_on_market
FROM {{ ref('listing_days_on_market') }}
WHERE days_on_market < 0
