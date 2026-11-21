#!/usr/bin/env python3
"""
Merge a range of numbered PDF files into one PDF.

Usage (Windows cmd, inside your activated venv):
    python -m pip install pypdf
    python merge_pdfs_by_range.py C:\\Users\\ishar\\Downloads\\MyPDFs --range 3-7
    python merge_pdfs_by_range.py C:\\Users\\ishar\\Downloads\\MyPDFs --range 1,4,6-9 --output combined.pdf
    python merge_pdfs_by_range.py . --pattern "Chapter*.pdf" --range all
    python merge_pdfs_by_range.py . --range 3-7 --list        (dry run: only list what would merge)
    

How the range works:
    The script reads the NUMBER in each file name (e.g. file3.pdf -> 3,
    "Chapter 12 - Intro.pdf" -> 12) and merges the files whose number
    falls in the range you give, in numeric order (so file2 comes
    before file10, unlike plain alphabetical sorting).

    --range accepts:  "3-7"  |  "1,4,6-9"  |  "5"  |  "all"
    --number first|last : which number in the name to use when a file
    name has more than one (default: last, so "report 2024 part 3.pdf"
    uses 3, not 2024).
"""

import argparse
import glob
import os
import re
import sys

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # pragma: no cover
    try:
        from PyPDF2 import PdfReader, PdfWriter
    except ImportError:
        print("ERROR: neither 'pypdf' nor 'PyPDF2' is installed.")
        print("Install one with:  python -m pip install pypdf")
        sys.exit(2)


def extract_number(filename, which="last"):
    """Return the number in a file name, or None if it has none."""
    numbers = re.findall(r"\d+", os.path.basename(filename))
    if not numbers:
        return None
    return int(numbers[-1] if which == "last" else numbers[0])


def parse_range(spec):
    """Parse '3-7', '1,4,6-9', '5' or 'all' into a set of ints (or None for all)."""
    spec = spec.strip().lower()
    if spec == "all":
        return None
    wanted = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = int(start.strip()), int(end.strip())
            if start > end:
                start, end = end, start
            wanted.update(range(start, end + 1))
        else:
            wanted.add(int(part))
    if not wanted:
        raise ValueError(f"could not parse range: {spec!r}")
    return wanted


def main():
    parser = argparse.ArgumentParser(
        description="Merge numbered PDF files in a given range into one PDF."
    )
    parser.add_argument(
        "folder",
        nargs="?",
        default=".",
        help="Folder containing the PDF files (default: current folder).",
    )
    parser.add_argument(
        "--pattern",
        default="*.pdf",
        help='File name pattern to match, e.g. "Chapter*.pdf" (default: "*.pdf").',
    )
    parser.add_argument(
        "--range",
        required=True,
        help='Which file numbers to merge: "3-7", "1,4,6-9", "5" or "all".',
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output PDF name (default: merged_<range>.pdf in the same folder).",
    )
    parser.add_argument(
        "--number",
        choices=["first", "last"],
        default="last",
        help="Which number in the name to use when a name has several (default: last).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Dry run: list the files that would be merged, without merging.",
    )
    args = parser.parse_args()

    folder = os.path.abspath(args.folder)
    if not os.path.isdir(folder):
        print(f"ERROR: folder not found: {folder}")
        return 1

    try:
        wanted = parse_range(args.range)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 1

    # Find files and number them. Skip the script's own outputs
    # (merged_*.pdf) so a second run in the same folder does not
    # merge the previous result back in.
    paths = sorted(glob.glob(os.path.join(folder, args.pattern)))
    numbered = []
    skipped = []
    for path in paths:
        base = os.path.basename(path)
        if base.lower().startswith("merged_"):
            continue
        num = extract_number(path, args.number)
        if num is None:
            skipped.append(os.path.basename(path))
        else:
            numbered.append((num, path))
    numbered.sort(key=lambda item: (item[0], os.path.basename(item[1])))

    if skipped:
        print(f"Note: skipped {len(skipped)} file(s) with no number in the name: "
              + ", ".join(skipped))

    if not numbered:
        print(f"ERROR: no numbered PDF files matching {args.pattern!r} in {folder}")
        return 1

    if wanted is None:
        selected = numbered
        range_label = "all"
    else:
        selected = [(n, p) for n, p in numbered if n in wanted]
        missing = sorted(wanted - {n for n, _ in numbered})
        if missing:
            print(f"Warning: no file found for number(s): {', '.join(map(str, missing))}")
        range_label = args.range.replace(",", "_").replace("-", "to")

    if not selected:
        print(f"ERROR: no files in range {args.range!r}. "
              f"Found numbers: {min(n for n, _ in numbered)}-"
              f"{max(n for n, _ in numbered)} "
              f"({len(numbered)} file(s)).")
        return 1

    print(f"Files to merge ({len(selected)}):")
    for num, path in selected:
        print(f"  {num:>4}  {os.path.basename(path)}")

    if args.list:
        print("Dry run (--list): nothing merged.")
        return 0

    output = args.output
    if not output:
        output = os.path.join(folder, f"merged_{range_label}.pdf")
    elif not os.path.isabs(output):
        output = os.path.join(folder, output)

    writer = PdfWriter()
    total_pages = 0
    for num, path in selected:
        reader = PdfReader(path)
        pages = len(reader.pages)
        for page in reader.pages:
            writer.add_page(page)
        total_pages += pages
        print(f"  added {os.path.basename(path)} ({pages} page(s))")

    with open(output, "wb") as fh:
        writer.write(fh)

    print(f"\nDone: {len(selected)} file(s), {total_pages} page(s) -> {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
