#!/usr/bin/env python3
from aws_cdk import App
from cdk_infrastructure.infrastructure_stack import ShoppingAssistantInfrastructureStack
from aws_cdk import aws_lambda as lambda_

app = App()

# Set default runtime for custom resources to Node.js 20.x
lambda_.Function._default_runtime = lambda_.Runtime.NODEJS_20_X

env_name = app.node.try_get_context("infrastructure_env") or "test"
stack_name = "RedditScraperStack" if env_name == "test" else f"RedditScraperStack-{env_name}"

ShoppingAssistantInfrastructureStack(app, stack_name)
app.synth()
