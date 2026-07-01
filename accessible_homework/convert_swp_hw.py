"""Convert Scientific Word homework .tex to accessible tagged article (local LuaLaTeX)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

PathLike = Union[str, Path]

_PKG_DIR = Path(__file__).resolve().parent
_DEFAULT_HOMEWORK = _PKG_DIR.parent / "905_homework"


def _normalize_metadata(text: str) -> str:
    return text.replace("\u2014", "--").replace("\u2013", "-")


def _escape_latex(text: str) -> str:
    for old, new in {
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "'": r"{'}",
    }.items():
        text = text.replace(old, new)
    return text


def _split_preamble_body(raw: str) -> tuple[str, str]:
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", raw, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        raise ValueError("No \\begin{document} ... \\end{document} found.")
    preamble = raw[: m.start()]
    body = m.group(1).strip()
    return preamble, body


def _extract_font_size(preamble: str) -> str:
    m = re.search(r"\\documentclass(?:\[[^\]]*\])?\{article\}", preamble)
    if not m:
        return "12pt"
    block = m.group(0)
    m_pt = re.search(r"(\d+)pt", block)
    return f"{m_pt.group(1)}pt" if m_pt else "12pt"


def _extract_theorem_block(preamble: str) -> str:
    lines: list[str] = []
    for line in preamble.splitlines():
        stripped = line.strip()
        if stripped.startswith(r"\newtheorem") or stripped.startswith(r"\newenvironment{proof}"):
            lines.append(line)
    return "\n".join(lines).strip()


def _extract_hw_header(body: str) -> tuple[str, str, str, str]:
    """Return (title, author, date, body_without_header)."""
    title = ""
    author = ""
    date = ""
    remainder = body

    title_m = re.search(
        r"\\begin\{center\}\s*(?:\{\\LARGE\s+([^}]+)\}|\\LARGE\s+([^}]+))\s*\\end\{center\}",
        remainder,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if title_m:
        title = (title_m.group(1) or title_m.group(2) or "").strip()
        remainder = remainder[: title_m.start()] + remainder[title_m.end() :]

    author_m = re.search(
        r"\\noindent\s+([^\n]+?)\\hspace\*\{\\stretch\{[^}]+\}\}",
        remainder,
    )
    if author_m:
        author = author_m.group(1).strip()
        remainder = remainder[: author_m.start()] + remainder[author_m.end() :]
    else:
        author_m = re.search(r"\\noindent\s+([^\n\\]+)", remainder)
        if author_m:
            author = author_m.group(1).strip()
            remainder = remainder[: author_m.start()] + remainder[author_m.end() :]

    date_m = re.search(
        r"\\noindent\s+((?:Economics|Econ\.?)\s+[^\\\n]+)",
        remainder,
        flags=re.IGNORECASE,
    )
    if date_m:
        date = date_m.group(1).strip()
        remainder = remainder[: date_m.start()] + remainder[date_m.end() :]

    remainder = re.sub(r"^\\medskip\s*", "", remainder.lstrip(), flags=re.MULTILINE)
    return title, author, date, remainder.strip()


def _consume_braced_arg(text: str, start: int) -> tuple[str, int]:
    if start >= len(text) or text[start] != "{":
        raise ValueError("Expected '{' at start of braced argument")

    depth = 0
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1

    raise ValueError("Unbalanced braces while parsing LaTeX argument")


def _plot_placeholder(caption: str = "") -> str:
    if caption.strip():
        return f"\n\\par\\noindent\\textbf{{Figure (plot omitted).}} {caption}\\par\n"
    return "\n\\par\\noindent\\textit{[Plot omitted]}\\par\n"


def _replace_includegraphics(body: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        return _plot_placeholder(Path(match.group(1)).stem.replace("_", " "))

    return re.sub(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", _repl, body)


def _replace_footnotes(body: str) -> str:
    marker = r"\footnote"
    out: list[str] = []
    i = 0

    while True:
        idx = body.find(marker, i)
        if idx == -1:
            out.append(body[i:])
            break

        out.append(body[i:idx])
        pos = idx + len(marker)
        if pos < len(body) and body[pos] == "%":
            pos += 1
        while pos < len(body) and body[pos].isspace():
            pos += 1

        if pos >= len(body) or body[pos] != "{":
            out.append(marker)
            i = idx + len(marker)
            continue

        try:
            note, pos = _consume_braced_arg(body, pos)
            out.append(f"\n\\par\\vspace{{0.4em}}{{\\footnotesize\\textit{{Note:}} {note.strip()}}}\n")
            i = pos
        except ValueError:
            out.append(marker)
            i = idx + len(marker)

    return "".join(out)


def _list_env_stack(body: str) -> list[str]:
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


def _close_open_lists(body: str) -> str:
    stack = _list_env_stack(body)
    if stack:
        body = body.rstrip() + "\n" + "\n".join(f"\\end{{{env}}}" for env in reversed(stack))
    return body


def _sanitize_hw_body(body: str) -> str:
    body = re.sub(r"\s+\\\s*$", "", body, flags=re.MULTILINE)
    body = _replace_includegraphics(body)
    body = _replace_footnotes(body)
    body = _close_open_lists(body.strip())
    body = re.sub(
        r"^\s*\\(?:newline|bigskip|smallskip)\s*(?:\n|$)",
        "",
        body,
        flags=re.MULTILINE,
    )
    return body.strip()


_KEY_NOTE = re.compile(
    r"\\noindent\s+\\textbf\{Note:\s*\}I have included the key here\..*?turn in the assignment\.\s*",
    re.DOTALL | re.IGNORECASE,
)


def _strip_key_note(body: str) -> str:
    return _KEY_NOTE.sub("", body).strip()


def _tail_starts_key_section(tail: str) -> tuple[bool, list[str]]:
    """True when content after \\pagebreak begins an answer-key section."""
    rest = tail.lstrip()
    closes: list[str] = []
    while rest:
        if rest.startswith(r"\end{enumerate}"):
            closes.append(r"\end{enumerate}")
            rest = rest[len(r"\end{enumerate}") :].lstrip()
            continue
        if re.match(r"^\\bigskip\b", rest):
            rest = re.sub(r"^\\bigskip\s*", "", rest).lstrip()
            continue
        if rest.startswith(r"\begin{enumerate}"):
            return True, closes
        return False, []
    return False, []


def _split_questions_and_key(body: str) -> tuple[str, bool]:
    """Return (body_for_student, has_separate_key_section)."""
    for m in re.finditer(r"\\pagebreak(?:\s*\\pagebreak)?(?:\s*\\bigskip)?", body, re.IGNORECASE):
        found, closes = _tail_starts_key_section(body[m.end() :])
        if not found:
            continue
        questions = body[: m.start()].rstrip()
        questions = re.sub(r"\\pagebreak(?:\s*\\bigskip)?\s*$", "", questions, flags=re.IGNORECASE).rstrip()
        if closes:
            questions = questions + "\n" + "\n".join(closes)
        questions = _close_open_lists(questions)
        questions = _strip_key_note(questions)
        return questions, True
    return _strip_key_note(body), False


def _insert_answer_key_heading(body: str) -> str:
    """Insert \\section*{Answer Key} before the answer-key enumerate block."""
    for m in re.finditer(r"\\pagebreak(?:\s*\\pagebreak)?(?:\s*\\bigskip)?", body, re.IGNORECASE):
        found, _closes = _tail_starts_key_section(body[m.end() :])
        if not found:
            continue
        tail = body[m.end() :]
        key_m = re.search(r"\\begin\{enumerate\}", tail.lstrip())
        if not key_m:
            continue
        leading = len(tail) - len(tail.lstrip())
        insert_at = m.end() + leading + key_m.start()
        if r"\section*{Answer Key}" in body[:insert_at]:
            return body
        return body[:insert_at] + r"\section*{Answer Key}" + "\n\n" + body[insert_at:]
    return body


def _fix_placeholder_items(body: str) -> str:
    body = re.sub(r"\\item\s+\.\.+\s*", r"\\item ", body)
    body = re.sub(
        r"\\item\s*(?:%[^\n]*)?\n(\s*\\begin\{(itemize|enumerate)\})",
        r"\\item \\textit{See below.}\n\1",
        body,
    )
    body = re.sub(
        r"\\item\s*\n(?=\s*\\item\s+[^\s\\])",
        r"\\item \\textit{[Continued.]}\n",
        body,
    )
    return body


def _fix_tabular_in_equation(body: str) -> str:
    pattern = re.compile(
        r"\\begin\{(equation\*?)\}\s*\\begin\{tabular\}\{([^}]+)\}(.*?)\\end\{tabular\}\s*%?\s*\\end\{\1\}\s*%?",
        re.DOTALL,
    )
    counter = [0]

    def repl(match: re.Match[str]) -> str:
        counter[0] += 1
        cols = match.group(2)
        tab_content = match.group(3)
        return (
            f"\\begin{{table}}[htbp]\n\\centering\n"
            f"\\begin{{tabular}}{{{cols}}}{tab_content}\\end{{tabular}}\n"
            f"\\caption{{Table {counter[0]}: Comparison of values.}}\n"
            f"\\end{{table}}"
        )

    return pattern.sub(repl, body)


def _prepare_key_body(body: str) -> str:
    body = _strip_key_note(body)
    body = _insert_answer_key_heading(body)
    body = re.sub(
        r"\\pagebreak(?:\s*\\pagebreak)?(?:\s*\\bigskip)?\s*",
        "",
        body,
        flags=re.IGNORECASE,
    )
    body = _fix_placeholder_items(body)
    body = _fix_tabular_in_equation(body)
    return body.strip()


def _parse_assignment_number(title: str) -> Optional[int]:
    m = re.search(r"Assignment\s+(\d+)", title, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_assignment_from_filename(stem: str) -> Optional[int]:
    m = re.match(r"hw(\d+)", stem, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _resolve_assignment_number(
    assignment_number: Optional[int],
    *,
    parsed_title: str,
    filename_stem: str,
) -> int:
    if assignment_number is not None:
        return assignment_number
    from_title = _parse_assignment_number(parsed_title)
    if from_title is not None:
        return from_title
    from_file = _parse_assignment_from_filename(filename_stem)
    if from_file is not None:
        return from_file
    return 1


def _assignment_title(assignment_number: int) -> str:
    return f"Assignment {assignment_number}"


def _parse_year_from_filename(stem: str) -> Optional[int]:
    m = re.search(r"fall(\d{2})", stem, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _parse_year_from_date(date: str) -> Optional[int]:
    m = re.search(r"20(\d{2})", date)
    if m:
        return int(m.group(1))
    m = re.search(r"Fall[,\s]+(\d{2})\b", date, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _resolve_year(
    year: Optional[int],
    *,
    filename_stem: str,
    date: str,
) -> int:
    if year is not None:
        return year
    from_file = _parse_year_from_filename(filename_stem)
    if from_file is not None:
        return from_file
    from_date = _parse_year_from_date(date)
    if from_date is not None:
        return from_date
    return 26


def _term_for_year(year: int) -> str:
    return f"Fall, 20{year:02d}"


def output_stem(
    source_stem: str,
    year: int,
    assignment_number: Optional[int] = None,
) -> str:
    """Build output file stem (e.g. hw1fall25 → hw2fall26)."""
    stem = source_stem
    if re.search(r"fall\d{2}", stem, re.IGNORECASE):
        stem = re.sub(r"fall\d{2}", f"fall{year:02d}", stem, count=1, flags=re.IGNORECASE)
    else:
        stem = f"{stem}fall{year:02d}"
    if assignment_number is not None and re.match(r"hw\d+", stem, re.IGNORECASE):
        stem = re.sub(r"^hw\d+", f"hw{assignment_number}", stem, count=1, flags=re.IGNORECASE)
    return stem


_DEFAULT_TERM = "Fall, 2026"


def _make_preamble(
    *,
    title: str,
    author: str,
    font_size: str,
    theorem_block: str,
    term: str = _DEFAULT_TERM,
    pdf_title: Optional[str] = None,
) -> str:
    title = _escape_latex(_normalize_metadata(title))
    author = _escape_latex(_normalize_metadata(author))
    term = _escape_latex(_normalize_metadata(term))
    pdf_title = _escape_latex(_normalize_metadata(pdf_title or title.replace(r"\—", "—")))
    pdf_author = author or "Kansas State University"

    theorem_section = f"\n{theorem_block}\n" if theorem_block else ""

    return rf"""% !TEX TS-program = lualatex

