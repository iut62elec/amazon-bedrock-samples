import pathlib
import sys
import unittest

DEPENDENCIES_ROOT = pathlib.Path(__file__).resolve().parents[1] / "dependencies"
sys.path.insert(0, str(DEPENDENCIES_ROOT))

from clean import CleanupReport, delete_customer_managed_policy, delete_iam_role, empty_and_delete_bucket
from security_config import deployment_tag
from unittest import mock


class FakePaginator:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages)


class FakeIamClient:
    def __init__(self):
        self.calls = []
        self.role = {"Tags": [deployment_tag("sample-deployment")]}
        self.pages = {
            "list_attached_role_policies": [
                {"AttachedPolicies": [{"PolicyArn": "arn:managed:one"}]},
                {"AttachedPolicies": [{"PolicyArn": "arn:managed:two"}]},
            ],
            "list_role_policies": [
                {"PolicyNames": ["inline-one"]},
                {"PolicyNames": ["inline-two"]},
            ],
            "list_instance_profiles_for_role": [{"InstanceProfiles": []}],
        }

    def get_role(self, **kwargs):
        return {"Role": self.role}

    def get_paginator(self, operation_name):
        return FakePaginator(self.pages[operation_name])

    def detach_role_policy(self, **kwargs):
        self.calls.append(("detach", kwargs))

    def delete_role_policy(self, **kwargs):
        self.calls.append(("delete_inline", kwargs))

    def delete_role(self, **kwargs):
        self.calls.append(("delete_role", kwargs))


class FakeS3Client:
    def __init__(self):
        self.delete_requests = []
        self.deleted_bucket = None
        self.pages = {
            "list_object_versions": [
                {
                    "Versions": [
                        {"Key": "data.csv", "VersionId": "version-1"}
                    ],
                    "DeleteMarkers": [
                        {"Key": "old.csv", "VersionId": "marker-1"}
                    ],
                }
            ],
            "list_objects_v2": [
                {"Contents": [{"Key": "current.csv"}]}
            ],
        }

    def get_paginator(self, operation_name):
        return FakePaginator(self.pages[operation_name])

    def delete_objects(self, **kwargs):
        self.delete_requests.append(kwargs["Delete"]["Objects"])
        return {}

    def delete_bucket(self, **kwargs):
        self.deleted_bucket = kwargs["Bucket"]


class FakeAwsError(Exception):
    def __init__(self, code, message="error"):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


class CleanupTests(unittest.TestCase):
    def delete_test_role(self, client):
        delete_iam_role(client, "sample-role", "sample-deployment",
                        {"arn:managed:one", "arn:managed:two"}, {"inline-one", "inline-two"})

    def test_owned_role_cleanup_handles_paginated_sample_policies(self):
        client = FakeIamClient()
        self.delete_test_role(client)
        detached_arns = {
            kwargs["PolicyArn"]
            for operation, kwargs in client.calls
            if operation == "detach"
        }
        self.assertEqual(detached_arns, {"arn:managed:one", "arn:managed:two"})
        inline_names = {
            kwargs["PolicyName"]
            for operation, kwargs in client.calls
            if operation == "delete_inline"
        }
        self.assertEqual(inline_names, {"inline-one", "inline-two"})
        self.assertEqual(
            client.calls[-1],
            ("delete_role", {"RoleName": "sample-role"}),
        )

    def test_leaves_unowned_roles_and_unexpected_dependencies_untouched(self):
        cases = [
            ({}, {}),
            ({"Tags": [deployment_tag("different-deployment")]}, {}),
            ({"Tags": [deployment_tag("sample-deployment")], "PermissionsBoundary": {"PermissionsBoundaryArn": "arn:boundary"}}, {}),
            (None, {"list_instance_profiles_for_role": [{"InstanceProfiles": [{"InstanceProfileName": "shared"}]}]}),
            (None, {"list_attached_role_policies": [{"AttachedPolicies": [{"PolicyArn": "arn:managed:one"}]}, {"AttachedPolicies": [{"PolicyArn": "arn:foreign"}]}]}),
            (None, {"list_role_policies": [{"PolicyNames": ["inline-one"]}, {"PolicyNames": ["foreign"]}]}),
        ]
        for role, pages in cases:
            with self.subTest(role=role, pages=pages):
                client = FakeIamClient()
                if role is not None:
                    client.role = role
                client.pages.update(pages)
                with self.assertRaisesRegex(RuntimeError, "untouched"):
                    self.delete_test_role(client)
                self.assertEqual(client.calls, [])

    def test_only_deletes_tagged_unused_customer_policies(self):
        ours = [deployment_tag("sample-deployment")]
        for tags, attachments, boundaries, should_delete in [
            ([], 0, 0, False), (ours, 1, 0, False), (ours, 0, 1, False), (ours, 0, 0, True)
        ]:
            with self.subTest(tags=tags, attachments=attachments, boundaries=boundaries):
                client = mock.Mock()
                client.get_paginator.return_value = FakePaginator([{"Tags": tags}])
                client.get_policy.return_value = {"Policy": {
                    "AttachmentCount": attachments, "PermissionsBoundaryUsageCount": boundaries
                }}
                client.list_policy_versions.return_value = {"Versions": [
                    {"VersionId": "v1", "IsDefaultVersion": False},
                    {"VersionId": "v2", "IsDefaultVersion": True},
                ]}
                args = (client, "123456789012", "sample-policy", "sample-deployment")
                if should_delete:
                    delete_customer_managed_policy(*args)
                    client.delete_policy.assert_called_once()
                    client.delete_policy_version.assert_called_once_with(
                        PolicyArn="arn:aws:iam::123456789012:policy/sample-policy", VersionId="v1")
                else:
                    with self.assertRaisesRegex(RuntimeError, "untouched"):
                        delete_customer_managed_policy(*args)
                    client.delete_policy.assert_not_called()
                    client.delete_policy_version.assert_not_called()

    def test_bucket_cleanup_removes_versions_markers_and_current_objects(self):
        client = FakeS3Client()

        empty_and_delete_bucket(client, "sample-bucket")

        self.assertEqual(
            client.delete_requests,
            [
                [
                    {"Key": "data.csv", "VersionId": "version-1"},
                    {"Key": "old.csv", "VersionId": "marker-1"},
                ],
                [{"Key": "current.csv"}],
            ],
        )
        self.assertEqual(client.deleted_bucket, "sample-bucket")

    def test_cleanup_report_ignores_only_expected_missing_resources(self):
        report = CleanupReport()

        report.attempt(
            "missing role",
            lambda: (_ for _ in ()).throw(FakeAwsError("NoSuchEntity")),
            {"NoSuchEntity"},
        )
        report.attempt(
            "denied role",
            lambda: (_ for _ in ()).throw(FakeAwsError("AccessDenied")),
            {"NoSuchEntity"},
        )

        self.assertEqual(len(report.failures), 1)
        with self.assertRaisesRegex(RuntimeError, "denied role"):
            report.raise_if_failed()


if __name__ == "__main__":
    unittest.main()
