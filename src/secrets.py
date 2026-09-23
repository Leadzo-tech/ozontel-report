import json
import os
from functools import lru_cache
from typing import Any

import boto3


@lru_cache(maxsize=1)
def _ssm_client():
    return boto3.client("ssm")


@lru_cache(maxsize=128)
def get_parameter(name: str, decrypt: bool = True) -> str:
    response = _ssm_client().get_parameter(Name=name, WithDecryption=decrypt)
    return response["Parameter"]["Value"]


def get_optional_parameter(name: str, decrypt: bool = True) -> str | None:
    """Like get_parameter but returns None if the parameter does not exist.

    Used for optional config (e.g. the Slack webhook): a missing parameter is a
    normal "not configured" state, not an error, so callers can skip gracefully
    instead of crashing the run with ParameterNotFound.
    """
    client = _ssm_client()
    try:
        response = client.get_parameter(Name=name, WithDecryption=decrypt)
    except client.exceptions.ParameterNotFound:
        return None
    return response["Parameter"]["Value"]


def get_json_parameter(name: str) -> dict[str, Any]:
    return json.loads(get_parameter(name, decrypt=True))


def parameter_prefix() -> str:
    prefix = os.environ.get("SSM_PARAMETER_PREFIX")
    if not prefix:
        raise RuntimeError("SSM_PARAMETER_PREFIX is required")
    return prefix.rstrip("/")


def parameter_name(env_var: str, suffix: str) -> str:
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit
    return f"{parameter_prefix()}/{suffix.lstrip('/')}"
