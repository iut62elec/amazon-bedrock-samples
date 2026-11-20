<h1 align="center">Text to SQL Bedrock Agent</h1>


## Authors:
**Pedram Jahangiri** @jpedram, **Sawyer Hirt** @sawyehir, **Zeek Granston** @zeekg, **Suyin Wang** @suyinwa

## Reviewer:
**Maira Ladeira Tanke** @mttanke





## Introduction
Harnessing the power of natural language processing, the "Text to SQL Bedrock Agent" facilitates the automatic transformation of natural language questions into executable SQL queries. This tool bridges the gap between complex database structures and intuitive human inquiries, enabling users to effortlessly extract insights from data using simple English prompts. It leverages AWS Bedrock's cutting-edge agent technology and exemplifies the synergy between AWS's robust infrastructure and advanced large language models offered in AWS bedrock, making sophisticated data analysis accessible to a wider audience.
This repository contains the necessary files to set up and test a Text to SQL conversion using the Bedrock Agent with AWS services.

![sequence-flow-agent](images/text-to-sql-architecture-Athena.png)

## Use case
The code here sets up an agent capable of crafting SQL queries from natural language questions. It then retrieves responses from the database, providing accurate answers to user inquiries. The diagram below outlines the high-level architecture of this solution.




The Agent is designed to:
- Retrieve database schemas
- Execute SQL queries


## Prerequisites

This sample uses [Amazon Bedrock Agents Classic](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-classic-maintenance-mode.html), which closed to new customers on July 30, 2026. Use a dedicated test account that already has Agents Classic access and verify that the configured model is available to that account in the selected Region **before** running the setup cell. [Model availability and end-of-life dates vary by Region](https://docs.aws.amazon.com/bedrock/latest/userguide/model-lifecycle-legacy.html).

Before you begin, ensure you have the following:
- An AWS account with the following permissions:
  - Create, tag, read, and manage the sample's IAM roles and policies.
  - Create and invoke AWS Lambda functions.
  - Create, read from, and write to Amazon S3 buckets.
  - Access and manage Amazon Bedrock agents and models.
  - Create and manage Amazon Glue databases and crawlers.
  - Execute queries and manage workspaces in Amazon Athena.
  - Access to Amazon Nova Lite (`amazon.nova-lite-v1:0`) for on-demand inference in the selected Region. Setup uses `bedrock:GetFoundationModel` to check the model before creating resources; the agent role can invoke only that regional model.

- For local setup,
        - Python and Jupyter Notebooks installed
        - AWS CLI installed and configured
        - An explicit AWS Region configured (for example, `AWS_DEFAULT_REGION=us-east-1`)
- For AWS SageMaker 
    - Make sure your domain has above permission 
    - Use Data Science 3.0 kernel in SageMaker Studio

## Installation

Clone the repository to your local machine or AWS environment

## Usage

1. Start by opening the `create_and_invoke_sql_agent.ipynb` Jupyter Notebook.
2. Run the notebook cells in order. The notebook will:
   - Import configurations from `config.py`.
   - Set your own 'AWS_PROFILE' 
   - Build the necessary infrastructure using `build_infrastructure.py`, which includes:
     - S3 buckets
     - Lambda functions
     - Bedrock agents
     - Glue databases and crawlers
     - Necessary IAM roles and policies
3. After the infrastructure is set up, you can execute sample queries within the notebook to test the agent.
4. To delete all resources created and avoid ongoing charges, run the clean.py script, in the notebook.



## Security boundaries

Treat all SQL produced by a generative AI model as untrusted input. This sample applies independent controls at the request, query, IAM, and Athena layers:

- `query_guard.py` accepts exactly one read-only `SELECT` statement and rejects comments, quoted identifiers, common table expressions, administrative statements, data mutations, `UNLOAD`, table functions, statement stacking, malformed tokens and strings, oversized input, non-ASCII syntax, and control characters before an Athena client is created.
- The Lambda execution role can query only the sample's dedicated Athena workgroup, read only the `thehistoryofbaseball` Glue catalog resources and `data/` objects, and write only to the sample bucket's `athena_result/` prefix. It has no AWS-managed Athena, Glue, or S3 FullAccess policy.
- The enforced Athena workgroup pins results to the sample bucket with SSE-S3 encryption, rejects Requester Pays data, emits CloudWatch metrics, and stops queries that scan more than 100 MB.
- Lambda validates its database, workgroup, timeout, and row-limit configuration before creating an Athena client or submitting a query. Query execution is limited to 120 seconds and 100 returned rows; the response includes `truncated: true` when additional rows exist, and timed-out queries are cancelled on a best-effort basis.
- The S3 bucket blocks all public access and enables default server-side encryption.
- Bedrock role assumption and Lambda invocation are restricted to the deploying account and expected agent ARN.
- Deployment stops before constructing clients, ARNs, or resource names when no AWS Region is configured.

These controls are defense in depth: the prompt and OpenAPI description guide model output, but neither is treated as a security boundary.

### Run the security tests

From this directory, run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile lambda_function.py query_guard.py dependencies/*.py tests/*.py
python3 -m json.tool dependencies/text_to_sql_openapi_schema.json > /dev/null
```

The suite covers rejected queries, supported read-only queries, configuration and notebook imports, bounded results and timeouts, IAM ownership checks, versioned S3 cleanup, and the scope of the generated IAM policies.

### Existing deployments

Merging or downloading this change does not update a running Lambda or its permissions. For deployments created before the dedicated workgroup and tagged roles:

1. Stop access to the old sample: disable its Bedrock action group and restrict invocation of the old Lambda before doing cleanup. Record the function's execution role ARN and the crawler's role from their configurations.
2. Do **not** run the old cleanup script unchanged. It detaches policies from `AWSGlueServiceRole` and attempts to delete it. Leave that role and any shared Bedrock role intact. Also leave their policy attachments intact unless the other owners have confirmed the change.
3. Inventory the sample's resources in the deploying account and Region. Verify ownership and other users before deleting anything. Preserve any data you need. Remove the old sample Lambda, and remove the broad Athena, Glue and S3 grants from its verified, dedicated role before deleting that role. If a role is shared, have its owner replace the grants safely. Remove only the remaining resources confirmed to belong exclusively to this sample.
4. Verify the old function is gone and its dedicated execution role no longer grants broad access. Deploy the new sample from a clean checkout. It creates new, tagged IAM roles and does not adopt existing roles.

## Cleanup

For a deployment created by this revision, run `dependencies/clean.py` through the notebook cleanup cell. Verify the configured agent, Lambda, Athena workgroup, Glue resources and S3 bucket belong exclusively to this deployment before running cleanup, particularly after a partial installation. Bucket cleanup permanently deletes object versions and delete markers. IAM cleanup checks the deployment tag and expected policy attachments; it leaves untagged roles, unexpected dependencies, permissions boundaries and in-use policies for the owner to review.

Expected already-absent resources are reported and ignored. Other failures are retained while independent cleanup steps continue, then raised together at the end so incomplete cleanup cannot look successful. Review the output and verify the resources are gone before discarding the checkout.
