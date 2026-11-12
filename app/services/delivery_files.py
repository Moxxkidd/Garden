"""Concrete file writers sharing delivery-v1 and one readable row projection."""

import csv
import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.errors import InputValidationError
from app.services.asset_export import _csv_cell
from app.services.delivery_report import KIND_LABELS, summary_lines

READABLE_COLUMNS = (
    "资产 ID",
    "URL",
    "方法",
    "类型",
    "状态码",
    "身份",
    "最近观察",
    "复核状态",
    "来源引用",
)
CSV_COLUMNS = (
    "schema_version",
    "project_id",
    "revision",
    "dataset",
    "matched",
    "row_kind",
    "filters",
    "coverage_summary",
    "limitations",
    "record",
)
TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parents[1] / "templates"),
    autoescape=select_autoescape(["html"]),
)


def readable_row(row, project_id):
    record = row.get("record", {})
    asset_id = row.get("display_asset_id") or row.get("asset_id") or row["id"]
    review = row.get("review") or {}
    return dict(
        zip(
            READABLE_COLUMNS,
            (
                asset_id,
                record.get("url", row.get("url", "")),
                record.get("method", row.get("method")) or "",
                KIND_LABELS.get(
                    record.get("kind", row.get("kind")),
                    record.get("kind", row.get("kind", row["record_type"])),
                ),
                ", ".join(str(c) for c in record.get("status_codes", [])),
                " · ".join(
                    str(v)
                    for v in (
                        record.get("display_name") or record.get("context"),
                        row.get("identity_key"),
                    )
                    if v
                ),
                record.get("last_seen") or row.get("last_seen") or row.get("seen_at") or "未知",
                {"keep": "人工保留", "ignore": "人工忽略", "unreviewed": "未复核"}.get(
                    review.get("state"), "未复核"
                )
                + (" · 需再复核" if review.get("needs_review") else ""),
                record.get("source_url")
                or (
                    f"/projects/{project_id}/assets?view=relations&project_run_id={row['project_run_id']}"
                    if row["record_type"] == "relation"
                    else f"/projects/{project_id}/assets/{asset_id}"
                ),
            ),
            strict=True,
        )
    )


def write_json(output, metadata, rows):
    output.write(json.dumps(metadata, ensure_ascii=False)[:-1] + ', "items": [')
    first = True
    for row in rows:
        if not first:
            output.write(",\n")
        json.dump(row, output, ensure_ascii=False)
        first = False
    output.write("]}\n")


