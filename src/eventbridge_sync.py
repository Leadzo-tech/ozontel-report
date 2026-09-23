import argparse
import json
import os
from typing import Any

from dataclasses import dataclass

import boto3

from src.report_registry import ReportSpec, load_report_specs


def rule_name(environment: str, report_name: str) -> str:
    return f"oz-{environment}-{report_name}"


def statement_id(name: str) -> str:
    return f"{name}-invoke"


@dataclass(frozen=True)
class DesiredRule:
    spec: ReportSpec
    expression: str
    extra_input: dict[str, Any]


class EventBridgeReconciler:
    def __init__(self, environment: str, lambda_arn: str, lambda_function_name: str | None = None):
        self.environment = environment
        self.lambda_arn = lambda_arn
        self.lambda_function_name = lambda_function_name or lambda_arn.split(":")[-1]
        self.events = boto3.client("events")
        self.lambda_client = boto3.client("lambda")

    @property
    def prefix(self) -> str:
        return f"oz-{self.environment}-"

    def list_actual_rules(self) -> dict[str, dict[str, Any]]:
        paginator = self.events.get_paginator("list_rules")
        rules: dict[str, dict[str, Any]] = {}
        for page in paginator.paginate(NamePrefix=self.prefix):
            for item in page.get("Rules", []):
                rules[item["Name"]] = item
        return rules

    def desired_rules(self, specs: dict[str, ReportSpec]) -> dict[str, DesiredRule]:
        desired: dict[str, DesiredRule] = {}
        for name, spec in specs.items():
            if not spec.enabled:
                continue
            desired[rule_name(self.environment, name)] = DesiredRule(spec, spec.schedule_expression, {})
            # One-off runs (e.g. a backfill) as their own rules, so they need no
            # lambda:InvokeFunction for whoever triggers them. Delete the entry
            # from the spec afterwards and the next deploy removes the rule.
            for run in spec.raw["schedule"].get("extra_runs") or []:
                desired[f"{rule_name(self.environment, name)}--{run['id']}"] = DesiredRule(
                    spec, run["expression"], dict(run.get("input") or {})
                )
        return desired

    def reconcile(self, specs: dict[str, ReportSpec], dry_run: bool = False) -> dict[str, list[str]]:
        desired = self.desired_rules(specs)
        actual = self.list_actual_rules()

        created: list[str] = []
        updated: list[str] = []
        deleted: list[str] = []

        for name, rule in desired.items():
            spec = rule.spec
            existing = actual.get(name)
            needs_update = existing is None or existing.get("ScheduleExpression") != rule.expression
            if dry_run:
                if existing is None:
                    created.append(spec.report_name)
                elif needs_update:
                    updated.append(spec.report_name)
                continue

            if needs_update:
                self.events.put_rule(
                    Name=name,
                    ScheduleExpression=rule.expression,
                    State="ENABLED",
                    Description=f"Ozonetel report rule for {spec.report_name} ({self.environment})",
                    Tags=[
                        {"Key": "Service", "Value": "ozonetel-report"},
                        {"Key": "Environment", "Value": self.environment},
                        {"Key": "ReportName", "Value": spec.report_name},
                        {"Key": "ManagedBy", "Value": "ozonetel-report-ci"},
                    ],
                )
                if existing is None:
                    created.append(spec.report_name)
                else:
                    updated.append(spec.report_name)

            self.events.put_targets(
                Rule=name,
                Targets=[
                    {
                        "Id": "ozonetel-report-worker",
                        "Arn": self.lambda_arn,
                        "Input": json.dumps(
                            {
                                **rule.extra_input,
                                "report_name": spec.report_name,
                                "environment": self.environment,
                            }
                        ),
                    }
                ],
            )
            self._ensure_lambda_permission(name)

        for name in sorted(set(actual.keys()) - set(desired.keys())):
            report_name = name.removeprefix(self.prefix)
            if dry_run:
                deleted.append(report_name)
                continue
            self.events.remove_targets(Rule=name, Ids=["ozonetel-report-worker"], Force=True)
            self.events.delete_rule(Name=name)
            self._remove_lambda_permission(name)
            deleted.append(report_name)

        return {"created_schedules": created, "updated_schedules": updated, "deleted_schedules": deleted}

    def _ensure_lambda_permission(self, name: str) -> None:
        rule = self.events.describe_rule(Name=name)
        try:
            self.lambda_client.add_permission(
                FunctionName=self.lambda_function_name,
                StatementId=statement_id(name),
                Action="lambda:InvokeFunction",
                Principal="events.amazonaws.com",
                SourceArn=rule["Arn"],
            )
        except self.lambda_client.exceptions.ResourceConflictException:
            return

    def _remove_lambda_permission(self, name: str) -> None:
        try:
            self.lambda_client.remove_permission(
                FunctionName=self.lambda_function_name,
                StatementId=statement_id(name),
            )
        except self.lambda_client.exceptions.ResourceNotFoundException:
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile Ozonetel report EventBridge rules")
    parser.add_argument("--environment", default=os.environ.get("ENVIRONMENT", "prod"))
    parser.add_argument("--lambda-arn", required=True)
    parser.add_argument("--lambda-function-name")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    specs = load_report_specs()
    result = EventBridgeReconciler(
        args.environment,
        args.lambda_arn,
        args.lambda_function_name,
    ).reconcile(specs, dry_run=args.dry_run)
    print(json.dumps({"environment": args.environment, "dry_run": args.dry_run, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
