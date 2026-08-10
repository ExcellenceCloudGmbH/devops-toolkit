#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


def _status_badge(value: str) -> str:
    mapping = {
        "success": "Success",
        "failure": "Failure",
        "error": "Error",
        "warning": "Warning",
        "skipped": "Skipped",
    }
    return mapping.get(value, value.replace("_", " ").title())


def _status_tone(value: str) -> str:
    mapping = {
        "success": "#0f766e",
        "failure": "#b91c1c",
        "error": "#b91c1c",
        "warning": "#475569",
        "skipped": "#475569",
    }
    return mapping.get(value, "#1f2937")


def _format_timestamp(value: str) -> str:
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    except ValueError:
        return value


def _build_subject(config: dict[str, Any], delivery: dict[str, Any]) -> str:
    marker_map = {
        "success": "SUCCESS",
        "failure": "FAILURE",
        "warning": "WARNING",
        "skipped": "SKIPPED",
    }
    marker = marker_map.get(delivery["summary"]["status"], config["overall_status"].upper())
    subject_context = delivery["subject_context"] or config["subject_context"] or config["title"]
    return f"[{marker}] {subject_context}"


def _write_pdf(html_content: str, output_path: Path) -> None:
    from weasyprint import HTML  # type: ignore

    HTML(string=html_content, base_url=str(output_path.parent)).write_pdf(str(output_path))


def _asset_data_uri(asset_path: Path) -> str:
    mime_type = mimetypes.guess_type(asset_path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(asset_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_branding(template_dir: Path, branding: dict[str, Any]) -> dict[str, Any]:
    resolved = dict(branding)
    logo_path = template_dir / "assets" / "lex-logo.png"
    if logo_path.exists():
        resolved["logo_image_uri"] = _asset_data_uri(logo_path)
    resolved.setdefault("logo_alt", resolved.get("name", "Lex"))
    return resolved


def _build_environment(template_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(("html", "xml")),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["status_badge"] = _status_badge
    environment.filters["status_tone"] = _status_tone
    environment.filters["format_timestamp"] = _format_timestamp
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description="Render Lex test report email/report assets.")
    parser.add_argument("--config", required=True, help="Normalized configuration file.")
    parser.add_argument("--template-dir", required=True, help="Directory containing the HTML templates.")
    parser.add_argument("--email-template", default="email.html", help="Email template filename.")
    parser.add_argument("--pdf-template", default="pdf.html", help="PDF template filename.")
    parser.add_argument("--out-dir", required=True, help="Directory where rendered artifacts should be written.")
    parser.add_argument("--manifest-out", required=True, help="Render manifest output path.")
    parser.add_argument("--skip-pdf", action="store_true", help="Render HTML only.")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    template_dir = Path(args.template_dir).resolve()
    output_dir = Path(args.out_dir).resolve()
    manifest_out = Path(args.manifest_out).resolve()

    with config_path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    config = dict(config)
    config["branding"] = _build_branding(template_dir, config.get("branding") or {})

    environment = _build_environment(template_dir)
    email_template = environment.get_template(args.email_template)
    report_template = environment.get_template(args.pdf_template)

    output_dir.mkdir(parents=True, exist_ok=True)
    deliveries_manifest: list[dict[str, Any]] = []

    for delivery in config["deliveries"]:
        delivery_dir = output_dir / delivery["slug"]
        delivery_dir.mkdir(parents=True, exist_ok=True)

        subject = _build_subject(config, delivery)
        attachment_path = delivery_dir / delivery["pdf"]["filename"]
        email_html_path = delivery_dir / "email.html"
        report_html_path = delivery_dir / "report.html"

        template_context = {
            "config": config,
            "delivery": delivery,
            "groups": delivery["groups"],
            "summary": delivery["summary"],
            "coverage": delivery["summary"].get("coverage") or config.get("coverage"),
            "has_group_coverage": any(group.get("coverage") for group in delivery["groups"]),
            "has_group_tests": any(group.get("tests") for group in delivery["groups"]),
            "subject": subject,
        }

        email_html = email_template.render(**template_context)
        report_html = report_template.render(**template_context)
        email_html_path.write_text(email_html, encoding="utf-8")
        report_html_path.write_text(report_html, encoding="utf-8")

        if delivery["pdf"]["mode"] == "provided":
            shutil.copyfile(delivery["pdf"]["path"], attachment_path)
        elif not args.skip_pdf:
            _write_pdf(report_html, attachment_path)

        deliveries_manifest.append(
            {
                "id": delivery["id"],
                "slug": delivery["slug"],
                "subject": subject,
                "recipients": delivery["recipients"],
                "sender": delivery["sender"],
                "status": delivery["summary"]["status"],
                "email_html_path": str(email_html_path),
                "report_html_path": str(report_html_path),
                "pdf_path": str(attachment_path) if attachment_path.exists() else "",
                "pdf_filename": delivery["pdf"]["filename"],
                "group_keys": delivery["group_keys"],
            }
        )

    manifest = {
        "overall_status": config["overall_status"],
        "delivery_count": len(deliveries_manifest),
        "deliveries": deliveries_manifest,
    }

    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    with manifest_out.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
