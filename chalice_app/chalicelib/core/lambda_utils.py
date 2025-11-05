from __future__ import annotations

import boto3


_lambda_client = boto3.client("lambda")


def get_lambda_function_name(
    handler: str = "reddit_scraper_lambda.lambda_handler",
) -> str:
    """Return the Lambda function name that matches the provided handler path."""

    paginator = _lambda_client.get_paginator("list_functions")
    for page in paginator.paginate():
        for function in page.get("Functions", []):
            if function.get("Handler") == handler:
                return function["FunctionName"]
    raise ValueError(f"Could not find Lambda function with handler '{handler}'")
