-- PostgreSQL Initialization Script for EchoDB
-- This script sets up the initial schema for your application database
--
-- Environment Variables:
--   POSTGRES_DB: Database name (default: postgres)

-- ============================================================================
-- CLEANUP: Remove unwanted default databases
-- ============================================================================

-- Drop the default 'postgres' database if it exists (it's created by PostgreSQL by default)
-- Note: We need to connect to 'echodb' database first, then drop 'postgres'
DO $$
DECLARE
    result TEXT;
BEGIN
    -- Check if postgres database exists and allows connections
    IF EXISTS (SELECT 1 FROM pg_database WHERE datname = 'postgres' AND datallowconn) THEN
        -- Terminate all connections to the postgres database first
        EXECUTE 'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = ''postgres'' AND pid <> pg_backend_pid()';

        -- Drop the database
        EXECUTE 'DROP DATABASE postgres';
        RAISE NOTICE 'Dropped default postgres database';
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'Could not drop postgres database: %', SQLERRM;
END $$;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- SAMPLE TABLES FOR DEMONSTRATION
-- These tables demonstrate real-time CDC replication to ClickHouse via PeerDB
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Users Table
-- Demonstrates user management with profile data
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users
(
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    username TEXT UNIQUE NOT NULL,
    first_name TEXT,
    last_name TEXT,
    avatar_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

-- ----------------------------------------------------------------------------
-- Products Table
-- Demonstrates product catalog management
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products
(
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,
    price DECIMAL(10, 2) NOT NULL,
    stock_quantity INTEGER NOT NULL DEFAULT 0,
    sku TEXT UNIQUE NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Orders Table
-- Demonstrates transactional data for CDC replication
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders
(
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_number TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- pending, processing, shipped, delivered, cancelled
    subtotal DECIMAL(10, 2) NOT NULL,
    tax DECIMAL(10, 2) NOT NULL DEFAULT 0,
    shipping DECIMAL(10, 2) NOT NULL DEFAULT 0,
    total DECIMAL(10, 2) NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    shipped_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ
);

-- ----------------------------------------------------------------------------
-- Order Items Table
-- Demonstrates order line items with product relationships
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_items
(
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10, 2) NOT NULL,
    total_price DECIMAL(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- Events Table
-- Demonstrates time-series analytics data - ideal for ClickHouse aggregation
-- This showcases the power of CDC + ClickHouse for real-time analytics
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events
(
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL, -- page_view, click, signup, purchase, etc.
    event_name TEXT NOT NULL,
    properties JSONB, -- Flexible event properties
    page_url TEXT,
    referrer_url TEXT,
    user_agent TEXT,
    ip_address TEXT,
    device_type TEXT, -- desktop, mobile, tablet
    browser TEXT,
    os TEXT,
    country_code TEXT(2),
    city TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON users(is_active);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_sku ON products(sku);
CREATE INDEX IF NOT EXISTS idx_products_is_active ON products(is_active);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product_id ON order_items(product_id);
CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at DESC);

-- ============================================================================
-- SAMPLE DATA
-- Populates tables with realistic sample data for demonstration
-- ============================================================================

-- Insert sample users
INSERT INTO users (id, email, username, first_name, last_name, is_active, is_verified, created_at, last_login_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'john.doe@example.com', 'johndoe', 'John', 'Doe', true, true, NOW() - INTERVAL '30 days', NOW() - INTERVAL '1 day'),
    ('22222222-2222-2222-2222-222222222222', 'jane.smith@example.com', 'janesmith', 'Jane', 'Smith', true, true, NOW() - INTERVAL '25 days', NOW() - INTERVAL '2 hours'),
    ('33333333-3333-3333-3333-333333333333', 'bob.wilson@example.com', 'bobwilson', 'Bob', 'Wilson', true, false, NOW() - INTERVAL '20 days', NOW() - INTERVAL '5 days'),
    ('44444444-4444-4444-4444-444444444444', 'alice.johnson@example.com', 'alicejohnson', 'Alice', 'Johnson', true, true, NOW() - INTERVAL '15 days', NOW() - INTERVAL '3 hours'),
    ('55555555-5555-5555-5555-555555555555', 'charlie.brown@example.com', 'charliebrown', 'Charlie', 'Brown', false, false, NOW() - INTERVAL '10 days', NULL)
ON CONFLICT (email) DO NOTHING;

-- Insert sample products
INSERT INTO products (name, description, category, price, stock_quantity, sku) VALUES
    ('Wireless Bluetooth Headphones', 'Premium noise-cancelling over-ear headphones', 'Electronics', 149.99, 150, 'ELEC-BT-HEAD-001'),
    ('USB-C Charging Cable', 'Fast charging 6ft cable', 'Electronics', 12.99, 500, 'ELEC-USB-CBL-002'),
    ('Ergonomic Office Chair', 'Adjustable height and lumbar support', 'Furniture', 299.99, 45, 'FURN-CHR-ERG-003'),
    ('Mechanical Gaming Keyboard', 'RGB backlit with blue switches', 'Electronics', 89.99, 75, 'ELEC-KBD-MCH-004'),
    ('Stainless Steel Water Bottle', 'Insulated 32oz bottle', 'Sports', 24.99, 200, 'SPRT-BTL-SSL-005'),
    ('Running Shoes', 'Lightweight performance running shoes', 'Sports', 119.99, 80, 'SPRT-SHO-RUN-006'),
    ('Coffee Maker', 'Programmable 12-cup coffee maker', 'Appliances', 79.99, 60, 'APPL-CMK-PRO-007'),
    ('LED Desk Lamp', 'Adjustable brightness desk lamp', 'Office', 34.99, 120, 'OFFC-LMP-LED-008'),
    ('Yoga Mat', 'Non-slip exercise mat 6mm', 'Sports', 29.99, 200, 'SPRT-MAT-YGA-009'),
    ('Wireless Mouse', 'Ergonomic wireless mouse', 'Electronics', 29.99, 180, 'ELEC-MSE-WLS-010')
ON CONFLICT (sku) DO NOTHING;

-- Insert sample orders
INSERT INTO orders (id, user_id, order_number, status, subtotal, tax, shipping, total, created_at, updated_at) VALUES
    ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', '11111111-1111-1111-1111-111111111111', 'ORD-2024-001', 'delivered', 274.97, 22.00, 5.99, 302.96, NOW() - INTERVAL '25 days', NOW() - INTERVAL '24 days'),
    ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', '22222222-2222-2222-2222-222222222222', 'ORD-2024-002', 'shipped', 149.99, 12.00, 0.00, 161.99, NOW() - INTERVAL '20 days', NOW() - INTERVAL '18 days'),
    ('cccccccc-cccc-cccc-cccc-cccccccccccc', '11111111-1111-1111-1111-111111111111', 'ORD-2024-003', 'processing', 419.98, 33.60, 7.99, 461.57, NOW() - INTERVAL '15 days', NOW() - INTERVAL '14 days'),
    ('dddddddd-dddd-dddd-dddd-dddddddddddd', '33333333-3333-3333-3333-333333333333', 'ORD-2024-004', 'delivered', 12.99, 1.04, 3.99, 18.02, NOW() - INTERVAL '10 days', NOW() - INTERVAL '9 days'),
    ('eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', '44444444-4444-4444-4444-444444444444', 'ORD-2024-005', 'pending', 239.98, 19.20, 0.00, 259.18, NOW() - INTERVAL '5 days', NOW() - INTERVAL '5 days')
ON CONFLICT (order_number) DO NOTHING;

-- Insert sample order items
INSERT INTO order_items (id, order_id, product_id, quantity, unit_price, total_price) VALUES
    ('aaaaaaaa-1111-1111-1111-111111111111', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 1, 1, 149.99, 149.99),
    ('aaaaaaaa-2222-2222-2222-222222222222', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 2, 2, 12.99, 25.98),
    ('aaaaaaaa-3333-3333-3333-333333333333', 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 5, 4, 24.99, 99.96),
    ('bbbbbbbb-1111-1111-1111-111111111111', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb', 1, 1, 149.99, 149.99),
    ('cccccccc-1111-1111-1111-111111111111', 'cccccccc-cccc-cccc-cccc-cccccccccccc', 3, 1, 299.99, 299.99),
    ('cccccccc-2222-2222-2222-222222222222', 'cccccccc-cccc-cccc-cccc-cccccccccccc', 4, 1, 89.99, 89.99),
    ('cccccccc-3333-3333-3333-333333333333', 'cccccccc-cccc-cccc-cccc-cccccccccccc', 8, 1, 34.99, 34.99),
    ('dddddddd-1111-1111-1111-111111111111', 'dddddddd-dddd-dddd-dddd-dddddddddddd', 2, 1, 12.99, 12.99),
    ('eeeeeeee-1111-1111-1111-111111111111', 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee', 6, 2, 119.99, 239.98)
ON CONFLICT DO NOTHING;

-- Insert sample events (time-series analytics data)
INSERT INTO events (user_id, session_id, event_type, event_name, properties, page_url, device_type, browser, os, country_code, city, created_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'sess_001', 'page_view', 'Homepage', '{"duration": 45}'::jsonb, 'https://example.com/', 'desktop', 'Chrome', 'macOS', 'US', 'San Francisco', NOW() - INTERVAL '25 days'),
    ('11111111-1111-1111-1111-111111111111', 'sess_001', 'click', 'Product View', '{"product_id": 1}'::jsonb, 'https://example.com/products/1', 'desktop', 'Chrome', 'macOS', 'US', 'San Francisco', NOW() - INTERVAL '25 days' + INTERVAL '10 seconds'),
    ('11111111-1111-1111-1111-111111111111', 'sess_001', 'click', 'Add to Cart', '{"product_id": 1, "quantity": 1}'::jsonb, 'https://example.com/products/1', 'desktop', 'Chrome', 'macOS', 'US', 'San Francisco', NOW() - INTERVAL '25 days' + INTERVAL '30 seconds'),
    ('22222222-2222-2222-2222-222222222222', 'sess_002', 'page_view', 'Homepage', '{"duration": 32}'::jsonb, 'https://example.com/', 'mobile', 'Safari', 'iOS', 'UK', 'London', NOW() - INTERVAL '20 days'),
    ('22222222-2222-2222-2222-222222222222', 'sess_002', 'click', 'Product View', '{"product_id": 3}'::jsonb, 'https://example.com/products/3', 'mobile', 'Safari', 'iOS', 'UK', 'London', NOW() - INTERVAL '20 days' + INTERVAL '15 seconds'),
    ('33333333-3333-3333-3333-333333333333', 'sess_003', 'page_view', 'Products Listing', '{"duration": 28}'::jsonb, 'https://example.com/products', 'desktop', 'Firefox', 'Windows', 'CA', 'Toronto', NOW() - INTERVAL '10 days'),
    ('33333333-3333-3333-3333-333333333333', 'sess_003', 'search', 'Product Search', '{"query": "headphones", "results": 12}'::jsonb, 'https://example.com/products?q=headphones', 'desktop', 'Firefox', 'Windows', 'CA', 'Toronto', NOW() - INTERVAL '10 days' + INTERVAL '5 seconds'),
    ('44444444-4444-4444-4444-444444444444', 'sess_004', 'page_view', 'Homepage', '{"duration": 15}'::jsonb, 'https://example.com/', 'tablet', 'Safari', 'iPadOS', 'AU', 'Sydney', NOW() - INTERVAL '5 days'),
    ('44444444-4444-4444-4444-444444444444', 'sess_004', 'click', 'Category View', '{"category": "Electronics"}'::jsonb, 'https://example.com/categories/electronics', 'tablet', 'Safari', 'iPadOS', 'AU', 'Sydney', NOW() - INTERVAL '5 days' + INTERVAL '8 seconds'),
    (NULL, 'sess_005', 'page_view', 'Homepage', '{"duration": 5}'::jsonb, 'https://example.com/', 'desktop', 'Chrome', 'Linux', 'DE', 'Berlin', NOW() - INTERVAL '2 days'),
    (NULL, 'sess_005', 'bounce', 'Page Bounce', '{}'::jsonb, 'https://example.com/', 'desktop', 'Chrome', 'Linux', 'DE', 'Berlin', NOW() - INTERVAL '2 days' + INTERVAL '5 seconds'),
    ('11111111-1111-1111-1111-111111111111', 'sess_006', 'page_view', 'Order Confirmation', '{"order_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}'::jsonb, 'https://example.com/orders/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'desktop', 'Chrome', 'macOS', 'US', 'San Francisco', NOW() - INTERVAL '25 days' + INTERVAL '5 minutes')
ON CONFLICT DO NOTHING;

-- ============================================================================
-- NEXT STEPS
-- ============================================================================
-- After your PostgreSQL database is running with this data:
--
-- 1. Create mirrors using the create-mirror.sh script:
--    ./scripts/create-mirror.sh users
--    ./scripts/create-mirror.sh orders
--    ./scripts/create-mirror.sh events
--
-- 2. Or enable auto-mirror to automatically create mirrors for new tables:
--    ./scripts/install-auto-mirror.sh
--
-- 3. Query your data in ClickHouse for real-time analytics:
--    docker exec -it echodb-clickhouse-1 clickhouse-client
--    SELECT * FROM postgres.users ORDER BY created_at DESC LIMIT 10;
--    SELECT event_type, COUNT(*) FROM postgres.events GROUP BY event_type;
-- ============================================================================


-- ============================================================================
-- AUTO-MIRROR TRIGGERS
-- Automatically creates PeerDB mirrors for new tables
-- ============================================================================

-- Enable UUID extension (if not already enabled)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Function to check if a schema is in the watch list
CREATE OR REPLACE FUNCTION is_schema_in_watch_list(schema_name text, watch_list text) RETURNS boolean AS $$
DECLARE
  item text;
BEGIN
  -- Handle empty or NULL watch list (default to public)
  IF watch_list IS NULL OR watch_list = '' THEN
    RETURN schema_name = 'public';
  END IF;

  -- Check each schema in the comma-separated list
  FOREACH item IN ARRAY regexp_split_to_array(watch_list, ',')
  LOOP
    IF schema_name = trim(item) THEN
      RETURN true;
    END IF;
  END LOOP;

  RETURN false;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a new table is created
CREATE OR REPLACE FUNCTION notify_new_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := 'public';
BEGIN
  -- Loop through all objects in this DDL command
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE TABLE'
  LOOP
    -- Extract schema.table name from object_identity
    schema_name := split_part(obj.object_identity, '.', 1);

    -- Skip if not in the target schemas
    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    -- Extract table name (after the first dot)
    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    -- Send notification with schema and table name
    PERFORM pg_notify('peerdb_create_mirror',
      json_build_object(
        'schema', schema_name,
        'table', table_name,
        'timestamp', now()
      )::text
    );
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Function to notify when a table is dropped
CREATE OR REPLACE FUNCTION notify_drop_table()
RETURNS event_trigger AS $$
DECLARE
  obj record;
  schema_name text;
  table_name text;
  watch_list text := 'public';
BEGIN
  -- Loop through all objects in this DDL command
  FOR obj IN SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'DROP TABLE'
  LOOP
    -- Extract schema.table name from object_identity
    schema_name := split_part(obj.object_identity, '.', 1);

    -- Skip if not in the target schemas
    IF NOT is_schema_in_watch_list(schema_name, watch_list) THEN
      CONTINUE;
    END IF;

    -- Extract table name (after the first dot)
    table_name := substring(obj.object_identity from position('.' in obj.object_identity) + 1);

    -- Send notification with schema and table name
    PERFORM pg_notify('peerdb_drop_mirror',
      json_build_object(
        'schema', schema_name,
        'table', table_name,
        'timestamp', now()
      )::text
    );
  END LOOP;
END;
$$ LANGUAGE plpgsql;

-- Create event trigger for CREATE TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_trigger
ON ddl_command_end
WHEN tag IN ('CREATE TABLE')
EXECUTE FUNCTION notify_new_table();

-- Create event trigger for DROP TABLE commands
DROP EVENT TRIGGER IF EXISTS peerdb_auto_mirror_drop_trigger;
CREATE EVENT TRIGGER peerdb_auto_mirror_drop_trigger
ON ddl_command_end
WHEN tag IN ('DROP TABLE')
EXECUTE FUNCTION notify_drop_table();

-- Log successful setup
DO $$
BEGIN
  RAISE NOTICE 'EchoDB auto-mirror triggers installed.';
  RAISE NOTICE 'Watching schemas: public';
  RAISE NOTICE 'Tables created/dropped in these schemas will trigger notifications.';
END $$;
