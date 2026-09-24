"""The same pipeline on AWS: S3 for the lake, Glue and EMR Serverless for Spark,
Redshift Serverless as the warehouse.

Nothing here changes what the pipeline computes. The Spark script is the one
tests/test_spark_conform.py holds to the pandas adapter, uploaded unchanged;
the SQL is the repository's, translated by rootsignal.sql.dialect.to_redshift.

Everything that bills is created with a ceiling and removed by teardown():

    Glue            2 workers, a 20-minute timeout
    EMR Serverless  capped at 8 vCPU, stops itself after 2 idle minutes
    Redshift        8 RPU, with a usage limit that switches the workgroup off
                    after 4 RPU-hours in a day

The bucket and the IAM roles cost nothing to keep and are left in place.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import pandas as pd

from ..sql.database import ANALYTICS_DIR, LOAD_ORDER, MARTS_DIR, SCHEMA_PATH, STAGING_DIR, _sql_files
from ..sql.dialect import _strip_comments, to_redshift
from .lake import CLEAN, CURATED, RAW, Lake
from .stages import RAW_CSV, publish_job

PREFIX = "rootsignal"
GLUE_JOB = f"{PREFIX}-conform"
EMR_APP = f"{PREFIX}-conform"
NAMESPACE = f"{PREFIX}-ns"
WORKGROUP = f"{PREFIX}-wg"
DATABASE = "dev"

GLUE_ROLE = f"{PREFIX}-glue"
EMR_ROLE = f"{PREFIX}-emr-serverless"
REDSHIFT_ROLE = f"{PREFIX}-redshift"


def _client(service: str):
    import boto3

    return boto3.client(service)


def account_id() -> str:
    return _client("sts").get_caller_identity()["Account"]


def region() -> str:
    import boto3

    return boto3.session.Session().region_name


def default_bucket() -> str:
    return f"{PREFIX}-lake-{account_id()}-{region()}"


# --------------------------------------------------------------------------
# Storage and access
# --------------------------------------------------------------------------


def ensure_bucket(name: str) -> str:
    """A private bucket for the lake. Public access is blocked outright."""
    s3 = _client("s3")
    try:
        s3.head_bucket(Bucket=name)
    except s3.exceptions.ClientError:
        s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": region()})
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    return name


def _lake_policy(bucket: str, write: bool) -> dict:
    actions = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"]
    if write:
        actions += ["s3:PutObject", "s3:DeleteObject"]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }


def ensure_role(
    name: str, services: list[str], bucket: str, write: bool, managed: tuple[str, ...] = ()
) -> str:
    """A role the services can assume, allowed into this bucket and nowhere else."""
    iam = _client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": services}, "Action": "sts:AssumeRole"}],
    }
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        created = False
    except iam.exceptions.NoSuchEntityException:
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        created = True
    iam.put_role_policy(
        RoleName=name, PolicyName=f"{PREFIX}-lake", PolicyDocument=json.dumps(_lake_policy(bucket, write))
    )
    for policy in managed:
        iam.attach_role_policy(RoleName=name, PolicyArn=policy)
    if created:
        # A new role takes a few seconds to become assumable everywhere.
        time.sleep(15)
    return arn


# --------------------------------------------------------------------------
# Spark: Glue and EMR Serverless
# --------------------------------------------------------------------------


@dataclass
class SparkRun:
    engine: str
    state: str
    seconds: float
    usage: str
    output: str
    error: str = ""


def run_glue(lake: Lake, role_arn: str, output: str | None = None, workers: int = 2) -> SparkRun:
    """Run the conform script as a Glue Spark job and wait for it."""
    glue = _client("glue")
    output = output or lake.uri(CURATED)
    job = {
        "Role": role_arn,
        "Command": {"Name": "glueetl", "ScriptLocation": publish_job(lake), "PythonVersion": "3"},
        "GlueVersion": "5.0",
        "WorkerType": "G.1X",
        "NumberOfWorkers": workers,
        "Timeout": 20,
        "MaxRetries": 0,
        "DefaultArguments": {"--input": lake.uri(RAW, RAW_CSV), "--output": output},
    }
    try:
        glue.get_job(JobName=GLUE_JOB)
        glue.update_job(JobName=GLUE_JOB, JobUpdate=job)
    except glue.exceptions.EntityNotFoundException:
        glue.create_job(Name=GLUE_JOB, **job)

    run_id = glue.start_job_run(JobName=GLUE_JOB)["JobRunId"]
    while True:
        run = glue.get_job_run(JobName=GLUE_JOB, RunId=run_id)["JobRun"]
        if run["JobRunState"] in {"SUCCEEDED", "FAILED", "ERROR", "TIMEOUT", "STOPPED"}:
            break
        time.sleep(15)
    seconds = float(run.get("ExecutionTime", 0))
    # Glue bills per second with a one-minute minimum, per worker (1 DPU each on G.1X).
    dpu_hours = max(seconds, 60) / 3600 * workers
    return SparkRun(
        engine="Glue 5.0",
        state=run["JobRunState"],
        seconds=seconds,
        usage=f"{dpu_hours:.3f} DPU-hours",
        output=output,
        error=run.get("ErrorMessage", ""),
    )


def ensure_emr_application() -> str:
    """An EMR Serverless Spark application with a hard capacity ceiling."""
    emr = _client("emr-serverless")
    for app in emr.list_applications()["applications"]:
        if app["name"] == EMR_APP and app["state"] not in {"TERMINATED"}:
            return app["id"]
    return emr.create_application(
        name=EMR_APP,
        releaseLabel="emr-7.5.0",
        type="SPARK",
        maximumCapacity={"cpu": "8 vCPU", "memory": "32 GB", "disk": "100 GB"},
        autoStartConfiguration={"enabled": True},
        autoStopConfiguration={"enabled": True, "idleTimeoutMinutes": 2},
    )["applicationId"]


def run_emr(lake: Lake, role_arn: str, output: str) -> SparkRun:
    """Run the same conform script on EMR Serverless and wait for it."""
    emr = _client("emr-serverless")
    app_id = ensure_emr_application()
    submit = " ".join(
        [
            "--conf spark.dynamicAllocation.enabled=false",
            "--conf spark.executor.instances=2",
            "--conf spark.executor.cores=1",
            "--conf spark.executor.memory=2g",
            "--conf spark.driver.cores=1",
            "--conf spark.driver.memory=2g",
        ]
    )
    job_id = emr.start_job_run(
        applicationId=app_id,
        executionRoleArn=role_arn,
        name="conform-online-retail",
        jobDriver={
            "sparkSubmit": {
                "entryPoint": publish_job(lake),
                "entryPointArguments": ["--input", lake.uri(RAW, RAW_CSV), "--output", output],
                "sparkSubmitParameters": submit,
            }
        },
        configurationOverrides={
            "monitoringConfiguration": {"s3MonitoringConfiguration": {"logUri": lake.uri("logs", "emr")}}
        },
        executionTimeoutMinutes=20,
    )["jobRunId"]
    while True:
        run = emr.get_job_run(applicationId=app_id, jobRunId=job_id)["jobRun"]
        if run["state"] in {"SUCCESS", "FAILED", "CANCELLED"}:
            break
        time.sleep(15)
    used = run.get("totalResourceUtilization", {})
    return SparkRun(
        engine=f"EMR Serverless {run.get('releaseLabel', 'emr-7.5.0')}",
        state=run["state"],
        seconds=float(run.get("totalExecutionDurationSeconds", 0)),
        usage=f"{used.get('vCPUHour', 0):.3f} vCPU-hours, {used.get('memoryGBHour', 0):.3f} GB-hours",
        output=output,
        error=run.get("stateDetails", ""),
    )


# --------------------------------------------------------------------------
# Redshift Serverless
# --------------------------------------------------------------------------


def _default_network() -> tuple[list[str], list[str]]:
    ec2 = _client("ec2")
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    subnets = [
        subnet["SubnetId"]
        for subnet in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]
    ]
    group = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc]}, {"Name": "group-name", "Values": ["default"]}]
    )["SecurityGroups"][0]["GroupId"]
    return subnets, [group]


def ensure_redshift(role_arn: str, base_capacity: int = 8, daily_rpu_hours: int = 4) -> str:
    """A Redshift Serverless workgroup at the smallest size, with a spending cap.

    The usage limit deactivates the workgroup once it has used daily_rpu_hours
    of compute in a day, so a forgotten workgroup cannot run up a bill.
    """
    rs = _client("redshift-serverless")
    try:
        rs.get_namespace(namespaceName=NAMESPACE)
    except rs.exceptions.ResourceNotFoundException:
        rs.create_namespace(
            namespaceName=NAMESPACE, dbName=DATABASE, iamRoles=[role_arn], defaultIamRoleArn=role_arn
        )
    try:
        workgroup = rs.get_workgroup(workgroupName=WORKGROUP)["workgroup"]
    except rs.exceptions.ResourceNotFoundException:
        subnets, groups = _default_network()
        workgroup = rs.create_workgroup(
            workgroupName=WORKGROUP,
            namespaceName=NAMESPACE,
            baseCapacity=base_capacity,
            maxCapacity=base_capacity,
            publiclyAccessible=False,
            subnetIds=subnets,
            securityGroupIds=groups,
        )["workgroup"]
    while workgroup["status"] != "AVAILABLE":
        time.sleep(15)
        workgroup = rs.get_workgroup(workgroupName=WORKGROUP)["workgroup"]

    limits = rs.list_usage_limits(resourceArn=workgroup["workgroupArn"]).get("usageLimits", [])
    if not limits:
        rs.create_usage_limit(
            resourceArn=workgroup["workgroupArn"],
            usageType="serverless-compute",
            amount=daily_rpu_hours,
            period="daily",
            breachAction="deactivate",
        )
    return workgroup["workgroupArn"]


def execute(statements: list[str], timeout: int = 900) -> list[str]:
    """Run statements in one transaction through the Redshift Data API."""
    data = _client("redshift-data")
    statements = [sql for sql in statements if sql.strip()]
    if len(statements) == 1:
        response = data.execute_statement(WorkgroupName=WORKGROUP, Database=DATABASE, Sql=statements[0])
    else:
        response = data.batch_execute_statement(WorkgroupName=WORKGROUP, Database=DATABASE, Sqls=statements)
    started = time.time()
    while True:
        described = data.describe_statement(Id=response["Id"])
        if described["Status"] in {"FINISHED", "FAILED", "ABORTED"}:
            break
        if time.time() - started > timeout:
            data.cancel_statement(Id=response["Id"])
            raise TimeoutError(f"Redshift statement did not finish in {timeout}s")
        time.sleep(2)
    if described["Status"] != "FINISHED":
        raise RuntimeError(f"Redshift: {described.get('Error', described['Status'])}")
    if len(statements) == 1:
        return [response["Id"]]
    return [part["Id"] for part in described["SubStatements"]]


def fetch(sql: str, schema: str | None = None) -> pd.DataFrame:
    """A SELECT through the Data API, as a frame.

    Each Data API call is its own session, so a search_path set in one call is
    gone by the next. With a schema, the SET and the SELECT go in one batch and
    the SELECT's result is read.
    """
    data = _client("redshift-data")
    statements = [f"SET search_path TO {schema}", sql] if schema else [sql]
    statement_id = execute(statements)[-1]
    columns, rows, token = None, [], None
    while True:
        kwargs = {"Id": statement_id, **({"NextToken": token} if token else {})}
        page = data.get_statement_result(**kwargs)
        columns = columns or [column["name"] for column in page["ColumnMetadata"]]
        for record in page["Records"]:
            rows.append([None if field.get("isNull") else next(iter(field.values())) for field in record])
        token = page.get("NextToken")
        if not token:
            break
    return pd.DataFrame(rows, columns=columns)


def statements_in(sql: str) -> list[str]:
    """The statements in a SQL file, comments removed first.

    Splitting on semicolons before removing comments would cut a statement at a
    semicolon inside a comment, and schema.sql's second line has one.
    """
    return [part.strip() for part in _strip_comments(sql).split(";") if part.strip()]


def load_redshift(lake: Lake, schema: str) -> dict[str, int]:
    """Create the schema, COPY the clean zone into it, and register the views."""
    statements = [
        f"DROP SCHEMA IF EXISTS {schema} CASCADE",
        f"CREATE SCHEMA {schema}",
        f"SET search_path TO {schema}",
        *statements_in(to_redshift(SCHEMA_PATH.read_text(encoding="utf-8"))),
    ]
    for name in LOAD_ORDER:
        statements.append(f"COPY {name} FROM '{lake.uri(CLEAN, name)}/' IAM_ROLE default FORMAT AS PARQUET")
    execute(statements)

    views = [f"SET search_path TO {schema}"]
    for directory in (STAGING_DIR, MARTS_DIR):
        for path in _sql_files(directory):
            views += statements_in(to_redshift(path.read_text(encoding="utf-8")))
    execute(views)

    counts = fetch(" UNION ALL ".join(f"SELECT '{name}' AS name, COUNT(*) AS n FROM {name}" for name in LOAD_ORDER), schema)
    return dict(zip(counts["name"], counts["n"].astype(int), strict=True))


def query_redshift(schema: str, name: str) -> pd.DataFrame:
    """One of the analytical queries, run inside `schema`."""
    (sql,) = statements_in(to_redshift((ANALYTICS_DIR / f"{name}.sql").read_text(encoding="utf-8")))
    return fetch(sql, schema)


# --------------------------------------------------------------------------
# What is running, and removing it
# --------------------------------------------------------------------------


def billable_resources() -> list[str]:
    """Everything this module creates that can bill while it exists."""
    found = []
    glue = _client("glue")
    try:
        glue.get_job(JobName=GLUE_JOB)
        found.append(f"Glue job {GLUE_JOB} (bills only while running)")
    except glue.exceptions.EntityNotFoundException:
        pass
    for app in _client("emr-serverless").list_applications()["applications"]:
        if app["name"] == EMR_APP and app["state"] != "TERMINATED":
            found.append(f"EMR Serverless application {app['id']} ({app['state']})")
    rs = _client("redshift-serverless")
    for workgroup in rs.list_workgroups()["workgroups"]:
        if workgroup["workgroupName"] == WORKGROUP:
            found.append(f"Redshift Serverless workgroup {WORKGROUP} ({workgroup['status']})")
    for namespace in rs.list_namespaces()["namespaces"]:
        if namespace["namespaceName"] == NAMESPACE:
            found.append(f"Redshift Serverless namespace {NAMESPACE} (storage)")
    return found


def teardown() -> list[str]:
    """Remove every billable resource. The bucket and roles are kept."""
    removed = []
    glue = _client("glue")
    # DeleteJob succeeds whether or not the job exists, so asking first is the
    # only way to report a removal that happened rather than one that did not.
    try:
        glue.get_job(JobName=GLUE_JOB)
        glue.delete_job(JobName=GLUE_JOB)
        removed.append(f"Glue job {GLUE_JOB}")
    except glue.exceptions.EntityNotFoundException:
        pass

    emr = _client("emr-serverless")
    for app in emr.list_applications()["applications"]:
        if app["name"] != EMR_APP or app["state"] == "TERMINATED":
            continue
        if app["state"] not in {"STOPPED", "CREATED"}:
            emr.stop_application(applicationId=app["id"])
            while emr.get_application(applicationId=app["id"])["application"]["state"] not in {"STOPPED"}:
                time.sleep(10)
        emr.delete_application(applicationId=app["id"])
        removed.append(f"EMR Serverless application {app['id']}")

    rs = _client("redshift-serverless")
    try:
        rs.get_workgroup(workgroupName=WORKGROUP)
        rs.delete_workgroup(workgroupName=WORKGROUP)
        while True:
            try:
                rs.get_workgroup(workgroupName=WORKGROUP)
                time.sleep(15)
            except rs.exceptions.ResourceNotFoundException:
                break
        removed.append(f"Redshift Serverless workgroup {WORKGROUP}")
    except rs.exceptions.ResourceNotFoundException:
        pass
    try:
        rs.delete_namespace(namespaceName=NAMESPACE)
        while True:
            try:
                rs.get_namespace(namespaceName=NAMESPACE)
                time.sleep(15)
            except rs.exceptions.ResourceNotFoundException:
                break
        removed.append(f"Redshift Serverless namespace {NAMESPACE}")
    except rs.exceptions.ResourceNotFoundException:
        pass
    return removed
