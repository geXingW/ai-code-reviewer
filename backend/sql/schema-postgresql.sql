-- ai-code-reviewer 全量建库脚本（postgresql）
-- 由 scripts/export_schema_sql.py 从 models/ 自动生成，请勿手改；
-- 模型变更后重新运行：python scripts/export_schema_sql.py
-- 使用方式：数据库为空库时手动执行本文件，应用启动不再自动建表。
-- 注意：所有建库脚本均不包含外键约束，表间关联由应用层维护。

CREATE TABLE audit_logs (
	id CHAR(32) NOT NULL, 
	actor VARCHAR(255), 
	action VARCHAR(255) NOT NULL, 
	resource_type VARCHAR(255) NOT NULL, 
	resource_id CHAR(32), 
	details JSON, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE engines (
	id CHAR(32) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	engine_type VARCHAR(50) NOT NULL, 
	config JSON, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE global_settings (
	key VARCHAR(255) NOT NULL, 
	value TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (key)
);

CREATE TABLE providers (
	id CHAR(32) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	protocol VARCHAR(50) NOT NULL, 
	base_url VARCHAR(2048) NOT NULL, 
	api_key TEXT NOT NULL, 
	model VARCHAR(255) NOT NULL, 
	temperature FLOAT NOT NULL, 
	max_tokens INTEGER NOT NULL, 
	extra_headers JSON, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE roles (
	id CHAR(32) NOT NULL, 
	name VARCHAR(64) NOT NULL, 
	description VARCHAR(256), 
	is_system BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);

CREATE TABLE rules (
	id CHAR(32) NOT NULL, 
	rule_id VARCHAR(255) NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	prompt_snippet TEXT NOT NULL, 
	severity_default VARCHAR(20) DEFAULT 'WARNING' NOT NULL, 
	category_default VARCHAR(20), 
	languages JSON NOT NULL, 
	path_patterns JSON NOT NULL, 
	tags JSON NOT NULL, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	grace_period_until DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_rules_rule_id ON rules (rule_id);

CREATE TABLE projects (
	id CHAR(32) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	gitlab_project_id VARCHAR(255) NOT NULL, 
	gitlab_base_url VARCHAR(512) DEFAULT '' NOT NULL, 
	gitlab_access_token TEXT NOT NULL, 
	webhook_secret TEXT NOT NULL, 
	engine_id CHAR(32), 
	provider_id CHAR(32), 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	timeout_seconds INTEGER NOT NULL, 
	max_files INTEGER NOT NULL, 
	commit_review_enabled BOOLEAN DEFAULT false NOT NULL, 
	commit_review_max_per_push INTEGER NOT NULL, 
	ignore_paths JSON, 
	default_block_severity VARCHAR(30) DEFAULT 'BLOCKER' NOT NULL, 
	deleted_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE role_permissions (
	id CHAR(32) NOT NULL, 
	role_id CHAR(32) NOT NULL, 
	permission VARCHAR(128) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (role_id, permission)
);

CREATE TABLE users (
	id CHAR(32) NOT NULL, 
	username VARCHAR(64) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	display_name VARCHAR(128), 
	role_id CHAR(32), 
	enabled BOOLEAN NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_username ON users (username);

CREATE TABLE project_block_policies (
	id CHAR(32) NOT NULL, 
	project_id CHAR(32), 
	branch_pattern VARCHAR(255) NOT NULL, 
	block_severity VARCHAR(30) NOT NULL, 
	block_on_engine_error BOOLEAN DEFAULT false NOT NULL, 
	require_all_resolved BOOLEAN DEFAULT false NOT NULL, 
	priority INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE project_negative_prompts (
	project_id CHAR(32) NOT NULL, 
	content TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (project_id)
);

CREATE TABLE project_notification_channels (
	id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	channel_type VARCHAR(30) NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	webhook_url TEXT NOT NULL, 
	secret TEXT, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE project_rules (
	project_id CHAR(32) NOT NULL, 
	rule_id CHAR(32) NOT NULL, 
	enabled BOOLEAN DEFAULT true NOT NULL, 
	severity_override VARCHAR(20), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (project_id, rule_id)
);

CREATE TABLE user_mappings (
	id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	gitlab_username VARCHAR(255) NOT NULL, 
	dingtalk_mobile VARCHAR(32) NOT NULL, 
	dingtalk_userid VARCHAR(128), 
	display_name VARCHAR(128), 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (project_id, gitlab_username)
);

CREATE INDEX ix_user_mappings_project_id ON user_mappings (project_id);

CREATE TABLE user_project_assignments (
	id CHAR(32) NOT NULL, 
	user_id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id, project_id)
);

CREATE TABLE reviews (
	id CHAR(32) NOT NULL, 
	project_id CHAR(32) NOT NULL, 
	mr_iid VARCHAR(255), 
	source_branch VARCHAR(255) NOT NULL, 
	target_branch VARCHAR(255) NOT NULL, 
	commit_sha VARCHAR(255) NOT NULL, 
	status VARCHAR(30) DEFAULT 'pending' NOT NULL, 
	engine_used VARCHAR(255), 
	provider_used VARCHAR(255), 
	policy_applied CHAR(32), 
	has_blocker BOOLEAN DEFAULT false NOT NULL, 
	finding_count INTEGER NOT NULL, 
	duration_ms INTEGER, 
	raw_llm_output TEXT, 
	base_sha VARCHAR(255), 
	parent_review_id CHAR(32), 
	review_mode VARCHAR(20) DEFAULT 'full' NOT NULL, 
	lifecycle_event VARCHAR(20), 
	review_kind VARCHAR(10) DEFAULT 'mr' NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE review_findings (
	id CHAR(32) NOT NULL, 
	review_id CHAR(32) NOT NULL, 
	file_path VARCHAR(2048) NOT NULL, 
	line_number INTEGER, 
	rule_id VARCHAR(255) NOT NULL, 
	severity VARCHAR(20) NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	description TEXT, 
	suggestion TEXT, 
	existing_code TEXT, 
	category VARCHAR(20), 
	confidence FLOAT NOT NULL, 
	gitlab_discussion_id VARCHAR(255), 
	fp_status VARCHAR(20) DEFAULT 'NONE' NOT NULL, 
	fp_marked_by VARCHAR(255), 
	fp_marked_at DATETIME, 
	fp_marked_reason TEXT, 
	fp_reviewed_by VARCHAR(255), 
	fp_reviewed_at DATETIME, 
	fp_review_note TEXT, 
	first_seen_review_id CHAR(32), 
	resolved_in_review_id CHAR(32), 
	status VARCHAR(20) DEFAULT 'open' NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE negative_examples (
	id CHAR(32) NOT NULL, 
	rule_id VARCHAR(255) NOT NULL, 
	project_id CHAR(32), 
	code_snippet TEXT NOT NULL, 
	explanation TEXT, 
	source_finding_id CHAR(32), 
	approved_by VARCHAR(255), 
	approved_at DATETIME, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
