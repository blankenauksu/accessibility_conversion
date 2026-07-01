"""Convert Scientific Word slide .tex to accessible ltx-talk slides (local LuaLaTeX)."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

PathLike = Union[str, Path]

_PKG_DIR = Path(__file__).resolve().parent
_DEFAULT_MATERIALS = _PKG_DIR.parent / "905_materials"


def _normalize_metadata(text: str) -> str:
    return text.replace("\u2014", "--").replace("\u2013", "-")


def _make_preamble(
    *,
    title: str,
    author: str,
    institute: str,
    date: str,
    aspectratio: str = "169",
) -> str:
    title = _normalize_metadata(title)
    author = _normalize_metadata(author)
    institute = _normalize_metadata(institute)
    date = _normalize_metadata(date)
    pdf_author = author or institute

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

\documentclass[aspectratio={aspectratio}]{{ltx-talk}}

\usepackage{{amsmath}}

\tagpdfsetup{{
  role/new-tag=frametitle/H1,
  math/alt/use
}}

\title{{{title}}}
\author{{{author}}}
\institute{{{institute}}}
\date{{{date}}}

\hypersetup{{
  pdftitle={{{title}}},
  pdfauthor={{{pdf_author}}},
}}

\begin{{document}}

\begin{{frame}}
  \titlepage
\end{{frame}}
"""


def _strip_swp_preamble(text: str) -> str:
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, flags=re.DOTALL | re.IGNORECASE)
    if not m:
        raise ValueError("No \\begin{document} ... \\end{document} found.")

    body = m.group(1)
    body = re.sub(r"\\Set(?:Title|Course|Author|Date)\{[^}]*\}\s*", "", body)
    body = re.sub(r"\\TitlePage\{\}\s*", "", body)
    body = re.sub(r"\\setcounter\{page\}\{1\}\s*", "", body)
    body = re.sub(r"^%TCIDATA\{.*?\}\s*$", "", body, flags=re.MULTILINE)
    return body.strip()


def _split_slides(body: str) -> list[str]:
    parts = re.split(r"\\pagebreak\s*", body, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p.strip()]


_TITLE_MAX_LEN = 72

_SKIP_ITEXT = frozenset({
    "a",
    "static",
    "dynamic",
    "optimizing behavior",
    "defining an equilibrium",
    "competitive equilibrium",
    "numeraire",
    "excess demand",
    "distortionary tax",
    "social planner",
    "pareto optimal",
    "cobb-douglas",
    "analytically",
    "numerically",
})


def _clean_title(raw: str) -> str:
    title = raw.replace(r"\ ", " ").strip()
    title = re.sub(r":\s*$", "", title).strip()
    title = re.sub(r",\s*$", "", title).strip()
    title = re.sub(r"\s+", " ", title)
    return title or "Untitled"


def _truncate_title(title: str, max_len: int = _TITLE_MAX_LEN) -> str:
    title = title.strip()
    if len(title) <= max_len:
        return title
    cut = title[: max_len - 3].rsplit(" ", 1)[0]
    return (cut or title[: max_len - 3]) + "..."


