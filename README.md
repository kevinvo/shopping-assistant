# Shopping Assistant Agent

![CI/CD](https://github.com/kevinvo/shopping-assistant/workflows/CI/CD/badge.svg)

An intelligent shopping assistant powered by AI that helps users discover products through Reddit discussions and recommendations. The system processes Reddit data, indexes it in vector databases, and provides real-time chat-based product recommendations.

## Architecture Overview

The application is built on AWS using a serverless architecture with the following components:

### Tech Stack

- **Backend Framework**: [AWS Chalice](https://github.com/aws/chalice) (Python serverless framework)
- **Infrastructure**: AWS CDK (TypeScript/CloudFormation)
- **Vector Databases**: Qdrant and Weaviate
- **LLM Providers**: OpenAI and Anthropic
- **AI Orchestration**: LangChain
- **Real-time Communication**: WebSocket API via API Gateway
- **Data Processing**: AWS Glue, Athena
- **Queue Management**: SQS
- **Storage**: S3, DynamoDB

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
│  • Metadata: DynamoDB (sessions, posts tracking)                │
│  • Query Engine: Athena (data lake queries)                     │
│  • LLMs: OpenAI (GPT-4), Anthropic (Claude)                     │
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
   - Chat processing with RAG
   - Vector indexing and retrieval
   - LLM integration and reranking
   - Session and connection management

4. **Glue Jobs** (`glue_jobs/`): Batch data processing
   - Reddit data transformation
   - Top posts processing
   - Daily aggregation jobs

## Prerequisites

- **Python**: 3.9+
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
bash scripts/attach-layer-to-functions.sh
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
│   ├── chalicelib/                # Business logic
│   │   ├── handlers/              # Route handlers
│   │   │   ├── rest.py           # REST API endpoints
│   │   │   ├── scheduled.py      # Scheduled tasks
│   │   │   └── sqs.py            # SQS consumers
│   │   ├── websocket.py          # WebSocket handlers
│   │   ├── chat.py               # Chat processing logic
│   │   ├── chat_processor.py     # Message processor
│   │   ├── llm.py                # LLM integration
│   │   ├── indexers/             # Vector indexing
│   │   ├── reranker/             # LLM reranking
│   │   ├── config.py             # Configuration
│   │   └── ...                   # Other modules
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
   - Ensure layer is built and attached: `bash scripts/attach-layer-to-functions.sh`
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
