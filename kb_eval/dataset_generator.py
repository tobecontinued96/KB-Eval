"""Generate retrieval evaluation JSONL datasets from Markdown sources."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kb_eval.dataset import load_samples
from kb_eval.errors import EvalError


@dataclass(frozen=True)
class MarkdownSource:
    path: Path
    document_name: str
    text: str


@dataclass(frozen=True)
class MarkdownSection:
    title: str
    heading_path: list[str]
    body: str
    source_path: Path
    document_name: str


@dataclass(frozen=True)
class MinerUConversionResult:
    markdown_path: Path
    command: list[str]
    stdout: str
    stderr: str


SCENARIO_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("故障恢复", ("故障", "恢复", "异常", "失败", "无法", "错误", "告警", "密码", "清除", "丢失", "重启", "reset")),
    ("查询诊断", ("查询", "查看", "检查", "诊断", "状态", "日志", "display", "show", "确认", "排查")),
    ("安全与准入", ("安全", "认证", "acl", "aaa", "nac", "ipsg", "snooping", "攻击", "准入", "权限")),
    ("协议特性", ("stp", "rstp", "ospf", "vrrp", "snmp", "lldp", "dhcp", "协议", "路由")),
]

COMMAND_PREFIXES = (
    "display",
    "reset",
    "save",
    "reboot",
    "system-view",
    "interface",
    "vlan",
    "undo",
    "stp",
    "dhcp",
    "acl",
    "aaa",
    "ip",
    "port",
    "snmp",
    "shutdown",
)

CHINESE_STOPWORDS = {
    "配置",
    "操作",
    "常见",
    "说明",
    "步骤",
    "介绍",
    "示例",
    "问题",
    "方法",
}

VENDOR_ID_PREFIXES = {
    "华为": "HW",
    "HUAWEI": "HW",
    "Huawei": "HW",
}


def read_markdown(path: Path, *, document_name: str | None = None) -> MarkdownSource:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8-sig")
    return MarkdownSource(
        path=path,
        document_name=document_name or path.with_suffix(".pdf").name,
        text=normalize_markdown(text),
    )


def normalize_markdown(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    return text


def iter_markdown_sections(source: MarkdownSource, *, min_section_chars: int = 80) -> list[MarkdownSection]:
    sections: list[MarkdownSection] = []
    heading_stack: list[str] = []
    current_title = source.path.stem
    current_path = [current_title]
    body_lines: list[str] = []

    def flush() -> None:
        body = "\n".join(body_lines).strip()
        if len(compact_text(body)) < min_section_chars:
            return
        sections.append(
            MarkdownSection(
                title=clean_heading(current_title),
                heading_path=[clean_heading(item) for item in current_path if clean_heading(item)],
                body=body,
                source_path=source.path,
                document_name=source.document_name,
            ),
        )

    for line in source.text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush()
            level = len(match.group(1))
            title = clean_heading(match.group(2))
            heading_stack = heading_stack[: max(level - 1, 0)]
            heading_stack.append(title)
            current_title = title
            current_path = heading_stack[:]
            body_lines = []
            continue
        body_lines.append(line)

    flush()
    return sections


def generate_samples_from_markdown(
    sources: list[MarkdownSource],
    *,
    vendor: str,
    model: str,
    max_samples: int,
    min_section_chars: int = 80,
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    sequence = 1
    for source in sources:
        for section in iter_markdown_sections(source, min_section_chars=min_section_chars):
            keywords = extract_keywords(section.title, section.body)
            if not keywords:
                keywords = [short_topic(section.title)]
            scenario_type = classify_scenario(section.title, section.body)
            topic = short_topic(section.title)
            sample = {
                "id": f"{sample_id_prefix(vendor, model)}-{sequence:03d}",
                "vendor": vendor.strip(),
                "model": model.strip(),
                "scenario_type": scenario_type,
                "topic": topic,
                "difficulty": infer_difficulty(section.body),
                "question": build_question(vendor, model, topic, scenario_type),
                "alternative_queries": build_alternative_queries(model, topic, scenario_type, keywords),
                "expected_documents": [section.document_name],
                "expected_sections": [section.heading_path[-1] if section.heading_path else section.title],
                "expected_keywords": keywords[:8],
                "evaluation_focus": build_evaluation_focus(section, keywords),
                "metadata": {
                    "generated_by": "kb_eval.dataset_generator.rule_based_v1",
                    "source_markdown": str(section.source_path),
                    "heading_path": section.heading_path,
                },
            }
            samples.append(sample)
            sequence += 1
            if len(samples) >= max_samples:
                return samples
    return samples


def write_jsonl_dataset(path: Path, samples: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for sample in samples:
            fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
    load_samples(path)


def convert_pdf_with_mineru(
    pdf_path: Path,
    output_root: Path,
    *,
    mineru_command: str = "",
    timeout_seconds: int = 900,
) -> MinerUConversionResult:
    output_root.mkdir(parents=True, exist_ok=True)
    output_dir = output_root

    errors: list[str] = []
    for command in mineru_commands(pdf_path, output_dir, mineru_command):
        try:
            completed = subprocess.run(
                command,
                cwd=str(pdf_path.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            errors.append(f"{command[0]}: command not found")
            continue
        except subprocess.TimeoutExpired:
            errors.append(f"{' '.join(command)}: timeout after {timeout_seconds}s")
            continue

        markdown_path = find_markdown_output(output_dir, pdf_path.stem)
        if completed.returncode == 0 and markdown_path:
            return MinerUConversionResult(
                markdown_path=markdown_path,
                command=command,
                stdout=completed.stdout[-4000:],
                stderr=completed.stderr[-4000:],
            )

        errors.append(
            "\n".join(
                [
                    f"{' '.join(command)}: exit {completed.returncode}",
                    completed.stdout[-1000:],
                    completed.stderr[-1000:],
                ],
            ).strip(),
        )

    joined = "\n\n".join(item for item in errors if item) or "MinerU command failed"
    raise EvalError(f"PDF 转 Markdown 失败：{pdf_path.name}\n{joined}")


def mineru_commands(pdf_path: Path, output_dir: Path, mineru_command: str) -> list[list[str]]:
    if mineru_command.strip():
        command = split_command(mineru_command)
        rendered = [
            part.replace("{input}", str(pdf_path)).replace("{output}", str(output_dir))
            for part in command
        ]
        if any("{input}" in part or "{output}" in part for part in command):
            return [rendered]
        return [rendered + ["-p", str(pdf_path), "-o", str(output_dir), "-m", "auto"]]

    return [
        ["mineru", "-p", str(pdf_path), "-o", str(output_dir), "-m", "auto"],
        ["magic-pdf", "-p", str(pdf_path), "-o", str(output_dir), "-m", "auto"],
        ["mineru", str(pdf_path), "-o", str(output_dir)],
    ]


def split_command(value: str) -> list[str]:
    return shlex.split(value, posix=False)


def find_markdown_output(output_dir: Path, pdf_stem: str) -> Path | None:
    markdown_files = sorted(output_dir.rglob("*.md"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not markdown_files:
        return None
    preferred = [item for item in markdown_files if item.stem.lower() == pdf_stem.lower()]
    if preferred:
        return preferred[0]
    return max(markdown_files, key=lambda item: item.stat().st_size)


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def clean_heading(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"^[\d.、\s]+", "", value)
    return value.strip(" \t#")


def short_topic(value: str, *, max_chars: int = 36) -> str:
    value = clean_heading(value)
    value = re.sub(r"\s+", " ", value)
    return value[:max_chars].rstrip() or "知识库章节"


def classify_scenario(title: str, body: str) -> str:
    haystack = f"{title}\n{body[:1200]}".lower()
    for scenario, needles in SCENARIO_RULES:
        if any(needle.lower() in haystack for needle in needles):
            return scenario
    return "配置操作"


def infer_difficulty(body: str) -> str:
    command_count = len(extract_command_candidates(body))
    if command_count >= 8 or len(body) > 2500:
        return "高级"
    if command_count >= 3 or len(body) > 1000:
        return "中等"
    return "基础"


def build_question(vendor: str, model: str, topic: str, scenario_type: str) -> str:
    subject = f"{vendor.strip()} {model.strip()}".strip()
    if scenario_type == "故障恢复":
        return f"{subject} 遇到“{topic}”相关问题时应该如何处理？"
    if scenario_type == "查询诊断":
        return f"{subject} 如何查看或确认“{topic}”？"
    if scenario_type == "安全与准入":
        return f"{subject} 如何配置或检查“{topic}”？"
    if scenario_type == "协议特性":
        return f"{subject} 中“{topic}”的关键配置和注意事项是什么？"
    return f"{subject} 如何配置“{topic}”？"


def build_alternative_queries(model: str, topic: str, scenario_type: str, keywords: list[str]) -> list[str]:
    queries = [
        f"{model} {topic} 怎么做？",
        f"怎么在 {model} 上处理 {topic}？",
    ]
    if scenario_type == "查询诊断":
        queries.append(f"{model} 用什么命令查看 {topic}？")
    elif scenario_type == "故障恢复":
        queries.append(f"{model} {topic} 异常怎么恢复？")
    else:
        queries.append(f"{model} {topic} 配置步骤是什么？")

    command = next((item for item in keywords if looks_like_command(item)), "")
    if command:
        queries.append(f"{model} {command} 命令适用于什么场景？")
    return dedupe(queries)[:4]


def build_evaluation_focus(section: MarkdownSection, keywords: list[str]) -> str:
    keyword_text = "、".join(keywords[:5])
    section_name = section.heading_path[-1] if section.heading_path else section.title
    return (
        f"应命中 `{section.document_name}` 中“{section_name}”相关章节，"
        f"重点关注 {keyword_text or section_name}，避免命中同名但缺少具体操作、命令或诊断依据的内容。"
    )


def extract_keywords(title: str, body: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(extract_command_candidates(body))
    candidates.extend(extract_code_spans(body))
    candidates.extend(extract_acronyms(f"{title}\n{body[:1200]}"))
    candidates.extend(extract_chinese_terms(title))
    return [item for item in dedupe(candidates) if item][:8]


def extract_command_candidates(text: str) -> list[str]:
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().strip("`")
        lower = stripped.lower()
        if any(lower.startswith(prefix) for prefix in COMMAND_PREFIXES):
            commands.append(re.sub(r"\s+", " ", stripped)[:96])
    inline_pattern = r"\b(" + "|".join(re.escape(item) for item in COMMAND_PREFIXES) + r")\b[ a-zA-Z0-9_./:-]{0,72}"
    for match in re.finditer(inline_pattern, text, flags=re.I):
        commands.append(re.sub(r"\s+", " ", match.group(0)).strip(" ,.;，。；：")[:96])
    return commands


def extract_code_spans(text: str) -> list[str]:
    spans = []
    for match in re.finditer(r"`([^`\n]{2,96})`", text):
        value = match.group(1).strip()
        if looks_like_command(value):
            spans.append(value)
    return spans


def extract_acronyms(text: str) -> list[str]:
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9/-]{1,31}\b", text)
    ignored = {"pdf", "md", "http", "https", "png", "jpg", "fig", "table"}
    return [token for token in tokens if token.lower() not in ignored]


def extract_chinese_terms(title: str) -> list[str]:
    parts = re.split(r"[，。；：、/\\|（）()《》\[\]\s]+", title)
    terms = []
    for part in parts:
        value = part.strip()
        if 2 <= len(value) <= 18 and value not in CHINESE_STOPWORDS:
            terms.append(value)
    return terms


def looks_like_command(value: str) -> bool:
    lower = value.strip().lower()
    return any(lower.startswith(prefix) for prefix in COMMAND_PREFIXES)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip(" \t\r\n,.;，。；：")
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def sample_id_prefix(vendor: str, model: str) -> str:
    model_part = re.sub(r"[^A-Za-z0-9]+", "", model).upper() or "KB"
    vendor_part = VENDOR_ID_PREFIXES.get(vendor.strip()) or "".join(re.findall(r"[A-Za-z0-9]+", vendor.upper())) or "AUTO"
    return f"{vendor_part}-{model_part}-EVAL"
