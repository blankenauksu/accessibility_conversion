"""Pre-upload checks for Canvas Ally accessibility on converted homework .tex files."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Sequence, Union

PathLike = Union[str, Path]
Severity = Literal["error", "warning", "info"]

_BLOCKING_WARNINGS = frozenset({
    "missing_tagging",
    "missing_pdf_title",
    "missing_math_alt",
    "includegraphics",
    "lonely_item",
    "unclosed_list",
    "manual_title_block",
    "pagebreak_in_key",
    "missing_key_heading",
    "empty_item",
})


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    location: Optional[str] = None


@dataclass
class ValidationReport:
    tex_path: Path
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def canvas_ready(self) -> bool:
        blocking = {f.code for f in self.findings if f.severity == "error"}
        blocking |= {
            f.code for f in self.findings
            if f.severity == "warning" and f.code in _BLOCKING_WARNINGS
        }
        return not blocking


def _add(report: ValidationReport, severity: Severity, code: str, message: str, location: Optional[str] = None) -> None:
    report.findings.append(Finding(severity, code, message, location))


def _list_stack(body: str) -> list[str]:
    stack: list[str] = []
    for m in re.finditer(
        r"\\begin\{(itemize|enumerate|description)\}|\\end\{(itemize|enumerate|description)\}",
        body,
    ):
        if m.group(0).startswith(r"\begin"):
            stack.append(m.group(1))
        elif stack and stack[-1] == m.group(2):
            stack.pop()
    return stack


def validate_accessible_hw(tex_path: PathLike) -> ValidationReport:
    path = Path(tex_path).expanduser().resolve()
    report = ValidationReport(tex_path=path)

    if not path.is_file():
        _add(report, "error", "missing_file", f"File not found: {path}")
        return report

    tex = path.read_text(encoding="utf-8", errors="replace")

    if r"\documentclass" not in tex or "article" not in tex:
        _add(report, "error", "not_article", "Expected \\documentclass{article}.")
    if "ltx-talk" in tex:
        _add(report, "error", "slide_class", "Homework file should not use ltx-talk.")

    if r"\DocumentMetadata" not in tex:
        _add(report, "error", "missing_document_metadata", "Missing \\DocumentMetadata block.")
    else:
        if not re.search(r"tagging\s*=\s*on", tex):
            _add(report, "error", "missing_tagging", "DocumentMetadata must set tagging=on.")
        if not re.search(r"pdfstandard\s*=\s*ua-2", tex):
            _add(report, "warning", "pdfstandard", "DocumentMetadata should target pdfstandard=ua-2.")
        if not re.search(r"math/setup\s*=\s*\{[^}]*mathml-SE", tex):
            _add(report, "warning", "mathml", "Expected math/setup={mathml-SE} for accessible math.")

    if not re.search(r"pdftitle\s*=\s*\{", tex):
        _add(report, "warning", "missing_pdf_title", "No pdftitle in \\hypersetup.")
    if not re.search(r"math/alt/use", tex):
        _add(
            report,
            "warning",
            "missing_math_alt",
            "Missing math/alt/use — Canvas Ally may flag equations as untagged images.",
        )
    if r"\usepackage{hyperref}" not in tex:
        _add(report, "warning", "missing_hyperref", "Missing \\usepackage{hyperref} for PDF metadata.")

    if re.search(r"\\begin\{center\}.*\\LARGE", tex, flags=re.DOTALL | re.IGNORECASE):
        body_m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, flags=re.DOTALL)
        body = body_m.group(1) if body_m else tex
        instructions = re.search(r"\\noindent\s+\\textit\{Instructions\.\}", body)
        if instructions:
            tail = body[instructions.start() :]
            if re.search(r"\\begin\{center\}.*\\LARGE", tail, flags=re.DOTALL | re.IGNORECASE):
                _add(
                    report,
                    "warning",
                    "manual_title_block",
                    "Extra manual \\begin{center}{\\LARGE ...} title block found in body.",
                )
        elif body.count(r"\begin{center}") > 1:
            _add(
                report,
                "warning",
                "manual_title_block",
                "Multiple \\begin{center} title blocks found — check for duplicate headers.",
            )

    for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex):
        line = tex[: m.start()].count("\n") + 1
        _add(
            report,
            "error",
            "includegraphics",
            f"\\includegraphics found ({m.group(1)}) — use text placeholders instead.",
            f"line {line}",
        )

    plot_count = len(re.findall(r"Figure \(plot omitted\)|\[Plot omitted\]", tex))
    if plot_count:
        _add(report, "info", "plot_placeholder", f"{plot_count} plot(s) replaced with text placeholders.")

    body_m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, flags=re.DOTALL)
    body = body_m.group(1) if body_m else tex

    if re.match(r"\\item\b", body.lstrip()) and not re.match(
        r"\\begin\{(itemize|enumerate|description)\}", body.lstrip()
    ):
        _add(report, "error", "lonely_item", "Lonely \\item outside a list environment.")

    open_lists = _list_stack(body)
    if open_lists:
        _add(report, "error", "unclosed_list", f"Unclosed list(s): {', '.join(open_lists)}")

    is_key_file = "_key_hw" in path.name
    if is_key_file:
        if re.search(r"\\pagebreak", tex, re.IGNORECASE):
            _add(report, "warning", "pagebreak_in_key", "Remove \\pagebreak from key PDF — hurts reading order.")
        if r"\section*{Answer Key}" not in tex:
            _add(report, "warning", "missing_key_heading", "Key PDF should include \\section*{Answer Key}.")
        if re.search(r"\\begin\{(equation\*?)\}\s*\\begin\{tabular\}", tex, re.DOTALL):
            _add(report, "error", "tabular_in_equation", "Move tabular out of equation* into a table environment.")
        for m in re.finditer(r"\\item\s*\n(?=\s*\\begin\{(itemize|enumerate)\})", body):
            line = tex[: m.start()].count("\n") + 1
            _add(
                report,
                "warning",
                "empty_item",
                "Empty \\item before a nested list — Ally may flag this.",
                f"line {line}",
            )
        if not re.search(r"Key", tex[: tex.find(r"\begin{document}")]):
            _add(report, "info", "key_pdftitle", "Consider pdftitle ending with 'Key' for the answer-key PDF.")

    if report.ok and report.canvas_ready:
        stem = path.stem.removesuffix("_hw") if path.stem.endswith("_hw") else path.stem
        if stem.endswith("_key"):
            stem = stem.removesuffix("_key")
        workdir = path.parent
        _add(
            report,
            "info",
            "compile_reminder",
            f"Compile with: bash accessible_homework/compile_pdf.sh {stem} {workdir}",
        )
        _add(
            report,
            "info",
            "pdf_output",
            "PDFs are written to canvas_pdfs/",
        )

    return report


def print_validation_report(report: ValidationReport) -> bool:
    print(f"Validate: {report.tex_path}")
    print(f"Status:   {'READY' if report.canvas_ready else 'NEEDS FIXES'}")
    print()

    for label, items in (
        ("Errors", report.errors),
        ("Warnings", report.warnings),
        ("Info", [f for f in report.findings if f.severity == "info"]),
    ):
        if not items:
            continue
        print(f"{label} ({len(items)}):")
        for item in items:
            where = f" [{item.location}]" if item.location else ""
            print(f"  [{item.code}]{where} {item.message}")
        print()

    print(
        f"Summary: {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s), "
        f"{len([f for f in report.findings if f.severity == 'info'])} info"
    )
    return report.canvas_ready


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check homework .tex for Canvas Ally issues.")
    parser.add_argument("tex_file", help="Path to *_hw.tex")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on warnings too")
    args = parser.parse_args(argv)

    report = validate_accessible_hw(args.tex_file)
    ready = print_validation_report(report)
    if not report.ok:
        return 1
    if args.strict and not ready:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
