from typing import Any

import requests


def send_failure_alert(webhook_url: str, payload: dict[str, Any]) -> None:
    message = {
        "text": (
            f"Ozonetel report failure: {payload.get('report_name')} "
            f"({payload.get('environment')})"
        ),
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Ozonetel report failure*\n"
                        f"*Report:* `{payload.get('report_name')}`\n"
                        f"*Environment:* `{payload.get('environment')}`\n"
                        f"*Git SHA:* `{payload.get('git_sha')}`\n"
                        f"*Error:* `{payload.get('error')}`"
                    ),
                },
            }
        ],
    }
    response = requests.post(webhook_url, json=message, timeout=10)
    response.raise_for_status()
