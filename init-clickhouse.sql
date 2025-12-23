-- ClickHouse Initialization Script for EchoDB
-- Creates the _peerdb database for PeerDB internal metadata tables

-- Create database for PeerDB internal tables
-- PeerDB uses this to store metadata, checkpoints, and raw staging data
CREATE DATABASE IF NOT EXISTS _peerdb;

-- Create the default database for actual synced data
CREATE DATABASE IF NOT EXISTS default;

-- Grant permissions
GRANT ALL ON _peerdb.* TO default;
GRANT ALL ON default.* TO default;
