-- Trispoke schema for Supabase.
-- Paste into Supabase Dashboard SQL Editor and Run.
--
-- Mirrors the SQLAlchemy models in src/trispoke/db/models.py at Alembic
-- revision f3b8c2459d12. Seeds the alembic_version row so future
-- `alembic upgrade head` runs against Supabase are no-ops.

CREATE TABLE apollo_slots (
	id SERIAL NOT NULL,
	slot_name VARCHAR NOT NULL,
	sequence_id VARCHAR NOT NULL,
	step_id VARCHAR NOT NULL,
	touch_id VARCHAR NOT NULL,
	template_id VARCHAR NOT NULL,
	followup_step_id VARCHAR,
	followup_touch_id VARCHAR,
	followup_template_id VARCHAR,
	mailbox_id VARCHAR,
	schedule_id VARCHAR,
	status VARCHAR NOT NULL,
	last_message_id VARCHAR,
	last_message_status VARCHAR,
	last_used_at TIMESTAMP WITHOUT TIME ZONE,
	last_freed_at TIMESTAMP WITHOUT TIME ZONE,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	PRIMARY KEY (id),
	UNIQUE (slot_name),
	UNIQUE (sequence_id)
);
CREATE INDEX idx_apollo_slots_status ON apollo_slots (status, last_message_status);
CREATE INDEX idx_apollo_slots_last_used ON apollo_slots (last_used_at);

CREATE TABLE campaigns (
	id SERIAL NOT NULL,
	name VARCHAR NOT NULL,
	mode VARCHAR NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	started_at TIMESTAMP WITHOUT TIME ZONE,
	settings_json TEXT NOT NULL,
	auto_approve_mode VARCHAR NOT NULL DEFAULT 'after_warmup',
	warmup_threshold INTEGER NOT NULL DEFAULT 20,
	PRIMARY KEY (id)
);

CREATE TABLE apollo_sync_state (
	id SERIAL NOT NULL,
	campaign_id INTEGER NOT NULL,
	last_synced_at TIMESTAMP WITHOUT TIME ZONE,
	last_event_cursor VARCHAR,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	PRIMARY KEY (id),
	UNIQUE (campaign_id),
	FOREIGN KEY(campaign_id) REFERENCES campaigns (id)
);
CREATE INDEX idx_apollo_sync_campaign ON apollo_sync_state (campaign_id);

CREATE TABLE leads (
	id SERIAL NOT NULL,
	campaign_id INTEGER NOT NULL,
	email VARCHAR NOT NULL,
	first_name VARCHAR,
	last_name VARCHAR,
	title VARCHAR,
	company_name VARCHAR,
	company_domain VARCHAR,
	company_size VARCHAR,
	company_industry VARCHAR,
	company_location VARCHAR,
	linkedin_url VARCHAR,
	apollo_id VARCHAR,
	apollo_contact_id VARCHAR,
	intake_source VARCHAR DEFAULT 'apollo_csv',
	source_row_json TEXT NOT NULL,
	status VARCHAR NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	PRIMARY KEY (id),
	FOREIGN KEY(campaign_id) REFERENCES campaigns (id)
);
CREATE INDEX idx_leads_status ON leads (status);
CREATE INDEX idx_leads_campaign_id ON leads (campaign_id);

CREATE TABLE emails (
	id SERIAL NOT NULL,
	lead_id INTEGER NOT NULL,
	campaign_id INTEGER NOT NULL,
	subject VARCHAR NOT NULL,
	body TEXT NOT NULL,
	model_used VARCHAR,
	prompt_version VARCHAR,
	tokens_used INTEGER,
	generation_seconds FLOAT,
	parent_email_id INTEGER,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	apollo_sequence_id VARCHAR,
	apollo_step_id VARCHAR,
	apollo_enrollment_id VARCHAR,
	apollo_pushed_at TIMESTAMP WITHOUT TIME ZONE,
	apollo_message_id VARCHAR,
	qc_status VARCHAR DEFAULT 'pending',
	qc_checked_at TIMESTAMP WITHOUT TIME ZONE,
	qc_flags_json TEXT,
	qc_model_used VARCHAR,
	send_mode VARCHAR NOT NULL DEFAULT 'scheduled',
	PRIMARY KEY (id),
	FOREIGN KEY(lead_id) REFERENCES leads (id),
	FOREIGN KEY(campaign_id) REFERENCES campaigns (id),
	FOREIGN KEY(parent_email_id) REFERENCES emails (id)
);
CREATE INDEX idx_emails_apollo_seq ON emails (apollo_sequence_id);
CREATE INDEX idx_emails_apollo_msg ON emails (apollo_message_id);
CREATE INDEX idx_emails_lead_id ON emails (lead_id);

CREATE TABLE events (
	id SERIAL NOT NULL,
	lead_id INTEGER NOT NULL,
	event_type VARCHAR NOT NULL,
	payload_json TEXT NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	PRIMARY KEY (id),
	FOREIGN KEY(lead_id) REFERENCES leads (id)
);
CREATE INDEX idx_events_lead_id ON events (lead_id);

CREATE TABLE pain_analyses (
	id SERIAL NOT NULL,
	lead_id INTEGER NOT NULL,
	chronic TEXT,
	acute TEXT,
	trigger TEXT,
	confidence FLOAT,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	PRIMARY KEY (id),
	UNIQUE (lead_id),
	FOREIGN KEY(lead_id) REFERENCES leads (id)
);

CREATE TABLE replies (
	id SERIAL NOT NULL,
	lead_id INTEGER NOT NULL,
	email_id INTEGER NOT NULL,
	received_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	from_address VARCHAR NOT NULL,
	subject VARCHAR NOT NULL,
	body TEXT NOT NULL,
	classification VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(lead_id) REFERENCES leads (id),
	FOREIGN KEY(email_id) REFERENCES emails (id)
);
CREATE INDEX idx_replies_lead_id ON replies (lead_id);

CREATE TABLE sends (
	id SERIAL NOT NULL,
	email_id INTEGER NOT NULL,
	sent_from_inbox VARCHAR NOT NULL,
	sent_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
	smtp_message_id VARCHAR,
	status VARCHAR NOT NULL,
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
	sent_via VARCHAR NOT NULL DEFAULT 'apollo',
	apollo_mailbox_id VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(email_id) REFERENCES emails (id)
);
CREATE INDEX idx_sends_email_id ON sends (email_id);

-- Alembic version marker so future `alembic upgrade head` runs against
-- Supabase are no-ops (won't try to re-create existing tables).
CREATE TABLE alembic_version (
	version_num VARCHAR(32) NOT NULL,
	PRIMARY KEY (version_num)
);
INSERT INTO alembic_version (version_num) VALUES ('f3b8c2459d12');
