# Shopping Assistant Agent

![CI/CD](https://github.com/kevinvo/shopping-assistant/workflows/CI/CD/badge.svg)

An intelligent shopping assistant powered by AI that helps users discover products through Reddit discussions and recommendations. The system processes Reddit data, indexes it in vector databases, and provides real-time chat-based product recommendations.

## Architecture Overview

The application is built on AWS using a serverless architecture with the following components:

### Tech Stack

- **Backend Framework**: [AWS Chalice](https://github.com/aws/chalice) (Python serverless framework)
- **Infrastructure**: AWS CDK (TypeScript/CloudFormation)
- **Vector Databases**: Qdrant and Weaviate
- **LLM Providers**: DeepSeek (chat generation) and Anthropic
- **Query Rewriting**: Context-aware reformulation before retrieval
- **Prompt Enrichment**: HyPE (Hypothetical Prompt Embeddings)
- **Embeddings**: OpenAI
- **AI Orchestration**: LangChain
- **Retrieval**: Hybrid RAG (vector + keyword search)
- **Real-time Communication**: WebSocket API via API Gateway
- **Data Lake**: Amazon S3 (raw and curated zones), AWS Glue ETL jobs, Amazon Athena interactive queries
- **Queue Management**: SQS
- **Operational Storage**: DynamoDB

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Client Application                       │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │ WebSocket / REST API
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      API Gateway (Chalice)                       │
├─────────────────────────────────────────────────────────────────┤
│  • REST Endpoints (/session, /auth, /health)                     │
│  • WebSocket Handlers (connect, message, disconnect)             │
│  • Scheduled Tasks (scraper, indexer, glue starter)             │
│  • SQS Consumers (chat processor, evaluator)                     │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Processing Layer                            │
├─────────────────────────────────────────────────────────────────┤
│  • Chat Processor (query understanding, retrieval, reranking)    │
│  • Query Rewriting (context-aware reformulation)                 │
│  • Hybrid RAG Retrieval (dense vectors + keyword signals)        │
│  • HyPE Prompt Embeddings (hypothetical context expansion)       │
│  • Semantic Chunker (semchunk + tiktoken for semantic splitting) │
│  • Data Indexer (vector embeddings to Qdrant/Weaviate)          │
│  • Reddit Scraper (daily Reddit data collection)                │
│  • Glue Jobs (batch data processing)                             │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Storage & Intelligence                      │
├─────────────────────────────────────────────────────────────────┤
│  • Vector DBs: Qdrant, Weaviate                                 │
│  • Data Lake: S3 (raw/processed Reddit data)                    │
│  • Metadata: DynamoDB                                           │
│      - WebSocketConnectionsV2  (live connection routing)        │
│      - ConversationHistoriesV1 (per-conversation chat memory)   │
│      - SessionsTableV2         (REST/cookie sessions)           │
│  • Query Engine: Athena (data lake queries)                     │
│  • Observability: LangSmith (query logs, retrieval metrics)     │
│  • LLMs: DeepSeek (chat), Anthropic (Claude)                    │
│  • Embeddings: OpenAI                                           │
└─────────────────────────────────────────────────────────────────┘
```

### Key Components

1. **Chalice API** (`chalice_app/`): Main application with all handlers
   - REST API for session management
   - WebSocket handlers for real-time chat
   - Scheduled functions for data processing
   - SQS consumers for async message processing

2. **CDK Infrastructure** (`cdk_infrastructure/`): Infrastructure as code
   - S3 buckets for raw and processed data
   - DynamoDB tables for sessions and metadata
   - SQS queues for async processing
   - Glue jobs and Athena
   - VPC and networking

3. **Business Logic** (`chalice_app/chalicelib/`):
   - Modular packages for APIs, jobs, ingestion, LLM utilities, sessions, and shared models
   - Chat processing with RAG
   - Vector indexing and retrieval
   - LLM integration, reranking, and LangSmith logging
   - Session and connection management

4. **Glue Jobs** (`glue_jobs/`): Batch data processing
   - Reddit data transformation
   - Top posts processing
   - Daily aggregation jobs

## Architecture Decisions

This section captures load-bearing design choices that aren't obvious from the
code alone. Each decision lists what we picked, the alternatives we considered,
and why.

### Anonymous identity = client-generated `session_id`

The frontend (`chatting-assistant-front-end`) generates a UUID on first load
and persists it in the browser's `localStorage` under
`shopping-assistant-chat`. That UUID is sent to `$connect` as a query string:

```
wss://<api>.execute-api.<region>.amazonaws.com/<stage>?session_id=<uuid>
```

The backend reads it (`chalicelib/api/websocket.py:_extract_query_params`) and
stores it on `ConnectionInfo`. If the client omits it, the server falls back
to generating a UUID.

**Lifetime:**

| Layer                          | Lifetime                                      |
| ------------------------------ | --------------------------------------------- |
| Browser (`localStorage`)       | Until the user clears storage                 |
| `ConnectionInfo` table         | 1-day TTL (live socket only)                  |
| `ConversationHistoriesV1`      | 30-day rolling TTL (bumped on every save)     |

**Why client-generated:** The previous design generated `session_id`
server-side at every `$connect`, which meant a reload (= new connection) was a
new identity. Putting the source of truth on the client makes the identity
survive reloads and disconnects without any auth.

**Why anonymous:** Product is pre-auth. When auth lands, it can either replace
`session_id` with the user's account id or live alongside it.

### Per-conversation chat memory in `ConversationHistoriesV1`

Chat history is partitioned per `(session_id, conversation_id)` in a dedicated
DynamoDB table.

- **Partition key:** composite string `"<session_id>#<conversation_id>"`.
- **TTL attribute:** `expiry_time`. **30-day rolling** — every save bumps it
  forward, so idle threads garbage-collect themselves and active threads stay
  alive indefinitely.
- **Frontend role:** owns the conversation list (in `localStorage`) and picks
  `conversation_id` for each thread.

**What we replaced:** chat history used to live on `ConnectionInfo.chat_history`,
keyed by `connection_id`. Disconnect deleted it; concurrent conversations
shared one history. Both behaviors were the wrong contract for a multi-thread
chat UI.

**Alternatives considered:**

- *Extend `SessionsTableV2`* (folding conversations under the existing
  cookie-session record). Rejected: mixes REST session concerns with WS chat
  state and risks the 400KB DDB item-size cap with long histories.
- *Keep using `ConnectionInfo`, just bucket by `conversation_id` inside the
  same record.* Rejected: still lost on disconnect.

A GSI on `session_id` (for "list a user's conversations server-side") is
intentionally **not** added — the frontend already lists conversations from
`localStorage`. We can add it later if cross-device session restore becomes a
requirement.

### Streaming protocol echoes `conversationId`

`message_start` / `message_chunk` / `message_end` carry both `messageId` and
`conversationId`. The frontend uses these to route chunks into the
originating conversation thread, even if the user switches threads
mid-stream. Today's `messageId` defaults to the inbound `request_id`; the
field is reserved for future use cases (multi-message turns, partial edits).

### CI/CD: persist Chalice deployed state in S3

`chalice deploy` tracks API Gateway and Lambda IDs in
`chalice_app/.chalice/deployed/<stage>.json`. That path is gitignored, so a
fresh CI checkout had no record of the existing API → Chalice would mint a
brand-new WebSocket API Gateway on every deploy, and the cleanup script
would delete the old one as orphaned. Effect: front-end broke on every
backend deploy and we accumulated dozens of unused API Gateways.

The workflow now:

1. **Before cleanup:** `aws s3 cp s3://shopping-assistant-layers-ap-southeast-1/chalice-state/chalice-test.json …` into place. 404 on first run is treated as "no prior state."
2. **After deploy:** uploads the freshly-written state back to the same key.
3. The cleanup script reads the restored file to learn which API IDs are
   active and skips them.

**Effect:** API Gateway IDs stay stable across deploys. The frontend's
hardcoded `WEBSOCKET_URL` constant doesn't need to be bumped on every deploy.

**Why S3 and not git-tracking:** the deployed-state file contains ARNs and
deployment metadata that we don't want versioned. S3 is a clean side channel
that's already accessible to the deploy IAM principal (it already writes
Lambda layer artifacts to the same bucket).

### Backward-compat fallbacks (rollout-only)

Two fallbacks exist to keep in-flight clients working during the deploy
window. Both are scheduled for removal once logs confirm no misses:

- `$message`: missing `sessionId` falls back to
  `ConnectionInfo.session_id`; missing `conversationId` defaults to
  `"default"`.
- `MessagePayload.from_dict` tolerates SQS messages without the new fields
  (legacy in-flight messages from before the schema bump).

`ConnectionInfo.chat_history` is now dead weight (written empty on connect,
never read or appended to) and will be removed in a follow-up cleanup.

### Reducing chat startup time

Goal: time from inbound user message to first response chunk ≤ 10s on a warm
container. Achieved at ~5s today. This is the playbook used to get there and
the moves still on the table if the floor needs to drop further.

**The two speed regimes.** A chat invocation either hits a *warm* container
(Python process already loaded, singletons primed, TLS pools to OpenAI /
OpenRouter / Qdrant established) or a *cold* one (none of the above). The
floor for cold is ~20s of one-time work before any chat work begins
regardless of code changes. The strategy is to make warm fast *and* keep
chat_processor warm so users never hit cold.

#### Make warm fast

1. **Singleton everything boto and HTTP-client-shaped.**
   `chalicelib/sessions/chat_message_service.py:get_chat()` is the
   module-level lazy singleton for `Chat` → `QdrantIndexer` → `OpenAIEmbeddings`
   → `boto3.client("s3")` → `DeepSeekClient` → `BM25Reranker`. Constructing
   any of these per-request silently discards the HTTP/TLS pool to the
   upstream service. `AppConfig` is also a module-level singleton at
   `chalicelib/core/config.py:228` — importing the class and calling
   `AppConfig()` again re-runs the Secrets Manager fetch (~200ms warm, ~3-5s
   cold). Always import `config`, never `AppConfig`.

2. **Cache anything with a per-call constructor cost.** The API Gateway
   Management client (`apigatewaymanagementapi`) is built per-call inside
   `send_message` and cached via `_apigw_client_for(domain, stage)` with
   `lru_cache(maxsize=8)`. LangChain's `ChatOpenAI` is similarly cached per
   `(temperature, top_p, max_tokens, json_mode)` on `DeepSeekClient`. Each
   one was ~10-30ms × 20 calls per response.

3. **Persist BM25 vocab to S3.** `QdrantIndexer._generate_query_sparse_vector`
   needs a term→index dictionary and an IDF table to build the query sparse
   vector. Rebuilding from a 10k-document Qdrant scroll costs ~7.6s. The
   dicts are written to
   `s3://<processed-reddit-data>/vocab/<collection>.json` after the first
   rebuild; subsequent cold starts load them in ~50ms.

4. **Overlap rewrite with search.** `Chat.process_chat` runs
   `rewrite_and_generate_hyde` (one LLM call, ~5s) concurrently with the
   primary `hybrid_search`. The chain ends at `max(rewrite, search)` instead
   of `rewrite + search`. Both the rewrite output and the raw user query are
   still surfaced — rewrite feeds rerank scoring, the raw query feeds search.

5. **Coalesce streaming chunks.** Per-token APIGW `post_to_connection` is
   ~70ms each. The streaming callback buffers 64 chars before each flush
   (`STREAM_CHUNK_FLUSH_CHARS` in `chat_message_service.py`); the first
   chunk flushes eagerly so the user sees text immediately. A 256-token
   response goes from ~256 sends to ~25.

#### Keep chat_processor warm

`keep_chat_warm` in `chalice_app/app.py` runs on a `Rate(3, MINUTES)`
schedule and enqueues a sentinel `{"keep_warm": true}` message to the
`ChatProcessingQueue`. `chat_processor` recognizes the sentinel via
`is_keep_warm_record(body)` and short-circuits to `prime_singletons()`
instead of going through `process_message`. `prime_singletons()` forces:

- `get_chat()` — builds the full singleton tree
- `_generate_query_sparse_vector("warmup")` — loads BM25 vocab from S3
- `embeddings.embed_query("warmup")` — establishes OpenAI httpx TLS pool

Cost ~$0.03/month. AWS recycles idle Lambda containers after ~10-15 min,
so a 3-min cadence is the smallest cushion that keeps the container alive.
60-min would always be cold. Function name kept short (`keep_chat_warm`,
not `keep_chat_processor_warm`) to fit EventBridge's 64-char rule-name
limit — same issue PR #14 hit on `refresh_suggestions`.

#### Measuring

`chalicelib/core/performance_timer.py` provides `@measure_execution_time`
(decorator) and `timed_block(name)` (context manager). Both emit:

```
PERFORMANCE: <name> executed in X.XXXX seconds
```

Use the **`monitor_all_logs.sh`** helper in `chalice_app/tests/scripts/` to
tail or analyze production logs:

```bash
./chalice_app/tests/scripts/monitor_all_logs.sh recent 15m chat
./chalice_app/tests/scripts/monitor_all_logs.sh follow chat
./chalice_app/tests/scripts/monitor_all_logs.sh analyze 24h chat
```

**Important — Lambda Duration ≠ TTFT.** "TTFT" is the time from START to
the first `MESSAGE_START` payload reaching APIGW. Lambda Duration is the
full handler runtime, which includes the LLM streaming its *entire*
response to completion *after* the first chunk has already shipped to the
user. A 22s Lambda Duration with a 5s TTFT is a normal warm response, not
a slow one. To measure TTFT, diff the `START RequestId` timestamp against
the first `Message content (ResponsePayload): {'type': <MessageType.MESSAGE_START` log line.

#### What's still on the table

- **Lambda region move to match Qdrant.** Qdrant Cloud is in `eu-west-2`;
  Lambda is in `ap-southeast-1`. Singapore ↔ London RTT (~160ms) adds
  300-500ms per Qdrant round-trip and ~200-400ms per OpenAI embeddings call.
  Same-region projection saves 1-2s warm and 3-5s cold. Requires migrating
  DynamoDB (Global Tables), S3 (Cross-Region Replication), Secrets Manager
  (multi-region), and re-pointing the front-end at the new WebSocket URL.
- **Provisioned concurrency on chat_processor.** Eliminates cold starts at
  the cost of ~$X/month per concurrent unit. Only worth doing if the
  3-minute keep-warm cadence proves insufficient (i.e. real traffic gaps
  exceed AWS's idle-recycle window).
- **Drop LangChain on the hot path.** ~13s of cold-start import time is
  LangChain's own module graph. Switching to direct OpenAI/Anthropic SDK
  calls would shave ~5-8s of cold init and trim ~50-150ms warm per LLM
  call. Big refactor.

## Prerequisites

- **Python**: 3.12+
- **Node.js**: 18+ (for CDK)
- **AWS CLI**: Configured with appropriate credentials
- **Docker**: For building Lambda layers
- **Chalice**: Python serverless framework
- **CDK CLI**: For infrastructure deployment

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd shopping-assistant-agent
```

### 2. Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
# Core application dependencies
pip install -r requirements.txt

# Development dependencies
pip install -r requirements_dev.txt

# Install CDK globally
npm install -g aws-cdk

# Bootstrap CDK (first time only)
cdk bootstrap
```

### 4. Configure Environment Variables

Create a `.env` file or set environment variables:

```bash
# AWS Configuration
AWS_REGION=ap-southeast-1
AWS_ACCOUNT_ID=your-account-id

# Vector Databases
QDRANT_URL=your-qdrant-url
WEAVIATE_URL=your-weaviate-url

# LLM API Keys
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
LANGSMITH_API_KEY=your-langsmith-key

# Reddit API
REDDIT_CLIENT_ID=your-reddit-client-id
REDDIT_CLIENT_SECRET=your-reddit-secret

# WebSocket Configuration
WEBSOCKET_DOMAIN=your-api-gateway-domain
WEBSOCKET_STAGE=chalice-test
```

## Deployment

### Infrastructure Deployment (CDK)

Deploy the AWS infrastructure:

```bash
cd cdk_infrastructure
cdk deploy --all
```

This creates:
- S3 buckets for data storage
- DynamoDB tables
- SQS queues
- Glue jobs and Athena
- VPC and networking resources

### Application Deployment (Chalice)

Deploy the Chalice application:

```bash
cd chalice_app

# Deploy to test stage
chalice deploy --stage chalice-test

# Deploy to production
chalice deploy --stage prod
```

### Scraper Step Functions Workflow

- Scraper state machines are provisioned by CDK. Stage metadata lives in `infrastructure/config/scraper_step_functions.json` (one entry per Chalice stage: Lambda name, desired Step Function name, SSM parameter path).
- Update that file (or supply `cdk deploy -c scraper_stage_config_path=/path/to/file.json`) before running `cdk synth`/`cdk deploy`. You can filter stages during deploy with `-c scraper_stages=chalice-dev,chalice-test`.
- CDK creates the state machines, writes their ARNs to SSM parameters, and emits CloudFormation outputs. Chalice should read the ARN for each stage (via config/SSM) and set `SCRAPER_STATE_MACHINE_ARN` accordingly.
- The scheduled scraper still triggers the workflow defined in `chalice_app/step_functions/scraper_state_machine.json` when executed.
- After deployment, verify the integration by running `chalice invoke --name scraper --stage <stage>` or scheduling a CloudWatch Event and confirming the execution appears in the Step Functions console.
- For ad-hoc manual runs, `chalice_app/tests/scripts/invoke_scraper_state_machine.sh` starts an execution using a default CloudWatch scheduled-event payload (override with `--input` if needed).

#### Rendering the State Machine Definition

```bash
cd chalice_app/step_functions

# Option 1: envsubst (requires SCRAPER_LAMBDA_ARN to be exported)
export SCRAPER_LAMBDA_ARN="arn:aws:lambda:ap-southeast-1:123456789012:function:shopping-assistant-api-chalice-test-scraper"
envsubst < scraper_state_machine.json > rendered_scraper_state_machine.json

# Option 2: provided helper script (resolve ARN automatically per stage)
./render_scraper_state_machine.sh --stage chalice-test
./render_scraper_state_machine.sh --stage prod

# Option 3: helper script with explicit ARN
./render_scraper_state_machine.sh "arn:aws:lambda:ap-southeast-1:123456789012:function:shopping-assistant-api-chalice-test-scraper"

# The --stage flag writes rendered_scraper_state_machine.<stage>.json;
# an explicit ARN falls back to rendered_scraper_state_machine.json

# Then create/update the state machine
aws stepfunctions update-state-machine \
  --state-machine-arn "arn:aws:states:ap-southeast-1:123456789012:stateMachine/shopping-assistant-api-chalice-test-scraper" \
  --definition file://rendered_scraper_state_machine.chalice-test.json
```

### Lambda Layer Deployment

The application requires a Lambda layer for dependencies:

```bash
# Build the layer using Docker
bash scripts/build-layer.sh

# Publish to AWS
bash scripts/publish-layer.sh

# Attach to all Chalice functions
bash scripts/attach_layer_to_functions.sh
```

See `chalice_app/PHASE1_SETUP.md` for detailed layer deployment instructions.

## Running Locally

### Local Development Server

Run Chalice locally for development:

```bash
cd chalice_app
chalice local

# API will be available at http://localhost:8000
```

### Local Testing

Run unit tests:

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=chalicelib --cov-report=term-missing

# Run specific test file
python tests/test_retrieval_metrics.py
```

### WebSocket Testing

Test WebSocket functionality:

```bash
cd chalice_app/tests

# Phase 1: Basic connectivity
python test_websocket_basic.py

# Phase 2: Message flow
python test_websocket_flow.py

# Phase 3: Full end-to-end flow
python test_websocket_full.py
```

### Monitoring Logs

Monitor Lambda logs in real-time:

```bash
cd chalice_app/tests

# Follow all logs
./monitor_all_logs.sh follow

# View recent logs (last 5 minutes)
./monitor_all_logs.sh recent

# View logs from last hour
./monitor_all_logs.sh recent 1h
```

## Testing

### Test Structure

```
tests/
├── test_retrieval_metrics.py      # Unit tests for retrieval metrics
chalice_app/tests/
├── test_websocket_basic.py        # WebSocket connectivity tests
├── test_websocket_flow.py         # Message flow tests
├── test_websocket_full.py         # End-to-end flow tests
├── test_layer_imports.py          # Layer dependency verification
└── scripts/
    ├── tail_websocket_logs.sh         # Log monitoring script
    ├── monitor_all_logs.sh            # Multi-log monitoring script
    ├── invoke_scraper_state_machine.sh # Invoke scraper Step Function
    └── tail_scraper_state_machine_logs.sh # Tail scraper Step Function logs
```

### Running Tests

```bash
# Run all tests
pytest tests/ chalice_app/tests/ -v

# Run specific test categories
pytest tests/ -v -m unit

# Run with coverage report
pytest tests/ -v --cov=chalicelib --cov-report=html
```

See `tests/README.md` for detailed testing instructions.

## Project Structure

```
shopping-assistant-agent/
├── chalice_app/                   # Main Chalice application
│   ├── app.py                     # Chalice entry point
│   ├── requirements.txt           # Python dependencies
│   ├── chalicelib/                # Business logic packages
│   │   ├── api/                  # REST and WebSocket route registration
│   │   ├── aws/                  # AWS service helpers (S3, Dynamo)
│   │   ├── core/                 # Configuration, logging, utilities
│   │   ├── ingestion/            # Reddit scraping and preprocessing
│   │   ├── indexers/             # Vector indexing utilities
│   │   ├── jobs/                 # Scheduled/background jobs
│   │   ├── llm/                  # LLM client, metrics, reranker
│   │   ├── models/               # Shared data objects and constants
│   │   ├── services/             # External service integrations (e.g., LangSmith)
│   │   └── sessions/             # Chat session orchestration
│   ├── scripts/                   # Deployment scripts
│   ├── tests/                     # Application tests
│   └── .chalice/                  # Chalice configuration
├── cdk_infrastructure/            # AWS CDK infrastructure
│   ├── __init__.py
│   └── infrastructure_stack.py   # Main stack definition
├── glue_jobs/                     # AWS Glue batch jobs
│   ├── process_top_data.py
│   └── process_top_daily_data.py
├── tests/                         # Unit tests
├── requirements.txt               # Root dependencies
├── requirements_dev.txt           # Development dependencies
├── pyproject.toml                 # Project configuration
└── README.md                      # This file
```

## Key Features

### 1. Real-Time Chat Interface
- WebSocket-based chat for instant responses
- Session management with DynamoDB
- Connection pooling and keep-alive

### 2. Intelligent Product Recommendations
- RAG (Retrieval-Augmented Generation)
- Multi-vector database support (Qdrant, Weaviate)
- LLM reranking for relevance
- Contextual product suggestions

### 3. Data Processing Pipeline
- Daily Reddit scraping
- Batch processing with AWS Glue
- Vector embeddings generation
- Indexing to vector databases

### 4. Performance Monitoring
- LangSmith integration for tracing
- Retrieval metrics tracking
- Execution time measurement
- CloudWatch logging

## Observability & Metrics

The system tracks comprehensive metrics for retrieval quality and response evaluation, all sent to [LangSmith](https://smith.langchain.com/) for real-time monitoring and analysis.

### Retrieval Quality Metrics

These metrics evaluate how well the RAG system retrieves relevant documents. The reranker's relevance scores (threshold ≥ 0.5) serve as pseudo ground truth.

| Metric | Description |
|--------|-------------|
| **Recall@K** (K=5, 10, 15) | Proportion of relevant documents retrieved in top K results |
| **nDCG@K** (K=5, 10, 15) | Normalized Discounted Cumulative Gain - measures ranking quality with position weighting |
| **MRR** | Mean Reciprocal Rank - position of the first relevant document |
| **Hit Rate@K** (K=5, 10, 15) | Binary indicator of whether any relevant document appears in top K |

### Response Quality Metrics

LLM-based evaluations assess the quality of generated responses:

| Metric | Range | Description |
|--------|-------|-------------|
| **Faithfulness** | 0-1 | Whether the response is grounded in the provided Reddit context |
| **Actionability** | 0-1 | How specific and actionable the product recommendations are |
| **Retrieval Relevance** | 0-1 | How relevant the retrieved documents are to the user's query |
| **Overall Score** | 0-1 | Weighted average: 40% Faithfulness + 35% Actionability + 25% Retrieval Relevance |
| **Heuristic Score** | 0-1 | Fast checks: has_products, has_specifics, response_length |

### Session Metrics

Each chat session tracks:

- **Query transformations**: `rewritten_query` (context-aware), `hyde_query` (hypothetical embeddings)
- **Result counts**: `num_rewritten_results`, `num_hyde_results`, `num_combined_results`, `num_reranked_results`
- **Metadata**: `chat_history_length`, `session_id`, `run_id` (LangSmith trace ID)

### How Metrics Flow to LangSmith

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  Chat Session   │────▶│   SQS Queue     │────▶│   Evaluator     │
│  (@traceable)   │     │  (async eval)   │     │   Lambda        │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
                                                         ▼
                                               ┌─────────────────┐
                                               │    LangSmith    │
                                               │  create_feedback│
                                               └─────────────────┘
```

1. **Trace Capture**: The `@traceable(name="chat_session")` decorator captures each chat interaction
2. **Async Evaluation**: Messages are queued to SQS for background evaluation
3. **Feedback Posting**: The evaluator computes metrics and posts them via `langsmith_client.create_feedback()`

### Viewing Metrics

Access the LangSmith dashboard to view:
- Individual trace details with retrieval and generation steps
- Feedback scores (recall, faithfulness, etc.) linked to each trace
- Aggregate metrics over time for quality monitoring

Dashboard: [smith.langchain.com](https://smith.langchain.com/)

## Configuration

Configuration is managed in `chalice_app/chalicelib/config.py`:

```python
# Environment-based config
class AppConfig:
    region: str
    environment: str
    log_level: str
    
    # Database URLs
    qdrant_url: str
    weaviate_url: str
    
    # API Keys
    openai_api_key: str
    anthropic_api_key: str
    langsmith_api_key: str
    
    # AWS Resources
    sessions_table_name: str
    chat_processing_queue_url: str
    websocket_domain: str
```

### CDK Context

Infrastructure resource names (buckets, DynamoDB tables, Glue database/table, etc.) are loaded from CDK context. The defaults live in `cdk.json` under `context.infrastructure`:

```json
{
  "context": {
    "infrastructure": {
      "raw_reddit_data_bucket_name": "shopping-assistant-raw-reddit-data",
      "raw_reddit_test_data_bucket_name": "shopping-assistant-raw-test-reddit-data",
      "processed_reddit_data_bucket_name": "shopping-assistant-processed-reddit-data",
      "processed_reddit_test_data_bucket_name": "shopping-assistant-processed-test-reddit-data",
      "glue_scripts_bucket_name": "shopping-assistant-glue-scripts",
      "reddit_posts_table_name": "reddit-posts",
      "reddit_posts_test_table_name": "reddit-posts-test",
      "glue_database_name": "reddit_data",
      "glue_table_name": "reddit_data_table",
      "alerts_email_address": "your-alerts@example.com"
    }
  }
}
```

- Adjust the values prior to `cdk synth`/`cdk deploy` if you need different resource names.
- To manage multiple environments, add `defaults` and an `environments` map inside `context.infrastructure`, then deploy with `cdk deploy -c infrastructure_env=<env>` to select the override.
- Missing keys cause deployment to fail early with a descriptive error.

## CI/CD

The project uses GitHub Actions for continuous integration:

- **Code Quality**: Black, Ruff, Bandit, MyPy
- **Testing**: Pytest with coverage
- **Deployment**: Automated deployments on merge to main
- **Stable API Gateway IDs**: the workflow restores
  `chalice_app/.chalice/deployed/<stage>.json` from
  `s3://shopping-assistant-layers-ap-southeast-1/chalice-state/<stage>.json`
  before running the cleanup step, and persists it back after a successful
  Chalice deploy. This stops Chalice from minting a new WebSocket API on
  every run. See *Architecture Decisions → CI/CD: persist Chalice deployed
  state in S3* for the full rationale.

See `.github/workflows/` for CI/CD configuration.

## Code Quality

Maintain code quality with:

```bash
# Format code
black chalice_app/ cdk_infrastructure/ glue_jobs/

# Lint and auto-fix
ruff check --fix chalice_app/ cdk_infrastructure/ glue_jobs/

# Security scan
bandit -r chalice_app/ cdk_infrastructure/ glue_jobs/

# Type checking
mypy chalice_app/ cdk_infrastructure/
```

See `CODE_QUALITY.md` for detailed instructions.

## Monitoring and Debugging

### CloudWatch Logs

View Lambda logs:

```bash
# WebSocket handlers
aws logs tail /aws/lambda/shopping-assistant-api-chalice-test-websocket_message --follow

# Chat processor
aws logs tail /aws/lambda/shopping-assistant-api-chalice-test-chat_processor --follow
```

### LangSmith Tracing

The application integrates with LangSmith for AI tracing:
- LLM calls
- Retrieval operations
- Reranking steps

View traces at [LangSmith Dashboard](https://smith.langchain.com/).

## Troubleshooting

### Common Issues

1. **Import Errors in Lambda**
   - Ensure layer is built and attached: `bash scripts/attach_layer_to_functions.sh`
   - Check layer ARN in `.chalice/layer-arn.txt`

2. **WebSocket Connection Failures**
   - Verify IAM permissions for `execute-api:ManageConnections`
   - Check `WEBSOCKET_DOMAIN` and `WEBSOCKET_STAGE` environment variables

3. **Vector Database Connection Issues**
   - Verify QDRANT_URL and WEAVIATE_URL
   - Check network connectivity from Lambda VPC

4. **Chat Processing Timeouts**
   - Increase Lambda timeout in `.chalice/config.json`
   - Optimize retrieval queries
   - Check queue backlog

See `chalice_app/tests/WEBSOCKET_TEST_SUMMARY.md` for known issues and solutions.

## Migration Notes

This project was migrated from a CDK-managed Lambda architecture to Chalice. See `MIGRATION_COMPLETE.md` for migration details.

Key changes:
- All Lambda functions now managed by Chalice
- Direct business logic execution (no Lambda invocations)
- Unified deployment with `chalice deploy`
- All code in `chalicelib/`

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests and code quality checks
5. Submit a pull request

## License

[Add your license information here]

## Support

For issues and questions:
- GitHub Issues: [repository-issues-url]
- Email: vodangkhoa@gmail.com

---

**Built with ❤️ using AWS Chalice, LangChain, and modern AI technologies**
