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
from dataclasses import dataclass
from typing import List
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
logger.info("Starting process_top_daily_data.py Glue job")

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


@dataclass
class S3PathInfo:
    """Dataclass representing path information for S3 data."""

    created_at_year: str
    created_at_month: str
    created_at_day: str
    subreddit: str
    exists: bool

    def get_s3_path(self, bucket_name: str) -> str:
        """Generate the full S3 path from the components."""
        return f"s3a://{bucket_name}/created_at_year={self.created_at_year}/created_at_month={self.created_at_month}/created_at_day={self.created_at_day}/subreddit_name={self.subreddit}/*"


def check_s3_paths(
    bucket_name: str, subreddits: List[str], days: List[datetime]
) -> List[S3PathInfo]:
    s3_client = boto3.client("s3")
    valid_paths = []

    for subreddit in subreddits:
        for day in days:
            year = str(day.year)
            month = str(day.month)
            day_of_month = str(day.day)

            # Construct the path prefix to check
            day_prefix = f"created_at_year={year}/created_at_month={month}/created_at_day={day_of_month}/subreddit_name={subreddit}/"
            logger.info(f"Checking for data in: {day_prefix}")

            # Check for actual objects
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=day_prefix)
            exists = response.get("KeyCount", 0) > 0

            path_info = S3PathInfo(
                created_at_year=year,
                created_at_month=month,
                created_at_day=day_of_month,
                subreddit=subreddit,
                exists=exists,
            )

            if exists:
                logger.info(f"Found {response['KeyCount']} objects in path")
                valid_paths.append(path_info)
            else:
                logger.warning(f"No objects found in path: {day_prefix}")

    return valid_paths


# Get path information for all subreddits and days
path_infos = check_s3_paths(
    bucket_name=RAW_REDDIT_DATA_BUCKET_NAME,
    subreddits=SUBREDDIT_NAMES,
    days=last_2_days,
)

existing_s3_paths = [
    path_info.get_s3_path(bucket_name=RAW_REDDIT_DATA_BUCKET_NAME)
    for path_info in path_infos
    if path_info.exists
]

if len(existing_s3_paths) == 0:
    logger.warning("No existing S3 paths found. Nothing to process.")
    spark.stop()
    exit(0)

logger.info("Reading data from existing S3 paths...")
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

# Read the top posts data from S3 for the last two days
new_data_df = (
    spark.read.option("header", "true").schema(input_schema).json(existing_s3_paths)
)

# Log data statistics
new_data_count = new_data_df.count()
logger.info(f"Read {new_data_count} records from source data")

if new_data_count == 0:
    logger.warning("No data found in the source files. Nothing to process.")
    spark.stop()
    exit(0)

# Log schema
logger.info("Schema of new data:")
new_data_df.printSchema()

# Extract unique partition keys from the new data
logger.info("Extracting unique partition keys...")
partition_keys_df = new_data_df.select("subreddit_name", "year", "month").distinct()
partition_count = partition_keys_df.count()
logger.info(f"Found {partition_count} unique partitions in the new data")

# Initialize an empty DataFrame for existing data
logger.info("Initializing empty DataFrame for existing data")
existing_data_df = spark.createDataFrame([], new_data_df.schema)
existing_data_count = 0

# Read only the relevant partitions from the existing Parquet files
logger.info("Reading existing data from relevant partitions...")
for row in partition_keys_df.collect():
    subreddit, year, month = row["subreddit_name"], row["year"], row["month"]
    partition_path = f"s3a://{PROCESSED_REDDIT_DATA_BUCKET_NAME}/merged_data/subreddit_name={subreddit}/year={year}/month={month}/"
    logger.info(f"Checking partition: {partition_path}")
    try:
        partition_df = spark.read.parquet(partition_path)
        partition_count = partition_df.count()
        logger.info(f"Read {partition_count} records from partition {partition_path}")
        existing_data_df = existing_data_df.union(partition_df)
        existing_data_count += partition_count
    except Exception as e:
        logger.info(
            f"No existing data for partition: {partition_path}, error: {str(e)}"
        )

logger.info(f"Total existing data records: {existing_data_count}")

# Combine existing data with new data
logger.info("Combining existing data with new data...")
combined_df = existing_data_df.union(new_data_df)
combined_count = combined_df.count()
logger.info(f"Combined data has {combined_count} records")

# Remove duplicates based on specific columns
logger.info("Removing duplicates...")
deduplicated_df = combined_df.dropDuplicates(["subreddit_name", "id"])
deduplicated_count = deduplicated_df.count()
logger.info(f"After deduplication: {deduplicated_count} records")

# Calculate how many duplicates were removed
duplicates_removed = combined_count - deduplicated_count
logger.info(f"Removed {duplicates_removed} duplicate records")

# Enable dynamic partition overwrite
spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

# When writing Parquet files, change from append to overwrite mode with dynamic partitioning
deduplicated_df.write.mode("overwrite").option(
    "path", f"s3a://{PROCESSED_REDDIT_DATA_BUCKET_NAME}/merged_data/"
).option("database", GLUE_DATABASE_NAME).option(
    "tableName", GLUE_TABLE_NAME
).partitionBy(
    "created_at_year", "created_at_month", "created_at_day", "subreddit_name"
).format("parquet").save()

# Stop the Spark context
logger.info("Stopping Spark context")
spark.stop()
logger.info("Glue job completed successfully")