\DocumentMetadata{{
  lang = en,
  pdfversion = 2.0,
  pdfstandard = ua-2,
  tagging = on,
  tagging-setup = {{
    math/setup = {{mathml-SE}},
    table/header-rows = 1
  }}
}}

\documentclass[{font_size}]{{article}}

\usepackage{{amsmath}}
\usepackage{{geometry}}
\geometry{{
  letterpaper,
  textwidth=6.5in,
  textheight=9in,
  top=0.85in,
  bottom=1in,
  left=0.75in,
  right=0.75in,
}}
\usepackage{{hyperref}}
{theorem_section}
\tagpdfsetup{{
  math/alt/use,
  role/new-tag=section/H1,
}}

\hypersetup{{
  pdftitle={{{pdf_title}}},
  pdfauthor={{{pdf_author}}},
  pdfsubject={{{term}}},
  hidelinks,
}}

\begin{{document}}
"""


def _make_document_header(*, title: str, author: str, term: str = _DEFAULT_TERM) -> str:
    title = _escape_latex(_normalize_metadata(title))
    author = _escape_latex(_normalize_metadata(author))
    term = _escape_latex(_normalize_metadata(term))

    lines = [
        r"\begin{center}",
        rf"{{\LARGE {title}}}",
        r"\end{center}",
        "",
    ]
    if author:
        lines.append(rf"\noindent {author}")
        lines.append("")
    lines.append(rf"\noindent Economics 905, {term}")
    lines.append("")
    lines.append(r"\medskip")
    lines.append("")
    return "\n".join(lines)


def _build_document(
    body: str,
    *,
    title: str,
    author: str,
    font_size: str,
    theorem_block: str,
    term: str = _DEFAULT_TERM,
    pdf_title: Optional[str] = None,
    sanitize: bool = True,
) -> str:
    if sanitize:
        body = _sanitize_hw_body(body)
    chunks = [
        _make_preamble(
            title=title,
            author=author,
            font_size=font_size,
            theorem_block=theorem_block,
            term=term,
            pdf_title=pdf_title,
        ),
        _make_document_header(title=title, author=author, term=term),
        body,
        "\n\\end{document}\n",
    ]
    return "".join(chunks)


def _resolve_input_path(filename: PathLike, search_dirs: Optional[Sequence[PathLike]] = None) -> Path:
    path = Path(filename).expanduser()
    candidates = [path]
    if search_dirs:
        candidates.extend(Path(d) / path.name for d in search_dirs)
    here = Path.cwd()
    candidates.extend([
        here / path,
        here / path.name,
        _DEFAULT_HOMEWORK / path.name,
        _PKG_DIR.parent / "905_homework" / path.name,
    ])

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate

    tried = "\n  ".join(str(c) for c in candidates)
    raise FileNotFoundError(f"Could not find '{filename}'. Tried:\n  {tried}")


def _output_path(
    input_path: Path,
    output_filename: Optional[PathLike],
    year: int,
    assignment_number: int,
    *,
    include_key: bool = False,
) -> Path:
    if output_filename is not None:
        return Path(output_filename).expanduser()
    stem = output_stem(input_path.stem, year, assignment_number)
    if include_key:
        stem = f"{stem}_key"
    return input_path.with_name(f"{stem}_hw.tex")


def peek_hw_source(
    filename: PathLike,
    *,
    search_dirs: Optional[Sequence[PathLike]] = None,
) -> dict[str, Optional[int | str]]:
    input_path = _resolve_input_path(filename, search_dirs=search_dirs)
    raw = input_path.read_text(encoding="utf-8", errors="replace")
    _preamble, body = _split_preamble_body(raw)
    parsed_title, parsed_author, parsed_date, content = _extract_hw_header(body)
    detected_year = _resolve_year(None, filename_stem=input_path.stem, date=parsed_date)
    detected_assignment = _resolve_assignment_number(
        None,
        parsed_title=parsed_title,
        filename_stem=input_path.stem,
    )
    stem = output_stem(input_path.stem, detected_year, detected_assignment)
    _, has_key = _split_questions_and_key(content)
    return {
        "source": input_path.name,
        "source_stem": input_path.stem,
        "title": parsed_title,
        "author": parsed_author,
        "assignment_number": detected_assignment,
        "year": detected_year,
        "output_stem": stem,
        "output_stem_key": f"{stem}_key",
        "has_key": has_key,
        "term": _term_for_year(detected_year),
    }


def convert_swp_hw(
    filename: PathLike,
    *,
    output_filename: Optional[PathLike] = None,
    assignment_number: Optional[int] = None,
    year: Optional[int] = None,
    author: str = "",
    search_dirs: Optional[Sequence[PathLike]] = None,
    validate: bool = True,
) -> dict[str, Path]:
    input_path = _resolve_input_path(filename, search_dirs=search_dirs)
    raw = input_path.read_text(encoding="utf-8", errors="replace")
    preamble, body = _split_preamble_body(raw)

    parsed_title, parsed_author, parsed_date, body = _extract_hw_header(body)
    detected_assignment = _resolve_assignment_number(
        None,
        parsed_title=parsed_title,
        filename_stem=input_path.stem,
    )
    detected_year = _resolve_year(None, filename_stem=input_path.stem, date=parsed_date)
    final_assignment = _resolve_assignment_number(
        assignment_number,
        parsed_title=parsed_title,
        filename_stem=input_path.stem,
    )
    final_year = _resolve_year(year, filename_stem=input_path.stem, date=parsed_date)
    final_title = _assignment_title(final_assignment)
    final_author = author or parsed_author or ""
    final_term = _term_for_year(final_year)
    final_stem = output_stem(input_path.stem, final_year, final_assignment)
    key_stem = f"{final_stem}_key"

    if not body.strip():
        raise ValueError(f"No homework content found in {input_path.name}")

    font_size = _extract_font_size(preamble)
    theorem_block = _extract_theorem_block(preamble)
    student_body, has_key = _split_questions_and_key(body)
    key_body = _prepare_key_body(body) if has_key else None

    build_kwargs = {
        "title": final_title,
        "author": final_author,
        "font_size": font_size,
        "theorem_block": theorem_block,
        "term": final_term,
    }
    key_pdf_title = f"{final_title} — Key"

    student_path = _output_path(
        input_path, output_filename, final_year, final_assignment, include_key=False
    )
    student_path.write_text(
        _build_document(student_body, **build_kwargs),
        encoding="utf-8",
    )

    result: dict[str, Path] = {"student": student_path}

    key_path = _output_path(
        input_path, None, final_year, final_assignment, include_key=True
    )
    if has_key and key_body is not None:
        key_path.write_text(
            _build_document(
                key_body,
                pdf_title=key_pdf_title,
                sanitize=True,
                **build_kwargs,
            ),
            encoding="utf-8",
        )
        result["key"] = key_path
    else:
        for stale in (key_path, key_path.with_name(f"{key_path.stem}.pdf")):
            if stale.exists():
                stale.unlink()
                print(f"Removed:    {stale.name}  (no key in source)")

    print(f"Read:       {input_path}")
    print(f"Wrote:      {student_path}" + ("  (questions only)" if has_key else ""))
    if has_key:
        print(f"Wrote:      {result['key']}  (with key)")
    else:
        print("Key:        none detected in source")
    print(f"Assignment: {final_title}")
    if final_assignment != detected_assignment:
        print(f"Source had: Assignment {detected_assignment}")
    print(f"Year:       20{final_year:02d}")
    if final_year != detected_year:
        print(f"Source had: 20{detected_year:02d}")
    print(f"Term:       {final_term}")
    print(f"Compile:    bash accessible_homework/compile_pdf.sh {final_stem} {input_path.parent}")
    print(f"PDF folder: canvas_pdfs/")
    if has_key:
        print(f"Compile:    bash accessible_homework/compile_pdf.sh {key_stem} {input_path.parent}")

    if validate:
        from validate_accessible_hw import print_validation_report, validate_accessible_hw

        print()
        print_validation_report(validate_accessible_hw(student_path))
        if has_key:
            print()
            print_validation_report(validate_accessible_hw(result["key"]))

    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Scientific Word homework to accessible tagged article."
    )
    parser.add_argument("filename", nargs="?", help="Input .tex file")
    parser.add_argument("--assignment-number", type=int, default=None, metavar="N")
    parser.add_argument("--year", type=int, default=None, metavar="YY", help="Two-digit year (e.g. 26 for 2026)")
    parser.add_argument("--author", default="")
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--validate-only", metavar="TEX", help="Validate an existing .tex file")
    args = parser.parse_args()

    if args.validate_only:
        from validate_accessible_hw import print_validation_report, validate_accessible_hw

        report = validate_accessible_hw(args.validate_only)
        print_validation_report(report)
        return 0 if report.canvas_ready else 1

    if not args.filename:
        parser.error("filename is required unless --validate-only is used")

    convert_swp_hw(
        args.filename,
        output_filename=args.output,
        assignment_number=args.assignment_number,
        year=args.year,
        author=args.author,
        search_dirs=[str(_DEFAULT_HOMEWORK), "."],
        validate=not args.no_validate,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