def write_text(output, document, rows, format, *, evidence_rows=()):
    if format == "json":
        write_json(output, document, rows)
    elif format in {"csv", "readable_csv"}:
        if format == "readable_csv" and document["dataset"] not in {
            "assets",
            "observations",
            "candidates",
        }:
            raise InputValidationError(
                "可读 CSV 适用于资产、身份观察或候选；其他数据集请用机器 CSV。"
            )
        if format == "readable_csv":
            output.write("\ufeff")
        writer = csv.DictWriter(
            output, fieldnames=READABLE_COLUMNS if format == "readable_csv" else CSV_COLUMNS
        )
        writer.writeheader()
        count = 0
        for row in rows:
            values = (
                readable_row(row, document["project_id"])
                if format == "readable_csv"
                else {
                    **{k: document[k] for k in CSV_COLUMNS if k in document},
                    "row_kind": "record",
                    "record": row,
                }
            )
            writer.writerow({k: _csv_cell(v) for k, v in values.items()})
            count += 1
        if not count and format == "csv":
            writer.writerow(
                {
                    **{k: _csv_cell(document[k]) for k in CSV_COLUMNS if k in document},
                    "row_kind": "metadata",
                }
            )
    elif format == "markdown":
        output.write("# Garden 资产交付报告\n\n## 概要\n\n")
        from html import escape

        for line in summary_lines(document):
            output.write(escape(line) + "\n\n")
        output.write(
            "筛选："
            + escape(json.dumps(document["filters"], ensure_ascii=False))
            + "\n\n## 资产\n\n"
        )
        output.write(
            "| " + " | ".join(READABLE_COLUMNS) + " |\n|" + "---|" * len(READABLE_COLUMNS) + "\n"
        )
        for row in rows:
            values = readable_row(row, document["project_id"])
            output.write(
                "| "
                + " | ".join(
                    escape(str(v)).replace("|", "&#124;").replace("\n", " ")
                    for v in values.values()
                )
                + " |\n"
            )
        output.write("\n## 证据与公共术语\n\n")
        for line in document["report"]["glossary"] + document["report"]["evidence"]["not_included"]:
            output.write(escape(line) + "\n\n")
        for source in document["source_coverage"]:
            scope = source["scope"]
            link = (
                f"/scans/{source['run_id']}"
                if source["source"] == "scan"
                else f"/inventory/{source['run_id']}"
            )
            output.write(
                f"- [项目运行 #{source['project_run_id']}]({link})：{source['index_status']} / "
                f"{scope.get('status', 'unknown')} / {scope.get('completeness') or 'unknown'}\n"
            )
        from urllib.parse import urlencode

        filters = {
            k: v
            for k, v in document["filters"].items()
            if v is not None and k not in {"before_run_id", "after_run_id"}
        }
        filters["view"] = (
            "observations" if document["dataset"] == "variants" else document["dataset"]
        )
        link = f"/projects/{document['project_id']}/assets?{urlencode(filters)}"
        if document["dataset"] == "changes":
            link = f"/projects/{document['project_id']}/compare?" + urlencode(
                {k: document["filters"][k] for k in ("before_run_id", "after_run_id")}
            )
        output.write(f"\n[查看筛选清单与来源证据]({link})\n")
    elif format == "html":
        template = TEMPLATES.get_template("delivery_offline.html")
        filters = {
            k: v
            for k, v in document["filters"].items()
            if v is not None
            and v != ""
            and not (k in {"collection", "sort", "order"} and v in {"all", "id", "asc"})
        }
        for part in template.generate(
            document=document,
            filters_json=json.dumps(filters, ensure_ascii=False) if filters else "全部已保存记录",
            lines=summary_lines(document),
            rows=rows,
            evidence_rows=evidence_rows,
            readable=readable_row,
            columns=READABLE_COLUMNS,
        ):
            output.write(part)
    elif format == "openapi":
        from app.services.delivery_openapi import openapi_draft

        if document["dataset"] != "observations":
            raise InputValidationError("OpenAPI 草稿需要选择身份观察数据集。")
        json.dump(openapi_draft(dict(document, items=rows)), output, ensure_ascii=False, indent=2)
        output.write("\n")
    else:
        raise InputValidationError("未知交付格式。")


class PdfUnavailableError(RuntimeError):
    pass


def render_pdf(html_path, destination, check):
    """Chromium reads only this generated HTML; all other requests are blocked."""
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(channel="chromium", headless=True)
        except PlaywrightError as error:
            raise PdfUnavailableError("pdf_unavailable") from error
        try:
            context = browser.new_context(offline=True, service_workers="block")
            uri = html_path.resolve().as_uri()
            context.route(
                "**/*",
                lambda route: route.continue_() if route.request.url == uri else route.abort(),
            )
            page = context.new_page()
            page.goto(uri, wait_until="load", timeout=30000)
            page.emulate_media(media="print")
            page.evaluate("() => document.fonts.ready")
            # Playwright's pdf() collects the entire protocol stream into bytes.
            # Use that same Chromium primitive but copy its stream in bounded blocks.
            import base64

            client = context.new_cdp_session(page)
            result = client.send(
                "Page.printToPDF",
                {
                    "transferMode": "ReturnAsStream",
                    "printBackground": True,
                    "preferCSSPageSize": True,
                    "paperWidth": 8.27,
                    "paperHeight": 11.7,
                    "displayHeaderFooter": True,
                    "headerTemplate": "<span></span>",
                    "footerTemplate": (
                        '<div style="width:100%;text-align:center;font-size:8px;color:#52665b">'
                        'Garden · <span class="pageNumber"></span> / '
                        '<span class="totalPages"></span></div>'
                    ),
                },
            )
            handle = result["stream"]
            try:
                with destination.open("wb") as output:
                    while True:
                        check()
                        block = client.send("IO.read", {"handle": handle, "size": 65536})
                        output.write(
                            base64.b64decode(block["data"])
                            if block.get("base64Encoded")
                            else block["data"].encode()
                        )
                        if block["eof"]:
                            break
            finally:
                client.send("IO.close", {"handle": handle})
        finally:
            browser.close()


