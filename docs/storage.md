# 数据存储层（Storage Layer）

## 概览

本项目支持两种关系型数据库：**PostgreSQL 15** 与 **MySQL 8.0**。运行时通过环境变量 `DATABASE_URL` 切换，代码无差异。

## 支持矩阵

- **PostgreSQL 15**（默认，生产推荐）
  - DSN 格式：`postgresql+asyncpg://user:pass@host:5432/dbname`
  - UUID 存储：原生 `uuid` 类型
  - JSON 存储：`json`（跨方言，未使用 `jsonb` 特有功能）
- **MySQL 8.0**（兼容模式）
  - DSN 格式：`mysql+aiomysql://user:pass@host:3306/dbname`
  - UUID 存储：`char(32)`（无连字符，SQLAlchemy 自动处理）
  - JSON 存储：`json`
  - 字符集：`utf8mb4` + `utf8mb4_unicode_ci`

## 切库方式

**PG（默认）**：什么都不用改，`docker compose up -d` 即可。

**MySQL**：
```bash
# 1. 启动 MySQL 服务（--profile 隔离，默认不启动）
docker compose --profile mysql up -d mysql

# 2. 设置 DSN 环境变量并启 backend
DATABASE_URL="mysql+aiomysql://ai_reviewer:ai_reviewer@mysql:3306/ai_code_reviewer" \
  docker compose --profile mysql up -d backend
```

或者永久切换：改 `.env` 里的 `DATABASE_URL`。

## 设计原则

### 1. 只用 SQLAlchemy 通用类型

Model 层严格使用跨方言类型，不引 dialect-specific 类型：

- ✅ `sa.Uuid` （不用 `postgresql.UUID`）
- ✅ `sa.JSON`（不用 `postgresql.JSONB`）
- ✅ `sa.true()` / `sa.false()`（不用 `text("true")`）
- ✅ `sa.DateTime(timezone=True)`（两个 DB 都支持）
- ✅ `sa.String / Integer / Boolean / Float / Text`（跨方言）

好处：迁移文件不需要方言分支，autogenerate 也不会误报差异。

### 2. UUID 在 Python 层生成

主键 UUID 不再依赖 DB 函数（`gen_random_uuid()` / `UUID()`），改用：

```python
id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
```

好处：不依赖 pgcrypto extension，也不需要 MySQL 触发器。

### 3. Alembic 迁移跨方言

初始迁移 `0001_initial_schema.py` 只用 `sa.*` 类型，可在两种 DB 上执行同一份迁移。

**注意**：如果未来新增 dialect-specific 需求（比如 PG 的 GIN 索引），应通过 `context.get_context().dialect.name` 判断方言，分支执行。

### 4. 加密字段使用 TEXT

`Provider.api_key`、`Project.gitlab_access_token`、`Project.webhook_secret` 存 Fernet 加密后的 base64 字符串。两个 DB 都用 `TEXT` 存，字节完全兼容。

**警告**：跨库迁移数据时 `SECRET_KEY` 必须保持一致，否则解密全废。

## CI 双库矩阵

`.github/workflows/ci.yml` 用 `strategy.matrix.db` 让 Backend Tests 在两个 DB 上各跑一次：

- `postgres`：`postgresql+asyncpg://...`
- `mysql`：`mysql+aiomysql://...`

两个 job 独立跑，其中任意一个 fail 都会阻止 PR 合入。

## 性能对比（参考）

（后续如有需要，在此补充压测数据。）

## 常见问题

**Q: 生产用 PG 还是 MySQL？**
默认推荐 PG，理由：
- JSONB / 部分索引 / 分区表等特性生态更成熟
- asyncpg 性能比 aiomysql 略优
- 现有部署经验积累在 PG 上

只有当客户环境**强制**只能提供 MySQL 时，才切到 MySQL。

**Q: 能同时用两个 DB 吗？**
不能。同一个 backend 实例只连一个 DB。如果需要迁移数据，用 `pg_dump` + `mysql` 手动导入（因为 UUID 表示不同，需要转换）。

**Q: SQLite 支持吗？**
不支持。Fernet 加密字段 + alembic 迁移在 SQLite 上体验很差，投入产出比不划算。

**Q: 未来还想支持 Oracle / SQL Server？**
先落地 PR-56C（Repository 抽象层）后再讨论。目前的 Model 直连模式再加方言会指数级复杂。
