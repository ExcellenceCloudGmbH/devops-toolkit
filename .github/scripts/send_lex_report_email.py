#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path

import requests

POSTMARK_API_URL = "https://api.postmarkapp.com/email"


def _send_delivery(
    *,
    delivery: dict,
    api_key: str,
    dry_run: bool,
) -> dict:
    with Path(delivery["email_html_path"]).open("r", encoding="utf-8") as handle:
        html_content = handle.read()

    pdf_path = Path(delivery["pdf_path"]) if delivery["pdf_path"] else None
    pdf_bytes = pdf_path.read_bytes() if pdf_path and pdf_path.exists() else None

    if dry_run:
        return {
            "id": delivery["id"],
            "subject": delivery["subject"],
            "recipients": delivery["recipients"],
            "status": "dry-run",
            "status_code": 0,
        }

    if pdf_bytes is None:
        raise RuntimeError(f"Delivery '{delivery['id']}' is missing its PDF attachment.")

    from_name = delivery["sender"]["from_name"] or None
    from_email = delivery["sender"]["from_email"]
    from_header = f"{from_name} <{from_email}>" if from_name else from_email

    payload = {
        "From": from_header,
        "To": ", ".join(delivery["recipients"]),
        "Subject": delivery["subject"],
        "HtmlBody": html_content,
        "MessageStream": "outbound",
        "Attachments": [
            {
                "Name": delivery["pdf_filename"],
                "Content": base64.b64encode(pdf_bytes).decode("utf-8"),
                "ContentType": "application/pdf",
            }
        ],
    }
    if delivery["sender"]["reply_to"]:
        payload["ReplyTo"] = delivery["sender"]["reply_to"]

    response = requests.post(
        POSTMARK_API_URL,
        headers={
            "X-Postmark-Server-Token": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code >= 300 or body.get("ErrorCode", 0) != 0:
        raise RuntimeError(
            f"Postmark returned {response.status_code} (ErrorCode={body.get('ErrorCode')}) "
            f"for delivery '{delivery['id']}': {body.get('Message')}"
        )

    return {
        "id": delivery["id"],
        "subject": delivery["subject"],
        "recipients": delivery["recipients"],
        "status": "sent",
        "status_code": response.status_code,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Send rendered Lex report emails with Postmark.")
    parser.add_argument("--manifest", required=True, help="Render manifest created by render_lex_test_report.py.")
    parser.add_argument("--out", required=True, help="Where to write the send results JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Validate the manifest without sending.")
    args = parser.parse_args()

    api_key = os.environ.get("POSTMARK_SERVER_TOKEN", "").strip()
    if not args.dry_run and not api_key:
        raise SystemExit("POSTMARK_SERVER_TOKEN is required to send report emails.")

    manifest_path = Path(args.manifest).resolve()
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    deliveries = manifest.get("deliveries", [])
    if not deliveries:
        raise SystemExit("Render manifest contains no deliveries.")

    results = []
    for delivery in deliveries:
        sender = delivery.get("sender", {})
        if not args.dry_run and not sender.get("from_email"):
            raise SystemExit(f"Delivery '{delivery['id']}' is missing sender.from_email.")
        results.append(
            _send_delivery(delivery=delivery, api_key=api_key, dry_run=args.dry_run)
        )

    output = {
        "delivery_count": len(deliveries),
        "sent_count": sum(1 for result in results if result["status"] in {"sent", "dry-run"}),
        "results": results,
    }
    output_path = Path(args.out).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
