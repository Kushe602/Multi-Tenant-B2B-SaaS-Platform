-- Database-per-service: each microservice owns an isolated logical database.
-- Executed once by the postgres image on first initialization
-- (mounted into /docker-entrypoint-initdb.d/). The default POSTGRES_DB
-- ("tenantforge") is created by the image itself; these are the extras.
CREATE DATABASE identity;
CREATE DATABASE tickets;
CREATE DATABASE notifications;
