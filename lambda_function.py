import os
import requests
from lib import CommentDownloader
from typing import Any

YOUTUBE_DATA_API_KEY_SECRET_ARN = "YOUTUBE_DATA_API_KEY_SECRET_ARN"


def get_youtube_data_api_key() -> str:
    if secret_name := os.environ.get(YOUTUBE_DATA_API_KEY_SECRET_ARN):
        secrets_extension_endpoint = (
            f"http://localhost:2773/secretsmanager/get?secretId={secret_name}"
        )
        headers = {
            "X-Aws-Parameters-Secrets-Token": os.environ.get("AWS_SESSION_TOKEN")
        }
        response = requests.get(secrets_extension_endpoint, headers=headers)
        response.raise_for_status()
        api_key: str = response.json()["SecretString"]
        return api_key
    raise Exception(
        f"Unset environment variable `{YOUTUBE_DATA_API_KEY_SECRET_ARN}` for ARN of YouTube Data API Key Secret"
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    params = event["queryStringParameters"]
    channel_name = params["channel"]
    print(event)
    print(f"Found params: {params}")
    print(f"Found channel name: {channel_name}")
    api_key = get_youtube_data_api_key()
    comment_downloader = CommentDownloader(api_key)
    output = comment_downloader.digest(
        channel_handle=channel_name,
        limit=500,
    )
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "text/html",
        },
        "body": output,
    }
