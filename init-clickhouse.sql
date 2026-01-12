-- ClickHouse Initialization Script for EchoDB
-- Creates the _peerdb database for PeerDB internal metadata tables

-- Drop the default database (created by ClickHouse automatically)
DROP DATABASE IF EXISTS default;

-- Create database for PeerDB internal tables
-- PeerDB uses this to store metadata, checkpoints, and raw staging data
CREATE DATABASE IF NOT EXISTS _peerdb;
