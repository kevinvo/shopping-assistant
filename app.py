#!/usr/bin/env python3
from aws_cdk import App
from cdk_infrastructure.infrastructure_stack import ShoppingAssistantInfrastructureStack
from aws_cdk import aws_lambda as lambda_

app = App()

# Set default runtime for custom resources to Node.js 20.x
lambda_.Function._default_runtime = lambda_.Runtime.NODEJS_20_X

ShoppingAssistantInfrastructureStack(app, "RedditScraperStack")
app.synth()
