-- Each listing should appear once per source (stg_listings dedups on listing_id+source).
-- Fails if any (listing_id, source) pair has more than one row in the listings mart.
SELECT
    listing_id,
    source,
    count(*) AS n
FROM {{ ref('listings') }}
GROUP BY listing_id, source
HAVING count(*) > 1
