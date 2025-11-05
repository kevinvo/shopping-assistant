from pyspark.sql import SparkSession
from pyspark.sql.types import (
    StructType,
    StructField,
    StringType,
    IntegerType,
    ArrayType,
)
import boto3
import logging
from datetime import datetime, timedelta, timezone
from glue_constants import (
    RAW_REDDIT_DATA_BUCKET_NAME,
    PROCESSED_REDDIT_DATA_BUCKET_NAME,
    GLUE_DATABASE_NAME,
    GLUE_TABLE_NAME,
    SUBREDDIT_NAMES,
)

# Initialize logging for AWS Glue (corrected configuration)
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Remove all custom handler configuration - Glue manages this automatically
# CloudWatch requires special handling through Glue's built-in mechanisms
logger.info("Starting process_top_data.py Glue job")

# Initialize Spark with AWS Hadoop configuration
logger.info("Initializing Spark session")
spark_builder = SparkSession.builder  # type: ignore
spark = (
    spark_builder.appName("RedditShoppingAssistantProcessing")
    .config(
        "spark.driver.extraJavaOptions",
        "-Dlog4j.configuration=file:/opt/glue/spark/conf/log4j.properties",
    )
    .config(
        "spark.executor.extraJavaOptions",
        "-Dlog4j.configuration=file:/opt/glue/spark/conf/log4j.properties",
    )
    .config("spark.ui.showConsoleProgress", "false")
    .config("spark.hadoop.fs.s3a.impl", "org.apache.hadoop.fs.s3a.S3AFileSystem")
    .config(
        "spark.hadoop.fs.s3a.aws.credentials.provider",
        "com.amazonaws.auth.DefaultAWSCredentialsProviderChain",
    )
    .getOrCreate()
)

# Disable all Spark context logging
spark.sparkContext.setLogLevel("OFF")
logging.getLogger("pyspark").setLevel(logging.CRITICAL)
logging.getLogger("py4j").setLevel(logging.CRITICAL)

# Set AWS credentials from environment
logger.info("Setting AWS credentials")
session = boto3.Session()
credentials = session.get_credentials()
spark.conf.set("spark.hadoop.fs.s3a.access.key", credentials.access_key)
spark.conf.set("spark.hadoop.fs.s3a.secret.key", credentials.secret_key)
if credentials.token:
    spark.conf.set("spark.hadoop.fs.s3a.session.token", credentials.token)
logger.info("AWS credentials set successfully")

# Set the logging level for Spark
sc = spark.sparkContext
sc.setLogLevel("INFO")  # Change from ALL to INFO for better readability

# Define S3 paths
logger.info(f"Using raw data bucket: {RAW_REDDIT_DATA_BUCKET_NAME}")
logger.info(f"Using processed data bucket: {PROCESSED_REDDIT_DATA_BUCKET_NAME}")

shopping_assistant_processed_reddit_data = "shopping-assistant-processed-reddit-data"
shopping_assistant_raw_reddit_data = "shopping-assistant-raw-reddit-data"

top_post_s3_path = (
    f"s3a://{shopping_assistant_raw_reddit_data}/top_posts/subreddit_name=*/*"
)

# Log the S3 paths being processed
logger.info("Processing the following S3 paths for top posts:")
for path in top_post_s3_path:
    logger.info(path)

output_s3_path = f"s3a://{shopping_assistant_processed_reddit_data}/merged_data/"

# Calculate the dates for the last 2 days
today = datetime.now(timezone.utc)
last_2_days = [today - timedelta(days=i) for i in range(2)]
logger.info(
    f"Processing data for dates: {', '.join(day.date().isoformat() for day in last_2_days)}"
)

# Format the dates to match the S3 path structure
date_paths = [
    f"created_at_year={day.year}/created_at_month={day.month}/created_at_day={day.day}"
    for day in last_2_days
]
logger.info(f"Date paths: {date_paths}")

# Initialize S3 client
s3_client = boto3.client("s3")
logger.info("S3 client initialized")

# Define S3 paths for the last 2 days with specific subreddit names
logger.info(
    f"Generating S3 paths for {len(SUBREDDIT_NAMES)} subreddits and {len(date_paths)} date paths"
)

# Define the schema for the input JSON data
input_schema = StructType(
    [
        StructField("id", StringType(), True),
        StructField("title", StringType(), True),
        StructField("content", StringType(), True),
        StructField("url", StringType(), True),
        StructField("score", IntegerType(), True),
        StructField("year", IntegerType(), True),
        StructField("month", IntegerType(), True),
        StructField(
            "comments",
            ArrayType(
                StructType(
                    [
                        StructField("id", StringType(), True),
                        StructField("score", IntegerType(), True),
                        StructField("body", StringType(), True),
                        StructField("year", IntegerType(), True),
                        StructField("month", IntegerType(), True),
                    ]
                )
            ),
            True,
        ),
        StructField("created_at_year", StringType(), True),
        StructField("created_at_month", StringType(), True),
        StructField("created_at_day", StringType(), True),
        StructField("subreddit_name", StringType(), True),
    ]
)

# Read the JSON data from S3 with explicit schema
logger.info(f"Reading data from: {top_post_s3_path}")
new_data_df = (
    spark.read.option("header", "true").schema(input_schema).json(top_post_s3_path)
)
new_data_count = new_data_df.count()
logger.info(f"Read {new_data_count} records from source data")

if new_data_count == 0:
    logger.warning("No data found in the source files. Nothing to process.")
    spark.stop()
    exit(0)

# Check if the output path already has data
try:
    # Read existing data
    logger.info(f"Checking for existing data at: {output_s3_path}")
    existing_data_df = spark.read.parquet(output_s3_path)
    existing_data_count = existing_data_df.count()
    logger.info(f"Found {existing_data_count} existing records")

    # Assuming you have a unique identifier like 'id' to deduplicate on
    # First, remove any records from existing data that match new data IDs
    if "id" in existing_data_df.columns and "id" in new_data_df.columns:
        # Get IDs from new data
        new_ids = new_data_df.select("id").distinct()
        logger.info(f"Found {new_ids.count()} unique IDs in new data")

        # Filter out records from existing data that have IDs in the new data
        filtered_existing_df = existing_data_df.join(
            new_ids, existing_data_df.id == new_ids.id, "left_anti"
        )
        filtered_count = filtered_existing_df.count()
        logger.info(f"After filtering existing data, {filtered_count} records remain")

        # Union the filtered existing data with new data
        merged_df = filtered_existing_df.union(new_data_df)
        logger.info(f"Merged data has {merged_df.count()} records")
    else:
        # If no ID column for deduplication, just append
        logger.info(
            "No common ID column found for deduplication, appending all records"
        )
        merged_df = existing_data_df.union(new_data_df)
        logger.info(f"Merged data has {merged_df.count()} records")

except Exception as e:
    logger.info(f"No existing data found or error reading existing data: {str(e)}")
    merged_df = new_data_df
    logger.info("Using only new data for output")

# Enable dynamic partition overwrite
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

# Write the merged data
logger.info(f"Writing data to: {output_s3_path}")
merged_df.write.mode("overwrite").option("path", output_s3_path).option(
    "database", GLUE_DATABASE_NAME
).option("tableName", GLUE_TABLE_NAME).partitionBy(
    "created_at_year", "created_at_month", "created_at_day", "subreddit_name"
).format(
    "parquet"
).save()
logger.info("Data successfully written to S3")

# Stop the Spark context
logger.info("Stopping Spark context")
spark.stop()
logger.info("Glue job completed successfully")
