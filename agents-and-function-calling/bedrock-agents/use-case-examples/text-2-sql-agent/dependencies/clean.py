"""Delete resources created by the text-to-SQL Bedrock Agent sample."""

from dataclasses import dataclass, field
import time

if __package__:
    from .security_config import GLUE_SERVICE_POLICY_ARN, LAMBDA_LOGGING_POLICY_ARN, deployment_tag
else:
    from security_config import GLUE_SERVICE_POLICY_ARN, LAMBDA_LOGGING_POLICY_ARN, deployment_tag


COMMON_NOT_FOUND_CODES = frozenset(
    {
        "EntityNotFoundException",
        "NoSuchBucket",
        "NoSuchEntity",
        "ResourceNotFoundException",
    }
)
ATHENA_NOT_FOUND_CODES = frozenset({"InvalidRequestException"})


def _aws_error_details(error):
    response = getattr(error, "response", {})
    error_details = response.get("Error", {}) if isinstance(response, dict) else {}
    return error_details.get("Code", ""), error_details.get("Message", str(error))


def _is_expected_missing_error(error, expected_codes):
    code, message = _aws_error_details(error)
    if code not in expected_codes:
        return False
    if code == "InvalidRequestException":
        normalized_message = message.lower()
        return "not found" in normalized_message or "does not exist" in normalized_message
    return True


def _list_all(client, operation_name, result_key, **kwargs):
    """Collect a complete paginated AWS list response before mutating it."""
    paginator = client.get_paginator(operation_name)
    items = []
    for page in paginator.paginate(**kwargs):
        items.extend(page.get(result_key, []))
    return items


@dataclass
class CleanupReport:
    """Continue independent cleanup steps while retaining every real failure."""

    failures: list = field(default_factory=list)

    def attempt(self, description, operation, expected_missing_codes=()):
        try:
            operation()
        except Exception as error:
            if _is_expected_missing_error(error, expected_missing_codes):
                print(f"{description}: already absent.")
                return
            self.failures.append((description, error))
            print(f"{description} failed: {error}")

    def raise_if_failed(self):
        if not self.failures:
            print("Cleanup completed without reported failures.")
            return
        descriptions = ", ".join(description for description, _ in self.failures)
        raise RuntimeError(
            f"Cleanup incomplete; {len(self.failures)} step(s) failed: {descriptions}"
        )


def _find_agent_id(client, agent_name):
    for summary in _list_all(client, "list_agents", "agentSummaries"):
        if summary.get("agentName") == agent_name:
            return summary["agentId"]
    return None


def delete_agent_resources(client, agent_name):
    """Delete deployed aliases; Bedrock removes its reserved test alias with the agent."""
    agent_id = _find_agent_id(client, agent_name)
    if not agent_id:
        print(f"Bedrock agent '{agent_name}': already absent.")
        return

    aliases = _list_all(
        client,
        "list_agent_aliases",
        "agentAliasSummaries",
        agentId=agent_id,
    )
    for alias in aliases:
        if alias["agentAliasId"] == "TSTALIASID":
            continue
        client.delete_agent_alias(
            agentId=agent_id,
            agentAliasId=alias["agentAliasId"],
        )

    for attempt in range(12):
        try:
            client.delete_agent(agentId=agent_id)
            print(f"Bedrock agent '{agent_name}' deleted successfully.")
            return
        except Exception as error:
            code, _ = _aws_error_details(error)
            if code != "ConflictException" or attempt == 11:
                raise
            time.sleep(5)


def delete_glue_tables(client, database_name):
    tables = _list_all(
        client,
        "get_tables",
        "TableList",
        DatabaseName=database_name,
    )
    for table in tables:
        client.delete_table(
            DatabaseName=database_name,
            Name=table["Name"],
        )
    print(f"Deleted {len(tables)} table(s) from Glue database '{database_name}'.")


def delete_iam_role(client, role_name, deployment_id, managed_arns, inline_names):
    """Only tear down a tagged sample role with the expected dependencies."""
    role = client.get_role(RoleName=role_name)["Role"]
    if deployment_tag(deployment_id) not in role.get("Tags", []):
        raise RuntimeError(f"Leaving role '{role_name}' untouched: missing deployment tag.")
    if role.get("PermissionsBoundary"):
        raise RuntimeError(f"Leaving role '{role_name}' untouched: permissions boundary present.")
    attached_policies = _list_all(
        client,
        "list_attached_role_policies",
        "AttachedPolicies",
        RoleName=role_name,
    )
    inline_policy_names = _list_all(
        client,
        "list_role_policies",
        "PolicyNames",
        RoleName=role_name,
    )
    instance_profiles = _list_all(
        client,
        "list_instance_profiles_for_role",
        "InstanceProfiles",
        RoleName=role_name,
    )

    foreign_inline = set(inline_policy_names) - inline_names
    foreign_managed = {policy["PolicyArn"] for policy in attached_policies} - managed_arns
    if instance_profiles or foreign_inline or foreign_managed:
        raise RuntimeError(f"Leaving role '{role_name}' untouched: unexpected role dependencies.")
    for policy in attached_policies:
        client.detach_role_policy(
            RoleName=role_name,
            PolicyArn=policy["PolicyArn"],
        )
    for policy_name in inline_policy_names:
        client.delete_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
        )
    client.delete_role(RoleName=role_name)
    print(f"IAM role '{role_name}' deleted successfully.")


