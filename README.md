# AI Coding Platform MCP Server

A public, read-only Model Context Protocol (MCP) server for querying structured information about AI coding platforms, subscription plans, model availability, pricing, and feature support.

The project uses an Excel workbook as the source dataset, imports validated records into Neon PostgreSQL, exposes simplified read-only database views, and serves the data through a Dockerized FastMCP server deployed on Render.

## Public MCP Endpoint

```text
https://ai-coding-platform-mcp.onrender.com/mcp
```

The endpoint is designed for MCP-compatible clients. Opening it directly in a normal browser may not display a conventional webpage.

## Architecture

```text
Excel workbook
      ↓
Python validation and importer
      ↓
Neon PostgreSQL catalog tables
      ↓
public_api read-only views
      ↓
Restricted catalog_mcp_reader role
      ↓
FastMCP server
      ↓
Docker container
      ↓
Render public HTTPS endpoint
      ↓
MCP-compatible clients
```

## Current Dataset

| Entity | Records |
|---|---:|
| Platforms | 14 |
| Subscription plans | 65 |
| Model providers | 11 |
| Model families | 36 |
| Features | 14 |
| Availability statuses | 12 |
| Platform-model records | 504 |
| Platform-feature records | 196 |

These counts may change as the dataset is updated.

## Available MCP Tools

### `get_dataset_metadata`

Returns dataset-level counts and basic metadata.

Example:

```text
How many platforms and plans are currently in the dataset?
```

### `list_platforms`

Lists AI coding platforms and optionally searches by platform name, company, or category.

Parameters:

- `search`
- `limit`

Example:

```text
List all AI-native coding platforms.
```

### `find_plans`

Finds subscription plans using optional filters.

Parameters:

- `maximum_monthly_price`
- `license_type`
- `platform_name`
- `limit`

Example:

```text
Find AI coding plans costing no more than $20 per month.
```

### `get_platform_details`

Returns one platform with its plans, pricing, supported model families, and feature support.

Parameter:

- `platform_id`

Example:

```text
Show the plans, models, and features for platform PLAT-001.
```

## Important Dataset Limitation

Model and feature availability is currently recorded mainly at the platform level.

A platform-level availability record does not automatically prove that every subscription tier includes the same model or feature. Plan-level entitlement mapping should be added in a later version.

## Project Structure

```text
AI-Coding-MCP-Project/
│
├── database/
│   ├── apply_schema.py
│   ├── apply_views.py
│   ├── schema.sql
│   ├── test_connection.py
│   ├── test_reader.py
│   └── views.sql
│
├── importer/
│   └── import_excel.py
│
├── mcp-server/
│   └── server.py
│
├── Dockerfile
├── .dockerignore
├── .gitignore
├── requirements.txt
└── README.md
```

The following files and folders are intentionally excluded from GitHub and the Docker image:

```text
.env
.venv/
data/
```

## Database Design

The PostgreSQL database uses two schemas.

### `catalog`

Contains the approved internal tables:

- `platform`
- `model_provider`
- `model_family`
- `feature`
- `availability_status`
- `subscription_tier`
- `plan_pricing`
- `platform_model_availability`
- `platform_feature_support`

### `public_api`

Contains simplified views used by the MCP server:

- `platforms`
- `plans`
- `platform_models`
- `platform_features`
- `dataset_metadata`

The MCP server does not query the internal `catalog` tables directly.

## Security Model

The project uses two separate PostgreSQL accounts.

### Owner account

Used only for:

- creating schemas and tables
- applying views
- importing and replacing dataset records

Environment variable:

```env
OWNER_DATABASE_URL=postgresql://...
```

### MCP reader account

Used by the public MCP server.

Environment variable:

```env
MCP_DATABASE_URL=postgresql://...
```

The `catalog_mcp_reader` account:

- can connect to the database
- can use the `public_api` schema
- can select from public views
- cannot read internal `catalog` tables
- cannot insert, update, or delete records
- has read-only transactions enabled

The public server exposes predefined MCP tools only. It does not provide arbitrary SQL execution.

## Local Setup

### 1. Clone the repository

