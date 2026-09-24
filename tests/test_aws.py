"""The AWS side of the pipeline, without an AWS account.

S3 runs on moto. Glue, EMR Serverless, IAM, EC2 and the two Redshift APIs are
small recording fakes: each test states which calls must happen, which must
not, and what the module does when AWS reports a failure. The fakes answer the
way the services were observed to answer on a real run, including the one
that surprised: Glue's DeleteJob succeeds for a job that does not exist.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rootsignal.pipeline import CLEAN, Lake, aws
from rootsignal.sql.database import LOAD_ORDER


class NotFound(Exception):
    pass


class FakeClient:
    """Records every call; answers from per-method handlers."""

    def __init__(self, **handlers):
        self.calls: list[tuple[str, dict]] = []
        self.handlers = handlers
        self.exceptions = SimpleNamespace(
            EntityNotFoundException=NotFound,
            ResourceNotFoundException=NotFound,
            NoSuchEntityException=NotFound,
            ClientError=NotFound,
        )

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(**kwargs):
            self.calls.append((name, kwargs))
            handler = self.handlers.get(name)
            if handler is None:
                return {}
            return handler(**kwargs) if callable(handler) else handler

        return call

    def called(self, name: str) -> list[dict]:
        return [kwargs for method, kwargs in self.calls if method == name]


def missing(**_):
    raise NotFound()


def sequence(*answers):
    """Successive answers to repeated calls, the last one repeating."""
    remaining = list(answers)

    def answer(**_):
        value = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(value, Exception):
            raise value
        return value

    return answer


@pytest.fixture
def clients(monkeypatch):
    """Route aws._client to fakes a test registers, and make waiting instant."""
    registry: dict[str, FakeClient] = {}
    monkeypatch.setattr(aws, "_client", lambda service: registry[service])
    monkeypatch.setattr(aws.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(aws, "publish_job", lambda lake: lake.uri("jobs", "conform_online_retail.py"))
    return registry


LAKE = Lake("s3://rootsignal-lake-test")


# --------------------------------------------------------------------------
# Roles and the bucket
# --------------------------------------------------------------------------


def test_a_role_can_only_reach_its_own_bucket(clients) -> None:
    iam = clients["iam"] = FakeClient(
        get_role=missing, create_role={"Role": {"Arn": "arn:aws:iam::1:role/r"}}
    )

    arn = aws.ensure_role("r", ["glue.amazonaws.com"], "lake-bucket", write=False)

    assert arn == "arn:aws:iam::1:role/r"
    trust = json.loads(iam.called("create_role")[0]["AssumeRolePolicyDocument"])
    assert trust["Statement"][0]["Principal"]["Service"] == ["glue.amazonaws.com"]
    policy = json.loads(iam.called("put_role_policy")[0]["PolicyDocument"])["Statement"][0]
    assert policy["Resource"] == ["arn:aws:s3:::lake-bucket", "arn:aws:s3:::lake-bucket/*"]
    assert "s3:PutObject" not in policy["Action"] and "s3:DeleteObject" not in policy["Action"]


def test_a_writing_role_may_put_and_delete_and_managed_policies_attach(clients) -> None:
    iam = clients["iam"] = FakeClient(get_role={"Role": {"Arn": "arn:existing"}})

    aws.ensure_role("r", ["glue.amazonaws.com"], "b", write=True, managed=("arn:managed",))

    assert not iam.called("create_role"), "an existing role is reused, not recreated"
    actions = json.loads(iam.called("put_role_policy")[0]["PolicyDocument"])["Statement"][0]["Action"]
    assert {"s3:PutObject", "s3:DeleteObject"} <= set(actions)
    assert iam.called("attach_role_policy") == [{"RoleName": "r", "PolicyArn": "arn:managed"}]


def test_the_bucket_is_created_private(monkeypatch) -> None:
    moto = pytest.importorskip("moto")
    import boto3

    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "ap-south-1",
    }.items():
        monkeypatch.setenv(name, value)
    with moto.mock_aws():
        aws.ensure_bucket("rootsignal-private")
        block = boto3.client("s3").get_public_access_block(Bucket="rootsignal-private")
        assert all(block["PublicAccessBlockConfiguration"].values())
        aws.ensure_bucket("rootsignal-private")  # a second run changes nothing


# --------------------------------------------------------------------------
# Glue and EMR Serverless
# --------------------------------------------------------------------------


def test_glue_creates_the_job_with_the_lake_paths_and_waits_for_it(clients) -> None:
    glue = clients["glue"] = FakeClient(
        get_job=missing,
        start_job_run={"JobRunId": "jr_1"},
        get_job_run=sequence(
            {"JobRun": {"JobRunState": "RUNNING"}},
            {"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 150}},
        ),
    )

    run = aws.run_glue(LAKE, "arn:role", workers=2)

    job = glue.called("create_job")[0]
    assert job["Name"] == aws.GLUE_JOB and job["Role"] == "arn:role"
    assert job["DefaultArguments"] == {
        "--input": "s3://rootsignal-lake-test/raw/online_retail/invoices.csv",
        "--output": "s3://rootsignal-lake-test/curated/online_retail",
    }
    assert job["NumberOfWorkers"] == 2 and job["Timeout"] == 20 and job["MaxRetries"] == 0
    assert len(glue.called("get_job_run")) == 2
    assert run.state == "SUCCEEDED"
    assert run.usage == f"{150 / 3600 * 2:.3f} DPU-hours"


def test_glue_updates_an_existing_job_and_bills_at_least_a_minute(clients) -> None:
    glue = clients["glue"] = FakeClient(
        get_job={"Job": {}},
        start_job_run={"JobRunId": "jr_2"},
        get_job_run={"JobRun": {"JobRunState": "SUCCEEDED", "ExecutionTime": 20}},
    )

    run = aws.run_glue(LAKE, "arn:role", workers=2)

    assert not glue.called("create_job") and len(glue.called("update_job")) == 1
    assert run.usage == f"{60 / 3600 * 2:.3f} DPU-hours"


def test_a_failed_glue_run_is_reported_with_its_error(clients) -> None:
    clients["glue"] = FakeClient(
        get_job={"Job": {}},
        start_job_run={"JobRunId": "jr_3"},
        get_job_run={"JobRun": {"JobRunState": "FAILED", "ExecutionTime": 70, "ErrorMessage": "boom"}},
    )

    run = aws.run_glue(LAKE, "arn:role")

    assert (run.state, run.error) == ("FAILED", "boom")


def test_emr_reuses_a_live_application_and_ignores_a_terminated_one(clients) -> None:
    emr = clients["emr-serverless"] = FakeClient(
        list_applications={
            "applications": [
                {"name": aws.EMR_APP, "state": "TERMINATED", "id": "old"},
                {"name": aws.EMR_APP, "state": "STOPPED", "id": "live"},
            ]
        }
    )
    assert aws.ensure_emr_application() == "live"
    assert not emr.called("create_application")


def test_a_new_emr_application_is_capped_and_stops_itself(clients) -> None:
    emr = clients["emr-serverless"] = FakeClient(
        list_applications={"applications": []}, create_application={"applicationId": "new"}
    )
    assert aws.ensure_emr_application() == "new"
    created = emr.called("create_application")[0]
    assert created["maximumCapacity"]["cpu"] == "8 vCPU"
    assert created["autoStopConfiguration"] == {"enabled": True, "idleTimeoutMinutes": 2}


def test_emr_runs_the_same_script_with_the_same_arguments(clients) -> None:
    emr = clients["emr-serverless"] = FakeClient(
        list_applications={"applications": [{"name": aws.EMR_APP, "state": "STARTED", "id": "app"}]},
        start_job_run={"jobRunId": "run"},
        get_job_run=sequence(
            {"jobRun": {"state": "RUNNING"}},
            {"jobRun": {"state": "SUCCESS", "totalExecutionDurationSeconds": 95,
                        "totalResourceUtilization": {"vCPUHour": 0.1, "memoryGBHour": 0.4}}},
        ),
    )

    run = aws.run_emr(LAKE, "arn:emr", "s3://rootsignal-lake-test/curated_emr/online_retail")

    driver = emr.called("start_job_run")[0]["jobDriver"]["sparkSubmit"]
    assert driver["entryPoint"] == "s3://rootsignal-lake-test/jobs/conform_online_retail.py"
    assert driver["entryPointArguments"] == [
        "--input", "s3://rootsignal-lake-test/raw/online_retail/invoices.csv",
        "--output", "s3://rootsignal-lake-test/curated_emr/online_retail",
    ]
    assert emr.called("start_job_run")[0]["executionTimeoutMinutes"] == 20
    assert (run.state, run.seconds) == ("SUCCESS", 95.0)
    assert run.usage == "0.100 vCPU-hours, 0.400 GB-hours"


# --------------------------------------------------------------------------
# Redshift Serverless
# --------------------------------------------------------------------------


def network_clients(registry) -> None:
    registry["ec2"] = FakeClient(
        describe_vpcs={"Vpcs": [{"VpcId": "vpc-1"}]},
        describe_subnets={"Subnets": [{"SubnetId": "a"}, {"SubnetId": "b"}, {"SubnetId": "c"}]},
        describe_security_groups={"SecurityGroups": [{"GroupId": "sg-1"}]},
    )


def test_redshift_is_created_at_one_size_with_a_spending_cap(clients) -> None:
    network_clients(clients)
    rs = clients["redshift-serverless"] = FakeClient(
        get_namespace=missing,
        get_workgroup=sequence(
            NotFound(),
            {"workgroup": {"status": "AVAILABLE", "workgroupArn": "arn:wg"}},
        ),
        create_workgroup={"workgroup": {"status": "CREATING", "workgroupArn": "arn:wg"}},
        list_usage_limits={"usageLimits": []},
    )

    assert aws.ensure_redshift("arn:rs", base_capacity=8, daily_rpu_hours=4) == "arn:wg"

    namespace = rs.called("create_namespace")[0]
    assert namespace["defaultIamRoleArn"] == "arn:rs"
    workgroup = rs.called("create_workgroup")[0]
    assert workgroup["baseCapacity"] == workgroup["maxCapacity"] == 8
    assert workgroup["publiclyAccessible"] is False
    assert workgroup["subnetIds"] == ["a", "b", "c"] and workgroup["securityGroupIds"] == ["sg-1"]
    assert rs.called("create_usage_limit") == [
        {"resourceArn": "arn:wg", "usageType": "serverless-compute", "amount": 4,
         "period": "daily", "breachAction": "deactivate"}
    ]


def test_an_existing_spending_cap_is_not_added_twice(clients) -> None:
    rs = clients["redshift-serverless"] = FakeClient(
        get_namespace={"namespace": {}},
        get_workgroup={"workgroup": {"status": "AVAILABLE", "workgroupArn": "arn:wg"}},
        list_usage_limits={"usageLimits": [{"usageLimitId": "u"}]},
    )
    aws.ensure_redshift("arn:rs")
    assert not rs.called("create_namespace") and not rs.called("create_workgroup")
    assert not rs.called("create_usage_limit")


def test_one_statement_is_executed_alone_and_several_as_one_batch(clients) -> None:
    data = clients["redshift-data"] = FakeClient(
        execute_statement={"Id": "single"},
        batch_execute_statement={"Id": "batch"},
        describe_statement=lambda Id: {
            "Status": "FINISHED", "SubStatements": [{"Id": f"{Id}:1"}, {"Id": f"{Id}:2"}]
        },
    )
    assert aws.execute(["SELECT 1", "   "]) == ["single"]
    assert aws.execute(["SET x", "SELECT 1"]) == ["batch:1", "batch:2"]
    assert data.called("batch_execute_statement")[0]["Sqls"] == ["SET x", "SELECT 1"]


def test_a_failed_statement_raises_with_redshifts_own_message(clients) -> None:
    clients["redshift-data"] = FakeClient(
        execute_statement={"Id": "s"},
        describe_statement={"Status": "FAILED", "Error": "column \"x\" does not exist"},
    )
    with pytest.raises(RuntimeError, match='column "x" does not exist'):
        aws.execute(["SELECT x"])


def test_a_statement_that_never_finishes_is_cancelled(clients, monkeypatch) -> None:
    data = clients["redshift-data"] = FakeClient(
        execute_statement={"Id": "slow"}, describe_statement={"Status": "STARTED"}
    )
    clock = iter([0.0, 5.0, 11.0])
    monkeypatch.setattr(aws.time, "time", lambda: next(clock))
    with pytest.raises(TimeoutError):
        aws.execute(["SELECT pg_sleep(100)"], timeout=10)
    assert data.called("cancel_statement") == [{"Id": "slow"}]


def test_fetch_sets_the_schema_in_the_same_batch_and_reads_every_page(clients) -> None:
    pages = {
        None: {
            "ColumnMetadata": [{"name": "region_code"}, {"name": "net_sales"}],
            "Records": [[{"stringValue": "France"}, {"doubleValue": 10.5}]],
            "NextToken": "page2",
        },
        "page2": {
            "ColumnMetadata": [{"name": "region_code"}, {"name": "net_sales"}],
            "Records": [[{"stringValue": "Spain"}, {"isNull": True}]],
        },
    }
    data = clients["redshift-data"] = FakeClient(
        batch_execute_statement={"Id": "b"},
        describe_statement={"Status": "FINISHED", "SubStatements": [{"Id": "b:1"}, {"Id": "b:2"}]},
        get_statement_result=lambda Id, NextToken=None: pages[NextToken],
    )

    frame = aws.fetch("SELECT * FROM mart", schema="online_retail")

    assert data.called("batch_execute_statement")[0]["Sqls"] == [
        "SET search_path TO online_retail", "SELECT * FROM mart"
    ]
    assert {call["Id"] for call in data.called("get_statement_result")} == {"b:2"}
    assert frame["region_code"].tolist() == ["France", "Spain"]
    assert frame["net_sales"].tolist()[0] == 10.5 and frame["net_sales"].isna().tolist()[1]


def test_the_redshift_load_copies_every_table_from_the_clean_zone(clients) -> None:
    batches = []

    def batch(Sqls, **_):
        batches.append(Sqls)
        return {"Id": f"b{len(batches)}"}

    counts_page = {
        "ColumnMetadata": [{"name": "name"}, {"name": "n"}],
        "Records": [[{"stringValue": name}, {"longValue": 7}] for name in LOAD_ORDER],
    }
    clients["redshift-data"] = FakeClient(
        batch_execute_statement=batch,
        describe_statement=lambda Id: {"Status": "FINISHED", "SubStatements": [{"Id": f"{Id}:1"}, {"Id": f"{Id}:2"}]},
        get_statement_result=counts_page,
    )

    counts = aws.load_redshift(LAKE, "online_retail")

    ddl = batches[0]
    assert ddl[:3] == ["DROP SCHEMA IF EXISTS online_retail CASCADE", "CREATE SCHEMA online_retail",
                       "SET search_path TO online_retail"]
    assert sum(statement.startswith("CREATE TABLE") for statement in ddl) == len(LOAD_ORDER)
    assert not any("CHECK" in statement or "CREATE INDEX" in statement for statement in ddl)
    copies = [statement for statement in ddl if statement.startswith("COPY")]
    assert copies == [
        f"COPY {name} FROM '{LAKE.uri(CLEAN, name)}/' IAM_ROLE default FORMAT AS PARQUET" for name in LOAD_ORDER
    ]
    views = batches[1]
    assert views[0] == "SET search_path TO online_retail"
    assert any(statement.startswith("CREATE VIEW mart_commercial_daily") for statement in views)
    assert counts == {name: 7 for name in LOAD_ORDER}


# --------------------------------------------------------------------------
# Teardown
# --------------------------------------------------------------------------


def test_teardown_reports_only_what_it_actually_removed(clients) -> None:
    """DeleteJob succeeds for a job that never existed; that is not a removal."""
    glue = clients["glue"] = FakeClient(get_job=missing, delete_job={"JobName": aws.GLUE_JOB})
    clients["emr-serverless"] = FakeClient(list_applications={"applications": []})
    clients["redshift-serverless"] = FakeClient(get_workgroup=missing, delete_namespace=missing)

    assert aws.teardown() == []
    assert not glue.called("delete_job")


def test_teardown_stops_a_running_application_before_deleting_it(clients) -> None:
    clients["glue"] = FakeClient(get_job={"Job": {}})
    emr = clients["emr-serverless"] = FakeClient(
        list_applications={"applications": [{"name": aws.EMR_APP, "state": "STARTED", "id": "app"}]},
        get_application=sequence({"application": {"state": "STOPPING"}}, {"application": {"state": "STOPPED"}}),
    )
    rs = clients["redshift-serverless"] = FakeClient(
        get_workgroup=sequence({"workgroup": {}}, {"workgroup": {}}, NotFound()),
        get_namespace=sequence({"namespace": {}}, NotFound()),
    )

    removed = aws.teardown()

    order = [method for method, _ in emr.calls]
    assert order.index("stop_application") < order.index("delete_application")
    assert rs.called("delete_workgroup") and rs.called("delete_namespace")
    assert removed == [
        f"Glue job {aws.GLUE_JOB}",
        "EMR Serverless application app",
        f"Redshift Serverless workgroup {aws.WORKGROUP}",
        f"Redshift Serverless namespace {aws.NAMESPACE}",
    ]


def test_billable_resources_lists_what_exists_and_nothing_else(clients) -> None:
    clients["glue"] = FakeClient(get_job=missing)
    clients["emr-serverless"] = FakeClient(
        list_applications={"applications": [
            {"name": aws.EMR_APP, "state": "TERMINATED", "id": "gone"},
            {"name": "someone-elses-app", "state": "STARTED", "id": "other"},
        ]}
    )
    clients["redshift-serverless"] = FakeClient(
        list_workgroups={"workgroups": [{"workgroupName": aws.WORKGROUP, "status": "AVAILABLE"}]},
        list_namespaces={"namespaces": [{"namespaceName": "other-ns"}]},
    )

    assert aws.billable_resources() == [f"Redshift Serverless workgroup {aws.WORKGROUP} (AVAILABLE)"]