def delete_customer_managed_policy(client, account_id, policy_name, deployment_id):
    """Delete non-default versions and then the exact sample policy."""
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    tags = _list_all(client, "list_policy_tags", "Tags", PolicyArn=policy_arn)
    if deployment_tag(deployment_id) not in tags:
        raise RuntimeError(f"Leaving policy '{policy_name}' untouched: missing deployment tag.")
    policy = client.get_policy(PolicyArn=policy_arn)["Policy"]
    if policy["AttachmentCount"] or policy.get("PermissionsBoundaryUsageCount", 0):
        raise RuntimeError(f"Leaving policy '{policy_name}' untouched: it is still in use.")
    versions = client.list_policy_versions(PolicyArn=policy_arn).get("Versions", [])
    for version in versions:
        if not version.get("IsDefaultVersion"):
            client.delete_policy_version(
                PolicyArn=policy_arn,
                VersionId=version["VersionId"],
            )
    client.delete_policy(PolicyArn=policy_arn)
    print(f"IAM policy '{policy_name}' deleted successfully.")


def _delete_s3_objects(client, bucket_name, objects):
    for start in range(0, len(objects), 1000):
        response = client.delete_objects(
            Bucket=bucket_name,
            Delete={"Objects": objects[start : start + 1000], "Quiet": True},
        )
        errors = response.get("Errors", [])
        if errors:
            raise RuntimeError(
                f"S3 rejected {len(errors)} object deletion(s) in '{bucket_name}'."
            )


def empty_and_delete_bucket(client, bucket_name):
    """Delete current objects, versions, and delete markers before the bucket."""
    versioned_objects = []
    paginator = client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket_name):
        for item in page.get("Versions", []):
            versioned_objects.append(
                {"Key": item["Key"], "VersionId": item["VersionId"]}
            )
        for item in page.get("DeleteMarkers", []):
            versioned_objects.append(
                {"Key": item["Key"], "VersionId": item["VersionId"]}
            )
    _delete_s3_objects(client, bucket_name, versioned_objects)

    current_objects = [
        {"Key": item["Key"]}
        for item in _list_all(
            client,
            "list_objects_v2",
            "Contents",
            Bucket=bucket_name,
        )
    ]
    _delete_s3_objects(client, bucket_name, current_objects)
    client.delete_bucket(Bucket=bucket_name)
    print(f"S3 bucket '{bucket_name}' deleted successfully.")


def cleanup_sample(sample):
    """Remove all resources created by this sample and report any residue."""
    report = CleanupReport()

    report.attempt(
        f"Delete Bedrock agent {sample.agent_name}",
        lambda: delete_agent_resources(
            sample.bedrock_agent_client,
            sample.agent_name,
        ),
        COMMON_NOT_FOUND_CODES,
    )
    report.attempt(
        f"Delete Lambda function {sample.lambda_name}",
        lambda: sample.lambda_client.delete_function(
            FunctionName=sample.lambda_name
        ),
        COMMON_NOT_FOUND_CODES,
    )
    report.attempt(
        f"Delete Athena workgroup {sample.athena_workgroup_name}",
        lambda: sample.athena.delete_work_group(
            WorkGroup=sample.athena_workgroup_name,
            RecursiveDeleteOption=True,
        ),
        ATHENA_NOT_FOUND_CODES,
    )
    report.attempt(
        f"Delete Glue crawler {sample.glue_crawler_name}",
        lambda: sample.glue.delete_crawler(Name=sample.glue_crawler_name),
        COMMON_NOT_FOUND_CODES,
    )
    report.attempt(
        f"Delete Glue tables in {sample.glue_database_name}",
        lambda: delete_glue_tables(
            sample.glue,
            sample.glue_database_name,
        ),
        COMMON_NOT_FOUND_CODES,
    )
    report.attempt(
        f"Delete Glue database {sample.glue_database_name}",
        lambda: sample.glue.delete_database(
            Name=sample.glue_database_name
        ),
        COMMON_NOT_FOUND_CODES,
    )

    policy_prefix = f"arn:aws:iam::{sample.account_id}:policy/"
    role_policies = (
        (sample.agent_role_name, {
            policy_prefix + sample.bedrock_agent_bedrock_allow_policy_name,
            policy_prefix + sample.bedrock_agent_s3_allow_policy_name,
        }, set()),
        (sample.lambda_role_name, {LAMBDA_LOGGING_POLICY_ARN}, {sample.lambda_query_policy_name}),
        (sample.glue_role_name, {GLUE_SERVICE_POLICY_ARN}, {sample.glue_data_policy_name}),
    )
    for role_name, managed_arns, inline_names in role_policies:
        report.attempt(
            f"Delete IAM role {role_name}",
            lambda r=role_name, m=managed_arns, i=inline_names: delete_iam_role(
                sample.iam_client, r, sample.deployment_id, m, i,
            ),
            COMMON_NOT_FOUND_CODES,
        )

    for policy_name in (
        sample.bedrock_agent_bedrock_allow_policy_name,
        sample.bedrock_agent_s3_allow_policy_name,
    ):
        report.attempt(
            f"Delete IAM policy {policy_name}",
            lambda policy_name=policy_name: delete_customer_managed_policy(
                sample.iam_client,
                sample.account_id,
                policy_name,
                sample.deployment_id,
            ),
            COMMON_NOT_FOUND_CODES,
        )

    report.attempt(
        f"Delete S3 bucket {sample.bucket_name}",
        lambda: empty_and_delete_bucket(
            sample.s3_client,
            sample.bucket_name,
        ),
        COMMON_NOT_FOUND_CODES,
    )
    report.raise_if_failed()


def main():
    if __package__:
        from . import config as sample
    else:
        import config as sample

    cleanup_sample(sample)


if __name__ == "__main__":
    main()
