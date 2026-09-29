-- Ensures PostGIS is enabled on the application database.
-- The postgis/postgis image already enables this on first init, but this
-- script makes the dependency explicit and keeps it idempotent for any
-- alternate Postgres image/environment.
CREATE EXTENSION IF NOT EXISTS postgis;
