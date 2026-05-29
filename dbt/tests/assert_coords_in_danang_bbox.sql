-- Geocoded coordinates should fall within a generous Da Nang bounding box.
-- Catches bad geocodes (e.g. wrong country / swapped lat/lng). Fails on outliers.
SELECT listing_id, source, lat, lng
FROM {{ ref('listings') }}
WHERE lat IS NOT NULL
  AND lng IS NOT NULL
  AND (
        lat NOT BETWEEN 15.7 AND 16.4
     OR lng NOT BETWEEN 107.8 AND 108.6
  )
