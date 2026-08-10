#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUCCESS_STATUSES = {"success", "passed", "pass", "ok", "green"}
FAILURE_STATUSES = {"failure", "failed", "fail", "error", "errors", "red"}
WARNING_STATUSES = {"warning", "warn", "unstable"}
SKIPPED_STATUSES = {"skipped", "skip", "not_run", "not-run"}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "delivery"


def _as_int(value: Any, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    return int(value)


def _parse_percentage(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("%"):
            normalized = normalized[:-1].strip()
        if not normalized:
            return None
        return float(normalized)
    raise ValueError("Coverage percentage must be a string or number.")


def _normalize_status(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip().lower().replace(" ", "_")
    if normalized in SUCCESS_STATUSES:
        return "success"
    if normalized in FAILURE_STATUSES:
        return "failure"
    if normalized in WARNING_STATUSES:
        return "warning"
    if normalized in SKIPPED_STATUSES:
        return "skipped"
    return normalized or "unknown"


def _status_sort_key(value: str) -> int:
    order = {"failure": 0, "warning": 1, "success": 2, "skipped": 3}
    return order.get(value, 4)


def _normalize_test_status(value: Any) -> str:
    raw = str(value).strip().lower().replace(" ", "_") if value is not None else ""
    if raw in {"error", "errors"}:
        return "error"
    return _normalize_status(value)


def _normalize_emails(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        items = [part.strip() for part in values.split(",")]
    elif isinstance(values, list):
        items = [str(part).strip() for part in values]
    else:
        raise ValueError("Email recipients must be a string or array.")

    unique: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not item:
            continue
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(item)
    return unique


def _load_json(config_json: str, config_path: str, workspace: Path, artifacts_dir: Path) -> dict[str, Any]:
    has_json = bool(config_json.strip())
    has_path = bool(config_path.strip())
    if has_json == has_path:
        raise SystemExit("Provide exactly one of --config-json or --config-path.")

    if has_json:
        loaded = json.loads(config_json)
        if not isinstance(loaded, dict):
            raise SystemExit("Inline report configuration must be a JSON object.")
        return loaded

    resolved_path = _resolve_existing_path(config_path, workspace=workspace, artifacts_dir=artifacts_dir)
    with resolved_path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    if not isinstance(loaded, dict):
        raise SystemExit("Report configuration file must contain a JSON object.")
    return loaded


def _resolve_existing_path(raw_path: str, *, workspace: Path, artifacts_dir: Path) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate.resolve()

    lookup_roots = [workspace, artifacts_dir]
    for root in lookup_roots:
        resolved = (root / raw_path).resolve()
        if resolved.exists():
            return resolved

    if artifacts_dir.exists():
        matches = [path for path in artifacts_dir.rglob(candidate.name) if path.is_file()]
        if len(matches) == 1:
            return matches[0].resolve()
        if len(matches) > 1:
            raise SystemExit(
                f"Ambiguous path '{raw_path}'. Matches: "
                + ", ".join(str(match) for match in matches)
            )

    raise SystemExit(f"Could not resolve existing path '{raw_path}'.")


def _normalize_coverage(value: Any, *, default_label: str = "Coverage") -> dict[str, Any] | None:
    if value is None or value == "":
        return None

    if isinstance(value, dict):
        label = str(value.get("label") or default_label).strip() or default_label
        display = str(value.get("display") or value.get("value") or "").strip()
        percentage_source = value.get("percentage", value.get("percent", value.get("value")))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        label = default_label
        display = ""
        percentage_source = value
    elif isinstance(value, str):
        label = default_label
        display = value.strip()
        percentage_source = value
    else:
        raise SystemExit("coverage must be a string, number, or object when present.")

    try:
        percentage = _parse_percentage(percentage_source)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if percentage is None and not display:
        return None
    if percentage is not None:
        percentage = round(percentage, 1)
    if not display and percentage is not None:
        display = f"{percentage:.1f}%"

    return {
        "label": label,
        "display": display,
        "percentage": percentage,
    }


def _format_duration(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        formatted = f"{float(value):.2f}".rstrip("0").rstrip(".")
        return f"{formatted} s"
    return str(value).strip()


def _normalize_test(raw_test: Any, index: int) -> dict[str, Any]:
    if isinstance(raw_test, str):
        name = raw_test.strip()
        raw_test = {}
    elif isinstance(raw_test, dict):
        name = str(
            raw_test.get("name")
            or raw_test.get("display_name")
            or raw_test.get("nodeid")
            or raw_test.get("id")
            or raw_test.get("test")
            or f"test-{index}"
        ).strip()
    else:
        raise SystemExit("Each group test entry must be a string or object.")

    location = str(raw_test.get("location") or raw_test.get("path") or raw_test.get("file") or "").strip()
    line = raw_test.get("line")
    if location and line not in {None, ""}:
        location = f"{location}:{line}"

    return {
        "name": name,
        "status": _normalize_test_status(raw_test.get("status")),
        "duration": _format_duration(raw_test.get("duration")),
        "details": str(raw_test.get("details") or raw_test.get("message") or "").strip(),
        "location": location,
    }


def _count_tests_by_status(tests: list[dict[str, Any]], status: str) -> int:
    return sum(1 for test in tests if test["status"] == status)


def _initials(value: str) -> str:
    parts = [part[0] for part in re.findall(r"[A-Za-z0-9]+", value)]
    return ("".join(parts[:2]) or "EC").upper()


def _normalize_branding(raw_branding: Any) -> dict[str, str]:
    if raw_branding is None:
        raw_branding = {}
    if not isinstance(raw_branding, dict):
        raise SystemExit("branding must be an object when present.")

    name = str(raw_branding.get("name") or "Excellence Cloud").strip() or "Excellence Cloud"
    return {
        "name": name,
        "logo_text": str(raw_branding.get("logo_text") or _initials(name)).strip() or _initials(name),
        "primary_color": str(raw_branding.get("primary_color") or "#283067").strip() or "#283067",
        "accent_color": str(raw_branding.get("accent_color") or "#24b6bb").strip() or "#24b6bb",
        "surface_color": str(raw_branding.get("surface_color") or "#f5f7fb").strip() or "#f5f7fb",
        "logo_text_color": str(raw_branding.get("logo_text_color") or "#ffffff").strip() or "#ffffff",
    }


def _normalize_group(raw_group: dict[str, Any], index: int) -> dict[str, Any]:
    key = str(raw_group.get("key") or raw_group.get("id") or raw_group.get("name") or f"group-{index}").strip()
    name = str(raw_group.get("name") or key).strip()
    description = str(raw_group.get("description") or "").strip()
    status = _normalize_status(raw_group.get("status"))
    raw_tests = raw_group.get("tests")
    if isinstance(raw_tests, list):
        normalized_tests = [_normalize_test(test, idx) for idx, test in enumerate(raw_tests, start=1)]
        raw_test_count: Any = len(normalized_tests)
    else:
        normalized_tests = []
        raw_test_count = raw_group.get("test_count", raw_tests)

    counts = raw_group.get("counts") if isinstance(raw_group.get("counts"), dict) else {}
    passed = _as_int(
        counts.get("passed", raw_group.get("passed")),
        default=_count_tests_by_status(normalized_tests, "success"),
    )
    failed = _as_int(
        counts.get("failed", raw_group.get("failed")),
        default=_count_tests_by_status(normalized_tests, "failure"),
    )
    skipped = _as_int(
        counts.get("skipped", raw_group.get("skipped")),
        default=_count_tests_by_status(normalized_tests, "skipped"),
    )
    errors = _as_int(
        counts.get("errors", raw_group.get("errors")),
        default=_count_tests_by_status(normalized_tests, "error"),
    )
    tests = _as_int(
        counts.get("tests", raw_test_count),
        default=passed + failed + skipped + errors,
    )

    metadata = raw_group.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise SystemExit(f"Group '{key}' metadata must be an object when present.")
    metadata = metadata or {}
    coverage = _normalize_coverage(raw_group.get("coverage", metadata.get("coverage")))

    return {
        "key": key,
        "name": name,
        "description": description,
        "status": status,
        "receivers": _normalize_emails(raw_group.get("receivers", raw_group.get("recipients"))),
        "counts": {
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errors": errors,
            "tests": tests,
        },
        "tests": normalized_tests,
        "coverage": coverage,
        "metadata": metadata,
    }


def _aggregate_coverage(groups: list[dict[str, Any]]) -> dict[str, Any] | None:
    weighted_total = 0.0
    total_weight = 0
    for group in groups:
        coverage = group.get("coverage")
        if not coverage or coverage.get("percentage") is None:
            continue
        weight = max(group["counts"]["tests"], 1)
        weighted_total += coverage["percentage"] * weight
        total_weight += weight

    if total_weight == 0:
        return None

    percentage = round(weighted_total / total_weight, 1)
    return {
        "label": "Coverage",
        "display": f"{percentage:.1f}%",
        "percentage": percentage,
    }


def _aggregate_groups(groups: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "passed": sum(group["counts"]["passed"] for group in groups),
        "failed": sum(group["counts"]["failed"] for group in groups),
        "skipped": sum(group["counts"]["skipped"] for group in groups),
        "errors": sum(group["counts"]["errors"] for group in groups),
        "tests": sum(group["counts"]["tests"] for group in groups),
    }

    if not groups:
        status = "unknown"
    else:
        status = sorted((group["status"] for group in groups), key=_status_sort_key)[0]

    return {
        "group_count": len(groups),
        "groups_passed": sum(1 for group in groups if group["status"] == "success"),
        "status": status,
        "counts": totals,
        "coverage": _aggregate_coverage(groups),
        "groups_with_coverage": sum(1 for group in groups if group.get("coverage")),
        "groups_with_tests": sum(1 for group in groups if group.get("tests")),
    }


def _normalize_delivery(
    raw_delivery: dict[str, Any],
    *,
    index: int,
    config_title: str,
    config_subject_context: str,
    sender_defaults: dict[str, str],
    global_recipients: list[str],
    group_map: dict[str, dict[str, Any]],
    workspace: Path,
    artifacts_dir: Path,
) -> dict[str, Any]:
    raw_sender = raw_delivery.get("sender")
    if raw_sender is not None and not isinstance(raw_sender, dict):
        raise SystemExit(f"Delivery #{index} sender overrides must be an object.")
    raw_sender = raw_sender or {}

    raw_group_keys = raw_delivery.get("group_keys", raw_delivery.get("groups"))
    if raw_group_keys is None:
        group_keys = list(group_map)
    else:
        if not isinstance(raw_group_keys, list):
            raise SystemExit("Delivery group_keys must be an array when provided.")
        group_keys = [str(item) for item in raw_group_keys]

    groups: list[dict[str, Any]] = []
    missing_groups = [key for key in group_keys if key not in group_map]
    if missing_groups:
        raise SystemExit(
            f"Delivery references unknown groups: {', '.join(sorted(missing_groups))}"
        )
    for key in group_keys:
        groups.append(deepcopy(group_map[key]))

    recipients = _normalize_emails(raw_delivery.get("recipients"))
    if not recipients:
        recipients = list(global_recipients)
    if not recipients:
        raise SystemExit(f"Delivery #{index} has no recipients.")

    delivery_id = str(
        raw_delivery.get("id")
        or raw_delivery.get("key")
        or raw_delivery.get("label")
        or raw_delivery.get("report_title")
        or f"delivery-{index}"
    ).strip()
    report_title = str(raw_delivery.get("report_title") or config_title).strip()
    subject_context = str(
        raw_delivery.get("subject_context")
        or raw_delivery.get("label")
        or report_title
        or config_subject_context
    ).strip()

    email_config = raw_delivery.get("email")
    if email_config is not None and not isinstance(email_config, dict):
        raise SystemExit(f"Delivery '{delivery_id}' email overrides must be an object.")

    report_config = raw_delivery.get("report")
    if report_config is not None and not isinstance(report_config, dict):
        raise SystemExit(f"Delivery '{delivery_id}' report overrides must be an object.")

    pdf_config = raw_delivery.get("pdf")
    if pdf_config is not None and not isinstance(pdf_config, dict):
        raise SystemExit(f"Delivery '{delivery_id}' pdf config must be an object.")
    pdf_config = pdf_config or {}

    mode = str(pdf_config.get("mode") or ("provided" if pdf_config.get("path") else "generate")).strip().lower()
    if mode not in {"generate", "provided"}:
        raise SystemExit(f"Delivery '{delivery_id}' pdf mode must be 'generate' or 'provided'.")

    resolved_pdf_path = None
    if mode == "provided":
        raw_pdf_path = str(pdf_config.get("path") or "").strip()
        if not raw_pdf_path:
            raise SystemExit(f"Delivery '{delivery_id}' requires pdf.path when mode is 'provided'.")
        resolved_pdf_path = str(
            _resolve_existing_path(raw_pdf_path, workspace=workspace, artifacts_dir=artifacts_dir)
        )

    attachment_filename = str(
        pdf_config.get("filename")
        or f"{_slugify(delivery_id)}-report.pdf"
    ).strip()

    sender = {
        "from_email": str(
            raw_delivery.get("from_email")
            or raw_sender.get("from_email")
            or sender_defaults["from_email"]
        ).strip(),
        "from_name": str(
            raw_delivery.get("from_name")
            or raw_sender.get("from_name")
            or sender_defaults["from_name"]
        ).strip(),
        "reply_to": str(
            raw_delivery.get("reply_to")
            or raw_sender.get("reply_to")
            or sender_defaults["reply_to"]
        ).strip(),
    }

    aggregate = _aggregate_groups(groups)

    return {
        "id": delivery_id,
        "slug": _slugify(delivery_id),
        "subject_context": subject_context or config_subject_context or config_title,
        "report_title": report_title,
        "recipients": recipients,
        "sender": sender,
        "groups": groups,
        "group_keys": group_keys,
        "summary": aggregate,
        "email": {
            "headline": str((email_config or {}).get("headline") or "").strip(),
            "intro": str((email_config or {}).get("intro") or "").strip(),
            "outro": str((email_config or {}).get("outro") or "").strip(),
        },
        "report": {
            "subtitle": str((report_config or {}).get("subtitle") or "").strip(),
            "notes": [str(item).strip() for item in (report_config or {}).get("notes", []) if str(item).strip()],
        },
        "pdf": {
            "mode": mode,
            "path": resolved_pdf_path,
            "filename": attachment_filename,
        },
        "metadata": raw_delivery.get("metadata", {}) if isinstance(raw_delivery.get("metadata"), dict) else {},
    }


def _derive_deliveries(
    *,
    title: str,
    subject_context: str,
    sender_defaults: dict[str, str],
    groups: list[dict[str, Any]],
    global_recipients: list[str],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for group in groups:
        receivers = tuple(_normalize_emails(group.get("receivers")))
        if receivers:
            buckets[receivers].append(group)

    deliveries: list[dict[str, Any]] = []
    for index, (receivers, bucket_groups) in enumerate(buckets.items(), start=1):
        group_names = ", ".join(group["name"] for group in bucket_groups[:3])
        if len(bucket_groups) > 3:
            group_names += f" (+{len(bucket_groups) - 3} more)"
        deliveries.append(
            {
                "id": f"group-batch-{index}",
                "slug": f"group-batch-{index}",
                "subject_context": group_names or subject_context or title,
                "report_title": title,
                "recipients": list(receivers),
                "sender": deepcopy(sender_defaults),
                "groups": deepcopy(bucket_groups),
                "group_keys": [group["key"] for group in bucket_groups],
                "summary": _aggregate_groups(bucket_groups),
                "email": {"headline": "", "intro": "", "outro": ""},
                "report": {"subtitle": "", "notes": []},
                "pdf": {
                    "mode": "generate",
                    "path": None,
                    "filename": f"group-batch-{index}-report.pdf",
                },
                "metadata": {},
            }
        )

    if global_recipients:
        deliveries.insert(
            0,
            {
                "id": "all-groups",
                "slug": "all-groups",
                "subject_context": subject_context or title,
                "report_title": title,
                "recipients": list(global_recipients),
                "sender": deepcopy(sender_defaults),
                "groups": deepcopy(groups),
                "group_keys": [group["key"] for group in groups],
                "summary": _aggregate_groups(groups),
                "email": {"headline": "", "intro": "", "outro": ""},
                "report": {"subtitle": "", "notes": []},
                "pdf": {
                    "mode": "generate",
                    "path": None,
                    "filename": "all-groups-report.pdf",
                },
                "metadata": {},
            },
        )

    return deliveries


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize Lex test report email payloads.")
    parser.add_argument("--config-json", default="", help="Inline JSON report configuration.")
    parser.add_argument("--config-path", default="", help="Path to a JSON report configuration file.")
    parser.add_argument("--workspace", required=True, help="Caller workspace root.")
    parser.add_argument("--artifacts-dir", required=True, help="Downloaded artifacts directory.")
    parser.add_argument("--out", required=True, help="Where to write the normalized JSON payload.")
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    artifacts_dir = Path(args.artifacts_dir).resolve()
    raw = _load_json(args.config_json, args.config_path, workspace, artifacts_dir)

    title = str(raw.get("title") or raw.get("report_title") or "Lex test report").strip()
    subject_context = str(raw.get("subject_context") or title).strip()
    generated_at = str(raw.get("generated_at") or datetime.now(timezone.utc).isoformat()).strip()
    overall_status = _normalize_status(raw.get("overall_status", raw.get("status")))
    if overall_status == "unknown":
        overall_status = "failure" if _as_int(raw.get("exit_code"), default=0) else "success"
    exit_code = _as_int(raw.get("exit_code"), default=0 if overall_status == "success" else 1)
    metadata = raw.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise SystemExit("metadata must be an object when present.")
    metadata = metadata or {}

    sender_raw = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
    sender_defaults = {
        "from_email": str(sender_raw.get("from_email") or raw.get("from_email") or "").strip(),
        "from_name": str(sender_raw.get("from_name") or raw.get("from_name") or title).strip(),
        "reply_to": str(sender_raw.get("reply_to") or raw.get("reply_to") or "").strip(),
    }

    groups_raw = raw.get("groups")
    if groups_raw is None:
        groups_raw = []
    if not isinstance(groups_raw, list):
        raise SystemExit("groups must be an array.")

    groups = [_normalize_group(group, index=index) for index, group in enumerate(groups_raw, start=1)]
    group_map = {group["key"]: group for group in groups}
    overall_coverage = _normalize_coverage(raw.get("coverage", metadata.get("coverage"))) or _aggregate_coverage(groups)
    branding = _normalize_branding(raw.get("branding", metadata.get("branding")))
    traceability_raw = raw.get("traceability")
    if traceability_raw is not None and not isinstance(traceability_raw, dict):
        raise SystemExit("traceability must be an object when present.")
    traceability_raw = traceability_raw or {}
    traceability = {
        "duration": str(traceability_raw.get("duration") or raw.get("duration") or "").strip(),
        "build_id": str(traceability_raw.get("build_id") or raw.get("build_id") or "").strip(),
        "branch": str(traceability_raw.get("branch") or raw.get("branch") or "").strip(),
        "run_url": str(traceability_raw.get("run_url") or raw.get("run_url") or "").strip(),
    }

    global_recipients = _normalize_emails(raw.get("recipients", raw.get("global_recipients")))

    deliveries_raw = raw.get("deliveries")
    if deliveries_raw is None:
        deliveries = _derive_deliveries(
            title=title,
            subject_context=subject_context,
            sender_defaults=sender_defaults,
            groups=groups,
            global_recipients=global_recipients,
        )
    else:
        if not isinstance(deliveries_raw, list):
            raise SystemExit("deliveries must be an array when provided.")
        deliveries = [
            _normalize_delivery(
                delivery,
                index=index,
                config_title=title,
                config_subject_context=subject_context,
                sender_defaults=sender_defaults,
                global_recipients=global_recipients,
                group_map=group_map,
                workspace=workspace,
                artifacts_dir=artifacts_dir,
            )
            for index, delivery in enumerate(deliveries_raw, start=1)
        ]

    if not deliveries:
        raise SystemExit("The configuration did not resolve to any deliveries.")

    normalized = {
        "title": title,
        "subject_context": subject_context,
        "generated_at": generated_at,
        "overall_status": overall_status,
        "exit_code": exit_code,
        "sender": sender_defaults,
        "branding": branding,
        "coverage": overall_coverage,
        "traceability": traceability,
        "metadata": metadata,
        "groups": groups,
        "deliveries": deliveries,
        "summary": {
            "delivery_count": len(deliveries),
            "group_count": len(groups),
            "groups_with_receivers": sum(1 for group in groups if group["receivers"]),
        },
    }

    output_path = Path(args.out).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(normalized, handle, indent=2)
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