def read_rows(path, check):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            check()
            yield json.loads(line)


def render_snapshot(directory, format, check):
    """Every artifact in a ZIP reads the same immutable, redacted disk snapshot."""
    import zipfile

    from app.schemas.delivery import DeliveryDocument

    document = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    rows_path = directory / "items.jsonl"
    evidence_path = (
        directory / "observations.jsonl" if document["dataset"] == "assets" else rows_path
    )

    def write_format(name, selected):
        path = directory / name
        rows = read_rows(evidence_path if selected == "readable_csv" else rows_path, check)
        with path.open("w", encoding="utf-8", newline="") as output:
            write_text(
                output, document, rows, selected, evidence_rows=read_rows(evidence_path, check)
            )
        return path

    extension = {"readable_csv": "csv", "markdown": "md", "openapi": "json"}.get(format, format)
    destination = directory / f"export.{extension}.partial"
    if format == "pdf":
        html = write_format("report.html", "html")
        check()
        render_pdf(html, destination, check)
    elif format == "zip":
        members = [
            write_format("assets.json", "json"),
            write_format("records.csv", "csv"),
            write_format("report.html", "html"),
            write_format("report.md", "markdown"),
        ]
        if document["dataset"] in {"assets", "observations", "candidates"}:
            members.append(write_format("readable.csv", "readable_csv"))
        for name, value in (
            ("metadata.json", document),
            ("schema.json", DeliveryDocument.model_json_schema()),
        ):
            path = directory / name
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            members.append(path)
        index = directory / "sources.json"

        def references():
            for row in read_rows(evidence_path, check):
                value = readable_row(row, document["project_id"])
                yield dict(
                    record_id=row["id"],
                    asset_id=value["资产 ID"],
                    identity=value["身份"],
                    reference=value["来源引用"],
                    source_available=row.get("source_available"),
                    evidence_ids=row.get("record", {}).get("evidence_ids"),
                )

        with index.open("w", encoding="utf-8") as output:
            write_json(
                output,
                {
                    k: document[k]
                    for k in ("schema_version", "project_id", "revision", "source_coverage")
                },
                references(),
            )
        members.append(index)
        fields = directory / "FIELDS.md"
        fields.write_text(
            "# Garden 交付字段说明\n\n统一快照见 metadata.json；"
            "机器清单为 delivery-v1，字段定义见 schema.json。\n\n可读 CSV 列："
            + "、".join(READABLE_COLUMNS)
            + "。资产主体视图的可读 CSV 按匹配的身份观察逐行输出；同一主体可有多行。"
            "UTF-8 BOM 便于表格软件显示中文，公式前导字符加单引号。旧机器 CSV 列保持不变。\n\n"
            "报告的主体数、观察数和数据集记录数分别计数。人工保留不代表业务有效。"
            "来源只含公开引用，未包含原始正文、截图、Cookie、令牌和私有会话文件。"
            "缺失的历史预算标为未知。\n",
            encoding="utf-8",
        )
        members.append(fields)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member in members:
                with member.open("rb") as source, archive.open(member.name, "w") as output:
                    while block := source.read(65536):
                        check()
                        output.write(block)
    else:
        write_format(destination.name, format)
    check()
    return destination