```powershell
git clone https://github.com/eman-ali-1248/ai-coding-platform-mcp.git
cd ai-coding-platform-mcp
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install packages

```powershell
python -m pip install -r requirements.txt
```

### 4. Create `.env`

Create a private `.env` file in the project root:

```env
OWNER_DATABASE_URL=postgresql://OWNER_CONNECTION_STRING
MCP_DATABASE_URL=postgresql://READER_CONNECTION_STRING
```

Never commit `.env` to GitHub.

### 5. Test the database connections

```powershell
python database/test_connection.py
python database/test_reader.py
```

The reader test should confirm that public view access works and internal catalog access is blocked.

## Creating the Database

Apply the database tables:

```powershell
python database/apply_schema.py
```

Apply the public views:

```powershell
python database/apply_views.py
```

## Importing the Excel Dataset

Place one `.xlsx` workbook inside the local `data` folder.

The workbook must contain these sheets:

```text
PLATFORM
MODEL_PROVIDER
MODEL_FAMILY
FEATURE
AVAILABILITY_STATUS
SUBSCRIPTION_TIER
PLAN_PRICING
PLATFORM_MODEL_AVAILABILITY
PLATFORM_FEATURE_SUPPORT
```

Run:

```powershell
python importer/import_excel.py
```

The importer:

- validates required sheets and headers
- validates primary keys
- checks duplicate keys
- verifies cross-sheet relationships
- cleans Boolean and date values
- clears previously imported records
- imports parent tables before child tables
- performs the upload inside a database transaction

Because the import is transactional, a failed import should not leave a partially updated dataset.

## Running the MCP Server Locally

Run:

```powershell
python mcp-server/server.py
```

The local endpoint is:

```text
http://localhost:8000/mcp
```

## Testing with MCP Inspector

Start MCP Inspector in a second terminal:

```powershell
npx -y @modelcontextprotocol/inspector
```

In the Inspector:

1. Select `Streamable HTTP`.
2. Enter:

```text
http://localhost:8000/mcp
```

3. Click **Connect**.
4. Open **Tools**.
5. Click **List Tools**.
6. Run the available tools.

## Docker

### Build the image

```powershell
docker build -t ai-coding-mcp .
```

### Run the container locally

```powershell
docker run --rm --name ai-coding-mcp-local -p 8000:8000 --env-file .env ai-coding-mcp
```

Then test:

```text
http://localhost:8000/mcp
```

through MCP Inspector.

The Docker image intentionally excludes:

- the Excel workbook
- database owner credentials
- importer files
- local virtual environments
- Git history

## Render Deployment

Create a Render Web Service using the GitHub repository.

Recommended settings:

```text
Runtime: Docker
Branch: main
Root Directory: blank
Dockerfile Path: ./Dockerfile
```

Add only this environment variable:

```text
MCP_DATABASE_URL
```

Do not add `OWNER_DATABASE_URL` to Render.

The deployed MCP endpoint will be:

```text
https://ai-coding-platform-mcp.onrender.com/mcp
```

Test the deployed URL through MCP Inspector using `Streamable HTTP`.

## Updating the Dataset

For data-only updates:

1. Replace the workbook inside the local `data` folder.
2. Activate the virtual environment.
3. Run the importer.
4. Verify the record counts in Neon.
5. Test the MCP tools again.

Commands:

```powershell
.\.venv\Scripts\Activate.ps1
python importer/import_excel.py
```

A Render redeployment is normally not required for data-only changes because the MCP server reads the latest records directly from Neon.

## Updating the Server

A new deployment is required when changing:

- `mcp-server/server.py`
- tool names or parameters
- dependencies
- the Dockerfile
- application behavior

Push code changes to GitHub:

```powershell
git add .
git commit -m "Describe the update"
git push origin main
```

Render should automatically deploy the updated commit.

## Planned Improvements

Possible future improvements include:

- plan-level model entitlement records
- plan-level feature entitlement records
- source and provenance tracking
- last-verified timestamps
- change history
- raw and staging database schemas
- automated source ingestion
- richer comparison tools
- dataset versioning
- authentication and rate limiting
- health and status endpoints

## Technology Stack

- Python
- FastMCP
- MCP Streamable HTTP
- PostgreSQL
- Neon
- Psycopg
- OpenPyXL
- Docker
- GitHub
- Render

## Status

The initial MVP is complete:

- Excel dataset validated
- PostgreSQL schema created
- records imported into Neon
- public read-only views created
- restricted database reader configured
- FastMCP tools created
- local MCP testing completed
- Docker testing completed
- GitHub repository created
- Render deployment completed
- public endpoint tested through MCP Inspector