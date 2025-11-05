import json
from pathlib import Path

from aws_cdk import (
    Stack,
    aws_lambda as lambda_,
    aws_s3 as s3,
    aws_glue as glue,
    aws_iam as iam,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_dynamodb as dynamodb,
    aws_s3_deployment as s3_deployment,
    aws_sqs as sqs,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cloudwatch_actions,
    aws_ec2 as ec2,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as sfn_tasks,
    aws_ssm as ssm,
    aws_logs as logs,
)
from constructs import Construct
import time

# S3 bucket names
RAW_REDDIT_DATA_BUCKET_NAME = "shopping-assistant-raw-reddit-data"
RAW_REDDIT_TEST_DATA_BUCKET_NAME = "shopping-assistant-raw-test-reddit-data"

PROCESSED_REDDIT_DATA_BUCKET_NAME = "shopping-assistant-processed-reddit-data"
PROCESSED_REDDIT_TEST_DATA_BUCKET_NAME = "shopping-assistant-processed-test-reddit-data"
GLUE_SCRIPTS_BUCKET_NAME = "shopping-assistant-glue-scripts"

# DynamoDB configurations
REDDIT_POSTS_TABLE_NAME = "reddit-posts"
REDDIT_POSTS_TEST_TABLE_NAME = "reddit-posts-test"

# Define a constant for the Lambda runtime
LAMBDA_RUNTIME = lambda_.Runtime.PYTHON_3_9

# Define constants for Glue resources
GLUE_DATABASE_NAME = "reddit_data"
GLUE_TABLE_NAME = "reddit_data_table"

# Define constant for alerts email
ALERTS_EMAIL_ADDRESS = "vodangkhoa@gmail.com"  # Replace with your actual email address


DEFAULT_SCRAPER_STAGE_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "infrastructure"
    / "config"
    / "scraper_step_functions.json"
)


class ShoppingAssistantInfrastructureStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # 1. Create VPC and networking first
        self.vpc = ec2.Vpc(
            self,
            "RedditScraperVPC",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                )
            ],
        )

        # 2. Create IAM roles and policies
        self.lambda_role = iam.Role(
            self,
            "RedditScraperLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        )

        # Create SNS topic for alerts
        self.alerts_topic = self.create_alerts_topic()

        # Add email subscription to the alerts topic
        self.add_email_subscription_to_alerts_topic(ALERTS_EMAIL_ADDRESS)

        # Initialize S3 buckets first
        self.raw_data_bucket = self.create_s3_bucket(
            bucket_name=RAW_REDDIT_DATA_BUCKET_NAME, id="RawRedditData"
        )
        self.raw_test_data_bucket = self.create_s3_bucket(
            bucket_name=RAW_REDDIT_TEST_DATA_BUCKET_NAME, id="RawRedditTestData"
        )
        self.processed_data_bucket = self.create_s3_bucket(
            bucket_name=PROCESSED_REDDIT_DATA_BUCKET_NAME, id="ProcessedRedditData"
        )
        self.processed_test_data_bucket = self.create_s3_bucket(
            bucket_name=PROCESSED_REDDIT_TEST_DATA_BUCKET_NAME,
            id="ProcessedRedditTestData",
        )

        # Create the Glue scripts bucket
        self.glue_scripts_bucket = self.create_s3_bucket(
            bucket_name=GLUE_SCRIPTS_BUCKET_NAME, id="GlueScriptsBucket"
        )

        # Deploy Glue scripts to S3
        self.deploy_glue_scripts()

        # Create Athena results bucket before Lambda functions
        self.athena_results_bucket = self.create_athena_results_bucket()

        self.dependencies_layer = (
            None  # Placeholder - not used by Chalice-managed functions
        )

        self.reddit_database = self.create_glue_database()
        self.create_glue_crawler(database=self.reddit_database)
        self.create_processed_data_glue_crawler(database=self.reddit_database)

        # Output the Glue database name
        CfnOutput(
            self,
            id="GlueDatabaseName",
            value=self.reddit_database.ref,
            description="The name of the Glue database for Reddit data",
        )

        # Output the Glue table name
        CfnOutput(
            self,
            id="GlueTableName",
            value="merged_data",
            description="The name of the Glue table for Reddit data",
        )

        self.create_dynamodb_table(table_name=REDDIT_POSTS_TABLE_NAME, id="RedditPosts")
        self.create_dynamodb_table(
            table_name=REDDIT_POSTS_TEST_TABLE_NAME, id="RedditPostsTest"
        )

        self.create_glue_job()
        self.create_process_top_data_glue_job()

        # Create Step Functions workflows for the Chalice-managed scraper Lambdas
        self.create_scraper_state_machines()

        # Create the Glue table with the specified schema
        self.create_glue_table(database=self.reddit_database)

        # Create Sessions Table first
        self.sessions_table = dynamodb.Table(
            self,
            "SessionsTable",
            table_name="SessionsTable",
            partition_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="expiry_time",
            removal_policy=RemovalPolicy.DESTROY,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        # Create new SessionsTableV2 with id as primary key
        self.sessions_table_v2 = dynamodb.Table(
            self,
            "SessionsTableV2",
            table_name="SessionsTableV2",
            partition_key=dynamodb.Attribute(
                name="id", type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="expiry_time",
            removal_policy=RemovalPolicy.DESTROY,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        # Create SQS queues for Chalice Lambda handlers
        # These queues are used by Chalice functions for async message processing
        self.chat_processing_queue = sqs.Queue(
            self,
            "ChatProcessingQueue",
            queue_name="ChatProcessingQueue",
            visibility_timeout=Duration.seconds(900),  # 15 minutes
            retention_period=Duration.days(1),
        )

        self.evaluation_queue = sqs.Queue(
            self,
            "EvaluationQueue",
            queue_name="shopping-assistant-evaluation-queue",
            visibility_timeout=Duration.seconds(300),  # 5 min for evaluation processing
            retention_period=Duration.days(7),
        )

        # Create WebSocket connections table for Chalice WebSocket handlers
        self.connections_table = dynamodb.Table(
            self,
            "WebSocketConnectionsV2",
            table_name="WebSocketConnectionsV2",
            partition_key=dynamodb.Attribute(
                name="id", type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

    def create_alerts_topic(self) -> sns.Topic:
        """Create an SNS topic for job failure alerts."""
        topic = sns.Topic(
            self,
            "JobFailureAlertsTopic",
            display_name="Job Failure Alerts",
            topic_name="job-failure-alerts",
        )

        # Output the SNS topic ARN
        CfnOutput(
            self,
            id="AlertsTopicArn",
            value=topic.topic_arn,
            description="ARN of the SNS topic for job failure alerts",
        )

        return topic

    def add_email_subscription_to_alerts_topic(self, email_address: str):
        """Add an email subscription to the alerts topic."""
        # Add a confirmation log so you know this is being created properly
        print(f"Creating email subscription for alerts to: {email_address}")

        self.alerts_topic.add_subscription(
            sns_subscriptions.EmailSubscription(email_address)
        )

        # Output the email address that will receive alerts
        CfnOutput(
            self,
            id="AlertsEmailAddress",
            value=email_address,
            description="Email address that will receive job failure alerts - CHECK CONFIRMATION STATUS",
        )

    def create_lambda_failure_alarm(
        self, lambda_function: lambda_.Function, alarm_name_suffix: str
    ):
        """Create a CloudWatch alarm for Lambda function failures."""
        # Generate a timestamp for unique alarm names
        timestamp = int(time.time())

        # Use the logical ID of the Lambda function for a consistent ID
        lambda_logical_id = Stack.of(lambda_function).get_logical_id(
            lambda_function.node.default_child
        )
        alarm_id = f"LambdaFailureAlarm-{lambda_logical_id}"

        # Add timestamp to alarm name to ensure uniqueness
        alarm_name = f"{lambda_function.function_name}-{alarm_name_suffix}-{timestamp}"

        # Create the alarm using CfnAlarm for more control
        alarm = cloudwatch.CfnAlarm(
            self,
            alarm_id,
            alarm_name=alarm_name,
            metric_name="Errors",
            namespace="AWS/Lambda",
            dimensions=[
                cloudwatch.CfnAlarm.DimensionProperty(
                    name="FunctionName", value=lambda_function.function_name
                )
            ],
            statistic="Sum",
            period=300,  # 5 minutes
            evaluation_periods=1,
            threshold=1,
            comparison_operator="GreaterThanOrEqualToThreshold",
            treat_missing_data="notBreaching",
            alarm_description=f"Alarm when {lambda_function.function_name} has errors",
            alarm_actions=[self.alerts_topic.topic_arn],
        )

        return alarm

    def create_s3_bucket(self, *, bucket_name: str, id: str) -> s3.Bucket:
        return s3.Bucket(
            self,
            id=id,
            bucket_name=bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=True,
        )

    def create_glue_database(self) -> glue.CfnDatabase:
        return glue.CfnDatabase(
            self,
            id="RedditDatabase",
            catalog_id=Stack.of(self).account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=GLUE_DATABASE_NAME, description="Database for Reddit scraping data"
            ),
        )

    def create_glue_crawler(self, *, database: glue.CfnDatabase):
        crawler_role = iam.Role(
            self,
            id="GlueCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
        )
        crawler_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSGlueServiceRole"
            )
        )
        self.raw_data_bucket.grant_read(crawler_role)

        crawler = glue.CfnCrawler(
            self,
            id="RedditDataCrawler",
            name="reddit-data-crawler",
            role=crawler_role.role_arn,
            database_name=database.ref,
            targets={
                "s3Targets": [
                    {"path": f"s3://{self.raw_data_bucket.bucket_name}/top_posts"}
                ]
            },
            schedule={"scheduleExpression": "cron(0 1 * * ? *)"},
        )

        # Output the name of the Glue Crawler
        CfnOutput(
            self,
            id="RedditDataCrawlerName",
            value=crawler.name,
            description="The name of the Glue Crawler for Reddit data",
        )

    def create_processed_data_glue_crawler(self, *, database: glue.CfnDatabase):
        crawler_role = iam.Role(
            self,
            id="ProcessedDataGlueCrawlerRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
        )
        crawler_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AWSGlueServiceRole"
            )
        )
        self.processed_data_bucket.grant_read(crawler_role)

        processed_crawler = glue.CfnCrawler(
            self,
            id="ProcessedRedditDataCrawler",
            name="processed-reddit-data-crawler",
            role=crawler_role.role_arn,
            database_name=database.ref,
            targets={
                "s3Targets": [
                    {"path": f"s3://{self.processed_data_bucket.bucket_name}/"}
                ]
            },
            schedule={"scheduleExpression": "cron(0 2 * * ? *)"},
        )

        # Output the name of the Processed Data Glue Crawler
        CfnOutput(
            self,
            id="ProcessedRedditDataCrawlerName",
            value=processed_crawler.name,
            description="The name of the Glue Crawler for processed Reddit data",
        )

    def create_dynamodb_table(self, *, table_name: str, id: str):
        dynamodb.Table(
            self,
            id=id,
            table_name=table_name,
            partition_key=dynamodb.Attribute(
                name="subreddit", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="post_id", type=dynamodb.AttributeType.STRING
            ),
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )
        # NOTE: Lambda migrated to Chalice - permissions now handled in Chalice IAM policy

    def grant_dynamodb_permissions(self, lambda_function: lambda_.Function):
        # Grant the Lambda function permission to put items in the DynamoDB table
        lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                actions=["dynamodb:PutItem"],
                resources=[
                    f"arn:aws:dynamodb:{self.region}:{self.account}:table/{REDDIT_POSTS_TABLE_NAME}"
                ],
            )
        )

    def create_glue_job(self):
        # Define an IAM role for the Glue job
        glue_role = iam.Role(
            self,
            "GlueJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSGlueServiceRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
            ],
        )

        # Grant specific S3 read permissions to the Glue role
        script_bucket_arn = f"arn:aws:s3:::{GLUE_SCRIPTS_BUCKET_NAME}/*"
        glue_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[script_bucket_arn],
            )
        )

        # Define the Glue job
        glue_job = glue.CfnJob(
            self,
            "Process-Top-Daily-Data-Job",
            name="Daily-Reddit-Data-Process-Top-Job",
            role=glue_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                script_location=f"s3://{GLUE_SCRIPTS_BUCKET_NAME}/process_top_daily_data.py",
                python_version="3",
            ),
            default_arguments={
                "--job-language": "python",
                "--enable-metrics": "",
                "--enable-continuous-cloudwatch-log": "true",
                "--extra-py-files": f"s3://{GLUE_SCRIPTS_BUCKET_NAME}/glue_constants.py",
            },
            glue_version="5.0",  # Specify the Glue version
            max_retries=1,
            timeout=2880,  # Timeout in minutes
        )

        # Create an alarm for this Glue job
        self.create_glue_job_failure_alarm(glue_job_name=glue_job.ref)

        # NOTE: Glue job scheduling removed - jobs can be triggered manually or via other means

    def create_process_top_data_glue_job(self):
        # Define an IAM role for the Glue job
        glue_role = iam.Role(
            self,
            "ProcessTopDataGlueJobRole",
            assumed_by=iam.ServicePrincipal("glue.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSGlueServiceRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
            ],
        )

        # Grant specific S3 read permissions to the Glue role
        script_bucket_arn = f"arn:aws:s3:::{GLUE_SCRIPTS_BUCKET_NAME}/*"
        glue_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[script_bucket_arn],
            )
        )

        # Define the Glue job
        glue_job = glue.CfnJob(
            self,
            "Process-Top-Data-Job",
            name="Oncee-Reddit-Data-Process-Top-Job",
            role=glue_role.role_arn,
            command=glue.CfnJob.JobCommandProperty(
                name="glueetl",
                script_location=f"s3://{GLUE_SCRIPTS_BUCKET_NAME}/process_top_data.py",
                python_version="3",
            ),
            default_arguments={
                "--job-language": "python",
                "--enable-metrics": "",
                "--enable-continuous-cloudwatch-log": "true",
                "--extra-py-files": f"s3://{GLUE_SCRIPTS_BUCKET_NAME}/glue_constants.py",
            },
            glue_version="5.0",  # Specify the Glue version
            max_retries=1,
            timeout=2880,  # Timeout in minutes
        )

        # Create an alarm for this Glue job
        self.create_glue_job_failure_alarm(glue_job_name=glue_job.ref)

    def create_outputs(self):
        CfnOutput(
            self,
            id="RawDataBucketName",
            value=self.raw_data_bucket.bucket_name,
            description="Raw data S3 bucket name",
        )
        CfnOutput(
            self,
            id="ProcessedDataBucketName",
            value=self.processed_data_bucket.bucket_name,
            description="Processed data S3 bucket name",
        )
        CfnOutput(
            self,
            id="ProcessedTestDataBucketName",
            value=self.processed_test_data_bucket.bucket_name,
            description="Processed test data S3 bucket name",
        )
        CfnOutput(
            self,
            id="AthenaResultsBucketName",
            value=self.athena_results_bucket.bucket_name,
            description="S3 bucket for Athena query results",
        )

    def create_scraper_state_machines(self):
        stage_configs = self._load_scraper_stage_configs()
        stages = self._determine_scraper_stages(stage_configs)

        known_stages = ", ".join(stage_configs.keys())

        for stage in stages:
            config = stage_configs.get(stage)
            if config is None:
                raise ValueError(
                    f"Unsupported scraper stage '{stage}'. Known stages: {known_stages}"
                )

            log_group = logs.LogGroup(
                self,
                f"{self._pascal_case(stage)}ScraperLogGroup",
                log_group_name=f"/aws/vendedlogs/states/{config['state_machine_name']}",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            )

            lambda_function = lambda_.Function.from_function_name(
                self,
                f"{self._pascal_case(stage)}ScraperLambda",
                function_name=config["lambda_function_name"],
            )

            lambda_task = sfn_tasks.LambdaInvoke(
                self,
                f"{self._pascal_case(stage)}ScraperTask",
                lambda_function=lambda_function,
                payload=sfn.TaskInput.from_json_path_at("$"),
                payload_response_only=True,
            )
            lambda_task.add_retry(
                errors=["States.Timeout"],
                interval=Duration.seconds(5),
                max_attempts=3,
                backoff_rate=2.0,
            )

            state_machine = sfn.StateMachine(
                self,
                f"{self._pascal_case(stage)}ScraperStateMachine",
                definition_body=sfn.DefinitionBody.from_chainable(lambda_task),
                state_machine_name=config["state_machine_name"],
                state_machine_type=sfn.StateMachineType.STANDARD,
                timeout=Duration.hours(2),
                logs=sfn.LogOptions(
                    destination=log_group,
                    level=sfn.LogLevel.ALL,
                    include_execution_data=True,
                ),
            )

            lambda_function.grant_invoke(state_machine.role)

            ssm.StringParameter(
                self,
                f"{self._pascal_case(stage)}ScraperStateMachineParameter",
                parameter_name=config["ssm_parameter"],
                string_value=state_machine.state_machine_arn,
            )

            CfnOutput(
                self,
                id=f"{self._pascal_case(stage)}ScraperStateMachineArn",
                value=state_machine.state_machine_arn,
                description=f"State machine ARN for {stage} scraper workflow",
            )

    def _pascal_case(self, value: str) -> str:
        return "".join(
            part.capitalize()
            for part in value.replace("-", " ").replace("_", " ").split()
            if part
        )

    def _load_scraper_stage_configs(self) -> dict[str, dict[str, str]]:
        context_path = self.node.try_get_context("scraper_stage_config_path")
        if context_path is None:
            config_path = DEFAULT_SCRAPER_STAGE_CONFIG_PATH
        else:
            resolved = Path(str(context_path)).expanduser()
            if not resolved.is_absolute():
                resolved = Path.cwd() / resolved
            config_path = resolved

        if not config_path.exists():
            raise FileNotFoundError(
                f"Scraper stage config not found at {config_path}. "
                "Provide --context scraper_stage_config_path=<path> or add the default file."
            )

        try:
            with config_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in scraper stage config {config_path}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise ValueError(
                f"Expected scraper stage config {config_path} to contain an object at the top level."
            )

        required_keys = {
            "lambda_function_name",
            "state_machine_name",
            "ssm_parameter",
        }
        optional_keys = {"state_machine_arn", "region", "account"}
        validated: dict[str, dict[str, str]] = {}
        for stage, config in data.items():
            if not isinstance(config, dict):
                raise ValueError(
                    f"Stage '{stage}' in {config_path} must be a JSON object with configuration details."
                )

            missing = required_keys - config.keys()
            if missing:
                raise ValueError(
                    f"Stage '{stage}' is missing required keys: {', '.join(sorted(missing))}"
                )

            entry: dict[str, str] = {key: str(config[key]) for key in required_keys}

            for key in optional_keys & config.keys():
                entry[key] = str(config[key])

            validated[stage] = entry

        if not validated:
            raise ValueError(
                f"Scraper stage config {config_path} does not define any stages."
            )

        return validated

    def _determine_scraper_stages(
        self, configs: dict[str, dict[str, str]]
    ) -> list[str]:
        stages_context = self.node.try_get_context("scraper_stages")

        if stages_context is None:
            return list(configs.keys())

        if isinstance(stages_context, str):
            stages = [
                stage.strip() for stage in stages_context.split(",") if stage.strip()
            ]
        elif isinstance(stages_context, (list, tuple)):
            stages = [
                str(stage).strip() for stage in stages_context if str(stage).strip()
            ]
        else:
            raise ValueError(
                "scraper_stages context must be a comma-separated string or list of stage names"
            )

        if not stages:
            raise ValueError("scraper_stages context did not specify any stages")

        unknown = [stage for stage in stages if stage not in configs]
        if unknown:
            known_stages = ", ".join(configs.keys())
            raise ValueError(
                "scraper_stages context includes unknown stages: "
                f"{', '.join(unknown)}. Known stages: {known_stages}"
            )

        return stages

    def deploy_glue_scripts(self):
        s3_deployment.BucketDeployment(
            self,
            id="DeployGlueScripts",
            sources=[s3_deployment.Source.asset("glue_scripts")],
            destination_bucket=self.glue_scripts_bucket,
            destination_key_prefix="",  # Optional: specify a prefix if needed
        )

    def create_athena_results_bucket(self) -> s3.Bucket:
        return s3.Bucket(
            self,
            id="AthenaQueryResultsBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=False,
        )

    def create_glue_table(self, *, database: glue.CfnDatabase):
        glue.CfnTable(
            self,
            id="RedditDataTable",
            catalog_id=Stack.of(self).account,
            database_name=database.ref,
            table_input=glue.CfnTable.TableInputProperty(
                name=GLUE_TABLE_NAME,
                description="Table for Reddit data",
                storage_descriptor=glue.CfnTable.StorageDescriptorProperty(
                    columns=[
                        glue.CfnTable.ColumnProperty(
                            name="comments",
                            type="array<struct<id:string,score:int,body:string,year:int,month:int>>",
                        ),
                        glue.CfnTable.ColumnProperty(name="content", type="string"),
                        glue.CfnTable.ColumnProperty(name="id", type="string"),
                        glue.CfnTable.ColumnProperty(name="month", type="int"),
                        glue.CfnTable.ColumnProperty(
                            name="original_title", type="string"
                        ),
                        glue.CfnTable.ColumnProperty(name="score", type="int"),
                        glue.CfnTable.ColumnProperty(name="title", type="string"),
                        glue.CfnTable.ColumnProperty(name="url", type="string"),
                        glue.CfnTable.ColumnProperty(name="year", type="int"),
                    ],
                    location=f"s3://{self.processed_data_bucket.bucket_name}/merged_data/",
                    input_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                    output_format="org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                    serde_info=glue.CfnTable.SerdeInfoProperty(
                        serialization_library="org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
                    ),
                ),
                partition_keys=[
                    glue.CfnTable.ColumnProperty(name="created_at_year", type="string"),
                    glue.CfnTable.ColumnProperty(
                        name="created_at_month", type="string"
                    ),
                    glue.CfnTable.ColumnProperty(name="created_at_day", type="string"),
                    glue.CfnTable.ColumnProperty(name="subreddit_name", type="string"),
                ],
                table_type="EXTERNAL_TABLE",
                parameters={
                    "classification": "parquet",
                    "compressionType": "none",
                    "typeOfData": "file",
                },
            ),
        )

    def _create_base_index_lambda(
        self,
        *,
        id: str,
        handler: str,
        function_name: str,
    ) -> lambda_.Function:
        """Create a base Lambda function with common configuration."""
        return lambda_.Function(
            self,
            id=id,
            runtime=LAMBDA_RUNTIME,
            code=lambda_.Code.from_asset("lambda"),
            handler=handler,
            timeout=Duration.minutes(15),
            memory_size=512,
            environment={
                "ENVIRONMENT": "prod",
                "ATHENA_OUTPUT_BUCKET": self.athena_results_bucket.bucket_name,
                "ATHENA_DATABASE": GLUE_DATABASE_NAME,
                "TABLE_NAME": "merged_data",
                "LOG_LEVEL": "INFO",
            },
            layers=[self.dependencies_layer] if self.dependencies_layer else [],
            function_name=function_name,
            architecture=lambda_.Architecture.X86_64,
        )

    def _add_common_lambda_permissions(self, lambda_function: lambda_.Function):
        """Add common permissions to the Lambda function."""
        # Add S3 permissions
        lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetBucketLocation",
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:PutObject",
                ],
                resources=[
                    self.raw_data_bucket.bucket_arn,
                    f"{self.raw_data_bucket.bucket_arn}/*",
                    self.processed_data_bucket.bucket_arn,
                    f"{self.processed_data_bucket.bucket_arn}/*",
                    self.processed_test_data_bucket.bucket_arn,
                    f"{self.processed_test_data_bucket.bucket_arn}/*",
                    self.athena_results_bucket.bucket_arn,
                    f"{self.athena_results_bucket.bucket_arn}/*",
                ],
            )
        )

        # Add Secrets Manager permissions
        secret_arn = self.format_arn(
            service="secretsmanager", resource="secret:prod/shopping-assistant/app*"
        )
        lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["secretsmanager:GetSecretValue"],
                resources=[secret_arn],
            )
        )

        # Add Athena permissions
        lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                    "athena:ListQueryExecutions",
                    "athena:BatchGetQueryExecution",
                    "athena:GetWorkGroup",
                    "athena:ListWorkGroups",
                    "glue:GetTable",
                    "glue:GetPartitions",
                    "glue:GetDatabase",
                    "glue:GetDatabases",
                    "glue:GetTables",
                ],
                resources=[
                    f"arn:aws:athena:{self.region}:{self.account}:workgroup/primary",
                    f"arn:aws:glue:{self.region}:{self.account}:catalog",
                    f"arn:aws:glue:{self.region}:{self.account}:database/{GLUE_DATABASE_NAME}",
                    f"arn:aws:glue:{self.region}:{self.account}:database/default",
                    f"arn:aws:glue:{self.region}:{self.account}:table/{GLUE_DATABASE_NAME}/*",
                    f"arn:aws:glue:{self.region}:{self.account}:table/default/*",
                ],
            )
        )

        # Add OpenSearch permissions
        lambda_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "aoss:APIAccessAll",
                    "aoss:DashboardsAccessAll",
                    "aoss:CreateIndex",
                    "aoss:DeleteIndex",
                    "aoss:UpdateIndex",
                    "aoss:DescribeIndex",
                    "aoss:ReadDocument",
                    "aoss:WriteDocument",
                ],
                resources=[f"arn:aws:aoss:{self.region}:{self.account}:collection/*"],
            )
        )

    def create_glue_job_failure_alarm(self, glue_job_name: str):
        """Create a CloudWatch alarm for Glue job failures."""
        # Generate a timestamp for unique alarm names
        timestamp = int(time.time())

        # Use a sanitized version of the job name for the ID
        sanitized_name = "".join(c if c.isalnum() else "-" for c in glue_job_name)

        # Create multiple alarms with different metrics to catch various failure modes

        # 1. Alarm for failed tasks
        task_alarm = cloudwatch.Alarm(
            self,
            f"GlueFailedTasksAlarm-{sanitized_name}",
            alarm_name=f"{glue_job_name}-failed-tasks-alarm-{timestamp}",
            metric=cloudwatch.Metric(
                namespace="Glue",
                metric_name="glue.driver.aggregate.numFailedTasks",
                dimensions_map={"JobName": glue_job_name},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Alarm when {glue_job_name} has failed tasks",
        )

        # 2. Alarm for job failures (generic metric)
        job_alarm = cloudwatch.Alarm(
            self,
            f"GlueJobFailureAlarm-{sanitized_name}",
            alarm_name=f"{glue_job_name}-job-failure-alarm-{timestamp}",
            metric=cloudwatch.Metric(
                namespace="AWS/Glue",
                metric_name="JobFailure",
                dimensions_map={"JobName": glue_job_name},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Alarm when {glue_job_name} job fails",
        )

        # 3. Alarm for job timeouts
        timeout_alarm = cloudwatch.Alarm(
            self,
            f"GlueTimeoutAlarm-{sanitized_name}",
            alarm_name=f"{glue_job_name}-timeout-alarm-{timestamp}",
            metric=cloudwatch.Metric(
                namespace="AWS/Glue",
                metric_name="Timeout",
                dimensions_map={"JobName": glue_job_name},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=f"Alarm when {glue_job_name} job times out",
        )

        # Add actions to all alarms
        task_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alerts_topic))
        job_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alerts_topic))
        timeout_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alerts_topic))

        return [task_alarm, job_alarm, timeout_alarm]
