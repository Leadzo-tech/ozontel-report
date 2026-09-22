import json
from functools import lru_cache
from typing import Any

import boto3


@lru_cache(maxsize=1)
def _ssm_client():
    return boto3.client("ssm")


@lru_cache(maxsize=8)
def get_parameter(name: str, decrypt: bool = True) -> str:
    response = _ssm_client().get_parameter(Name=name, WithDecryption=decrypt)
    return response["Parameter"]["Value"]


def get_json_parameter(name: str) -> dict[str, Any]:
    return json.loads(get_parameter(name, decrypt=True))