def _plain_text_from_latex(fragment: str) -> str:
    text = fragment
    text = re.sub(r"(?s)\\footnote(?:\%)?\{.*?\}", " ", text)
    text = re.sub(r"\$[^$]*\$", " ", text)
    text = re.sub(r"\\(?:begin|end)\{[^}]+\}", " ", text)
    text = re.sub(r"\\(?:textit|textbf|emph|text|noindent)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:label|ref|eqref|cite)\{[^}]+\}", " ", text)
    text = re.sub(r"\\[A-Za-z@]+", " ", text)
    text = re.sub(r"[{}%\\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_bad_title(title: str) -> bool:
    if not title or len(title) < 8:
        return True
    if re.search(r"[$\\{}_^]| zk| o |\(\s*\)", title):
        return True
    words = title.split()
    if words and words[0].islower():
        return True
    if title.lower().startswith(("strictly ", "makes ", "knowing ", "here, ", "`")):
        return True
    return False


def _finalize_title(title: str) -> Optional[str]:
    title = _clean_title(title)
    if _is_bad_title(title):
        return None
    return title


def _is_skip_itext(candidate: str) -> bool:
    lowered = candidate.lower().strip(" .")
    if len(lowered) < 4:
        return True
    return lowered in _SKIP_ITEXT


def _remove_line_at_index(text: str, line_index: int) -> str:
    lines = text.splitlines()
    if line_index < 0 or line_index >= len(lines):
        return text
    return "\n".join(lines[:line_index] + lines[line_index + 1 :]).strip()


def _title_from_first_sentence(body: str) -> Optional[str]:
    pre = re.split(r"\\begin\{", body, maxsplit=1)[0]
    lines = [ln.strip() for ln in pre.splitlines() if ln.strip()]
    if not lines:
        return None

    buf: list[str] = []
    for line in lines[:4]:
        if line.startswith("\\begin") or line.startswith("\\FRAME"):
            break
        buf.append(line)
        plain = _plain_text_from_latex(" ".join(buf))
        if len(plain) >= 15 and (plain.endswith(".") or len(plain.split()) >= 7):
            break

    plain = _plain_text_from_latex(" ".join(buf))
    if len(plain) < 10:
        return None

    plain = re.sub(r"\s+has the following features\.?$", "", plain, flags=re.IGNORECASE)
    plain = re.sub(r",\s*,", ",", plain)
    plain = re.sub(r"\s+,", ",", plain)
    plain = re.sub(r",\s+", ", ", plain)
    plain = re.sub(r"\.+$", "", plain).strip()
    return _finalize_title(_truncate_title(plain))


def _title_from_first_item(body: str) -> Optional[str]:
    if not re.match(r"\\begin\{(?:itemize|enumerate)\}", body.lstrip()):
        return None

    m = re.search(
        r"\\begin\{(?:itemize|enumerate)\}\s*\\item\s+"
        r"(?:\\textit\{([^}]+)\}|\\textbf\{([^}]+)\})",
        body,
        flags=re.DOTALL,
    )
    if m:
        candidate = _clean_title(m.group(1) or m.group(2) or "")
        if candidate and not _is_skip_itext(candidate):
            return _finalize_title(_truncate_title(candidate))

    m2 = re.search(
        r"\\begin\{(?:itemize|enumerate)\}\s*\\item\s+(.+?)(?:\n|\\item|\\end)",
        body,
        flags=re.DOTALL,
    )
    if m2:
        plain = _plain_text_from_latex(m2.group(1))
        sentence = plain.split(".")[0].strip()
        if len(sentence) >= 12:
            return _finalize_title(_truncate_title(sentence))
    return None


def _title_from_part_marker(body: str) -> Optional[str]:
    m = re.search(
        r"\\textbf\{\s*Part\s*\(([a-z])\)\s*\}|\\textbf\{\s*Step\s*(\d+)",
        body,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    if m.group(1):
        return f"Part ({m.group(1)})"
    return f"Step {m.group(2)}"


def _find_standalone_heading_line(body: str) -> Optional[tuple[str, str]]:
    lines = body.splitlines()
    for idx, line in enumerate(lines[:8]):
        stripped = line.strip()
        for pattern in (
            r"^(?:\\noindent\s+)?\\textit\{([^}]+)\}$",
            r"^(?:\\noindent\s+)?\\textbf\{([^}]+)\}$",
        ):
            m = re.fullmatch(pattern, stripped)
            if not m:
                continue
            title = _clean_title(m.group(1))
            if _is_skip_itext(title):
                continue
            finalized = _finalize_title(title)
            if not finalized:
                continue
            new_body = _remove_line_at_index(body, idx)
            return finalized, new_body if new_body else body
    return None


def _escape_frame_title(title: str) -> str:
    for old, new in {
        "&": r"\&",
        "%": r"\%",
        "#": r"\#",
        "_": r"\_",
        "'": r"{'}",
    }.items():
        title = title.replace(old, new)
    return title


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


def _infer_list_env(body: str, fallback: str = "itemize") -> str:
    for env in ("enumerate", "itemize", "description"):
        if re.search(rf"\\end\{{{env}\}}", body):
            return env
    return fallback


def _close_open_lists(body: str) -> str:
    stack = _list_env_stack(body)
    if stack:
        body = body.rstrip() + "\n" + "\n".join(f"\\end{{{env}}}" for env in reversed(stack))
    return body


def _repair_cross_slide_lists(slides: list[str]) -> list[str]:
    """Repair SWP pagebreaks that split list environments across slides."""
    if not slides:
        return slides

    repaired: list[str] = []
    carry_env: Optional[str] = None

    for i, slide in enumerate(slides):
        body = slide.strip()
        next_body = slides[i + 1].strip() if i + 1 < len(slides) else ""

        if carry_env:
            m_end = re.match(rf"^\s*\\end\{{{carry_env}\}}\s*", body)
            if m_end:
                body = body[m_end.end() :].lstrip()
            carry_env = None

        if re.match(r"^\s*\\item\b", body) and not re.match(
            r"^\s*\\begin\{(itemize|enumerate|description)\}", body
        ):
            body = f"\\begin{{{_infer_list_env(body)}}}\n" + body

        if (
            next_body
            and re.search(r"\\item\s*$", body)
            and not re.match(r"^\s*\\item\b", next_body)
            and not re.match(r"^\s*\\begin\{(itemize|enumerate|description)\}", next_body)
            and not re.match(r"^\s*\\end\{(itemize|enumerate|description)\}", next_body)
        ):
            body = re.sub(r"\\item\s*$", "", body.rstrip())
            env = _infer_list_env(body)
            if _list_env_stack(body):
                body = _close_open_lists(body)
            slides[i + 1] = f"\\begin{{{env}}}\n\\item {next_body}"

        open_stack = _list_env_stack(body)
        if open_stack and next_body:
            if re.match(r"^\s*\\item\b", next_body) and not re.match(
                r"^\s*\\begin\{(itemize|enumerate|description)\}", next_body
            ):
                body = _close_open_lists(body)
                carry_env = None
            elif re.match(rf"^\s*\\end\{{{open_stack[-1]}\}}", next_body):
                body = _close_open_lists(body)
                carry_env = open_stack[-1]
            else:
                body = _close_open_lists(body)
        else:
            body = _close_open_lists(body)

        repaired.append(body.strip())

    return repaired


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


def _replace_swp_frames(body: str, *, plot_counter: Optional[list[int]] = None) -> str:
    marker = r"\FRAME{"
    out: list[str] = []
    i = 0
    if plot_counter is None:
        plot_counter = [0]

    while True:
        idx = body.find(marker, i)
        if idx == -1:
            out.append(body[i:])
            break

        out.append(body[i:idx])
        pos = idx + len(marker) - 1

        try:
            args: list[str] = []
            for _ in range(8):
                arg, pos = _consume_braced_arg(body, pos)
                args.append(arg)

            plot_counter[0] += 1
            caption = args[4].strip() if len(args) > 4 else ""
            if caption.startswith(r"\Qcb{") and caption.endswith("}"):
                caption = caption[len(r"\Qcb{") : -1]
            caption = caption.replace(r"\protect", "").strip()
            out.append(_plot_placeholder(caption))
            i = pos
        except ValueError:
            out.append(marker)
            i = idx + len(marker)

    return "".join(out)


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


def _sanitize_slide_body(body: str, *, plot_counter: Optional[list[int]] = None) -> str:
    body = re.sub(r"\s+\\\s*$", "", body, flags=re.MULTILINE)
    body = _replace_swp_frames(body, plot_counter=plot_counter)
    body = _replace_includegraphics(body)
    body = _replace_footnotes(body)
    body = re.sub(r"\\section\*?\{[^}]*\}\s*", "", body)
    body = _close_open_lists(body.strip())
    body = re.sub(
        r"^\s*\\(?:newline|bigskip|smallskip)\s*(?:\n|$)",
        "",
        body,
        flags=re.MULTILINE,
    )
    return body.strip()


def _extract_title_and_body(slide_text: str, slide_num: int) -> tuple[str, str]:
    text = slide_text.strip()

    if slide_num == 1:
        return "Introduction", text

    lines = text.split("\n", 1)
    first = lines[0].strip()

    for pattern, group in (
        (r"(?:\\noindent\s+)?\\textit\{([^}]+)\}", 1),
        (r"(?:\\noindent\s+)?\\textbf\{([^}]+)\}", 1),
    ):
        m = re.fullmatch(pattern, first)
        if m:
            title = _finalize_title(_clean_title(m.group(group)))
            if title:
                body = lines[1].strip() if len(lines) > 1 else ""
                return title, body if body else text

    m_inline = re.match(
        r"^(?:\\noindent\s+)?(?:The\s+)?\\textit\{([^}]+)\}\s*",
        text,
        flags=re.IGNORECASE,
    )
    if m_inline:
        title = _finalize_title(_clean_title(m_inline.group(1)))
        if title and not _is_skip_itext(title):
            body = text[m_inline.end() :].strip()
            return title, body if body else text

    heading = _find_standalone_heading_line(text)
    if heading and _finalize_title(heading[0]):
        return _finalize_title(heading[0]) or f"Slide {slide_num}", heading[1]

    for guesser in (_title_from_part_marker, _title_from_first_sentence, _title_from_first_item):
        title = guesser(text)
        if title:
            return title, text

    return f"Slide {slide_num}", text


def _resolve_input_path(filename: PathLike, search_dirs: Optional[Sequence[PathLike]] = None) -> Path:
    path = Path(filename).expanduser()
    candidates = [path]
    if search_dirs:
        candidates.extend(Path(d) / path.name for d in search_dirs)
    here = Path.cwd()
    candidates.extend([
        here / path,
        here / path.name,
        _DEFAULT_MATERIALS / path.name,
        _PKG_DIR.parent / "905_materials" / path.name,
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


def _output_path(input_path: Path, output_filename: Optional[PathLike]) -> Path:
    if output_filename is not None:
        return Path(output_filename).expanduser()
    return input_path.with_name(f"{input_path.stem}_slides.tex")


def _build_document(
    slides: list[str],
    *,
    title: str,
    author: str,
    institute: str,
    date: str,
    aspectratio: str,
) -> tuple[str, int]:
    chunks = [_make_preamble(
        title=title,
        author=author,
        institute=institute,
        date=date,
        aspectratio=aspectratio,
    )]

    plot_counter = [0]
    for i, slide in enumerate(slides, start=1):
        frame_title, frame_body = _extract_title_and_body(slide, i)
        chunks.append("\n\\begin{frame}\n")
        chunks.append(f"\\frametitle{{{_escape_frame_title(frame_title)}}}\n")
        chunks.append(_sanitize_slide_body(frame_body, plot_counter=plot_counter))
        chunks.append("\n\\end{frame}\n")

    chunks.append("\n\\end{document}\n")
    return "".join(chunks), plot_counter[0]


def convert_swp_slides(
    filename: PathLike,
    *,
    output_filename: Optional[PathLike] = None,
    title: str = "Course Lecture Notes",
    author: str = "",
    institute: str = "Kansas State University",
    date: str = "",
    aspectratio: str = "169",
    search_dirs: Optional[Sequence[PathLike]] = None,
    validate: bool = True,
) -> Path:
    input_path = _resolve_input_path(filename, search_dirs=search_dirs)
    raw = input_path.read_text(encoding="utf-8", errors="replace")
    body = _strip_swp_preamble(raw)
    slides = _repair_cross_slide_lists(_split_slides(body))

    if not slides:
        raise ValueError(f"No slide content found in {input_path.name}")

    out_path = _output_path(input_path, output_filename)
    document, plot_count = _build_document(
        slides,
        title=title,
        author=author,
        institute=institute,
        date=date,
        aspectratio=aspectratio,
    )
    out_path.write_text(document, encoding="utf-8")

    print(f"Read:     {input_path}")
    print(f"Wrote:    {out_path}")
    print(f"Slides:   {len(slides)} content frames + 1 title frame")
    print(f"Plots:    {plot_count} replaced with text placeholder(s)")
    print(f"Compile:  bash accessible_slides/compile_pdf.sh {input_path.stem} {input_path.parent}")

    if validate:
        from validate_accessible_tex import print_validation_report, validate_accessible_tex

        print()
        print_validation_report(validate_accessible_tex(out_path))

    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Scientific Word slides to accessible ltx-talk."
    )
    parser.add_argument("filename", nargs="?", help="Input .tex file")
    parser.add_argument("--title", default="Course Lecture Notes")
    parser.add_argument("--author", default="")
    parser.add_argument("--institute", default="Kansas State University")
    parser.add_argument("--date", default="")
    parser.add_argument("--output", default=None)
    parser.add_argument("--no-validate", action="store_true")
    parser.add_argument("--validate-only", metavar="TEX", help="Validate an existing .tex file")
    args = parser.parse_args()

    if args.validate_only:
        from validate_accessible_tex import print_validation_report, validate_accessible_tex

        report = validate_accessible_tex(args.validate_only)
        print_validation_report(report)
        return 0 if report.canvas_ready else 1

    if not args.filename:
        parser.error("filename is required unless --validate-only is used")

    convert_swp_slides(
        args.filename,
        output_filename=args.output,
        title=args.title,
        author=args.author,
        institute=args.institute,
        date=args.date,
        search_dirs=[str(_DEFAULT_MATERIALS), "."],
        validate=not args.no_validate,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
