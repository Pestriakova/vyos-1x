import os
import re
import glob
import json
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from google.cloud import bigquery  # <-- NEW

# Какие элементы считаем "CLI сущностями"
TARGET_TAGS = {"node", "leafNode", "tagNode"}

# include строка вида: #include <include/xxx.xml.i>
INCLUDE_RE = re.compile(r'^\s*#include\s*<([^>]+)>\s*$', re.MULTILINE)


def normalize_text(s: str) -> str:
    return s.replace("\u00A0", " ")


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return normalize_text(f.read())


def resolve_include_path(repo_root: str, current_file: str, include_ref: str) -> str:
    p1 = os.path.join(repo_root, "interface-definitions", include_ref)
    if os.path.exists(p1):
        return p1

    p2 = os.path.join(os.path.dirname(current_file), include_ref)
    if os.path.exists(p2):
        return p2

    hits = glob.glob(os.path.join(repo_root, "**", include_ref), recursive=True)
    if hits:
        return hits[0]

    raise FileNotFoundError(f"Include not found: <{include_ref}> referenced from {current_file}")


def expand_includes(repo_root: str, file_path: str, visited: set, depth: int = 0, max_depth: int = 50) -> str:
    if depth > max_depth:
        raise RecursionError(f"Max include depth exceeded at {file_path}")

    norm = os.path.normpath(file_path)
    if norm in visited:
        return ""

    visited.add(norm)
    content = read_file(file_path)

    def repl(match):
        include_ref = match.group(1).strip()
        inc_path = resolve_include_path(repo_root, file_path, include_ref)
        return expand_includes(repo_root, inc_path, visited, depth + 1, max_depth)

    expanded = INCLUDE_RE.sub(repl, content)
    expanded = re.sub(r'<\?xml[^>]*\?>\s*', "", expanded)
    return expanded


def parse_xml_from_file(repo_root: str, file_path: str) -> ET.Element:
    visited = set()
    expanded_body = expand_includes(repo_root, file_path, visited)
    xml_text = '<?xml version="1.0"?>\n' + expanded_body.strip() + "\n"
    return ET.fromstring(xml_text)


def has_child(props: ET.Element, tag: str) -> bool:
    return props is not None and props.find(tag) is not None


def pct(x: int, y: int) -> float:
    return round((float(x) / float(y) * 100.0), 2) if y else 0.0


def write_to_bigquery(row: dict) -> None:
    """
    Вставляет 1 строку в BigQuery.
    Требования:
    - В workflow должен быть настроен GOOGLE_APPLICATION_CREDENTIALS на json-key файл
    - В BQ уже создана таблица vyos-billing-data.ci_metrics.docs_coverage
    """
    project_id = "vyos-billing-data"
    table_id = "vyos-billing-data.ci_metrics.docs_coverage"

    client = bigquery.Client(project=project_id)

    errors = client.insert_rows_json(table_id, [row])
    if errors:
        # ВАЖНО: если прав не хватает / схема не совпала — ошибки будут тут
        raise RuntimeError(f"BigQuery insert failed: {errors}")

    print("✅ Inserted 1 row into BigQuery:", table_id)


def main():
    repo_root = os.path.abspath(os.getcwd())
    files = glob.glob("interface-definitions/**/*.xml.in", recursive=True)

    total_entities = 0
    with_help = 0
    with_value_or_completion = 0
    with_constraint = 0
    with_doc_tag = 0

    parsed_files = 0
    failed_files = 0
    failures = []

    for fp in files:
        try:
            root = parse_xml_from_file(repo_root, fp)
            parsed_files += 1
        except Exception as e:
            failed_files += 1
            failures.append({"file": fp, "error": str(e)})
            continue

        for elem in root.iter():
            if elem.tag in TARGET_TAGS:
                total_entities += 1
                props = elem.find("properties")

                if has_child(props, "help"):
                    with_help += 1

                if has_child(props, "valueHelp") or has_child(props, "completionHelp"):
                    with_value_or_completion += 1

                if has_child(props, "constraint"):
                    with_constraint += 1

                if has_child(props, "documentation"):
                    with_doc_tag += 1

    row = {
        # BigQuery TIMESTAMP: нормально принимает строку ISO с Z
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files_total": len(files),
        "files_parsed": parsed_files,
        "files_failed": failed_files,
        "entities_total": total_entities,
        "help_entities": with_help,
        "help_coverage_pct": pct(with_help, total_entities),
        "value_or_completion_entities": with_value_or_completion,
        "value_or_completion_coverage_pct": pct(with_value_or_completion, total_entities),
        "constraint_entities": with_constraint,
        "constraint_coverage_pct": pct(with_constraint, total_entities),
        "documentation_entities": with_doc_tag,
        "documentation_coverage_pct": pct(with_doc_tag, total_entities),
        # failures_sample у тебя STRING — значит кладём JSON-строкой
        "failures_sample": json.dumps(failures[:10], ensure_ascii=False),
        # если поле есть в таблице (на скрине оно есть) — кладём:
        "value_or_completion_coverage_pct": pct(with_value_or_completion, total_entities),
    }

    # 1) как раньше — печатаем в лог
    print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

    # 2) NEW — пишем в BigQuery
    write_to_bigquery(row)


if __name__ == "__main__":
    main()
