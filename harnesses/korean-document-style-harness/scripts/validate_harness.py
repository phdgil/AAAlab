"""Validate this public instruction package using only the standard library."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "README.md", "LICENSE", "LICENSE_AUDIT.md", "EVALUATION.md",
    "skills/korean-document-style/SKILL.md",
    "skills/korean-document-style/references/reports.md",
    "skills/korean-document-style/references/slides.md",
    "skills/korean-document-style/maintenance/feedback.md",
    "skills/evolve/SKILL.md", "skills/evolve/maintenance/feedback.md",
)


def main():
    errors = []
    for name in REQUIRED:
        if not (ROOT / name).is_file():
            errors.append(f"Missing {name}")
    files = list(ROOT.rglob("*.md"))
    for file in files:
        text = file.read_text(encoding="utf-8-sig")
        if file.name == "SKILL.md":
            match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
            if not match:
                errors.append(f"Invalid frontmatter: {file.relative_to(ROOT)}")
            else:
                fields = dict(re.findall(r"^(name|description):\s*(.+)$", match[1], re.M))
                if fields.get("name") != file.parent.name or not fields.get("description"):
                    errors.append(f"Invalid skill metadata: {file.relative_to(ROOT)}")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if re.match(r"[a-z]+://|#", target):
                continue
            linked = (file.parent / target.split("#", 1)[0]).resolve()
            if not linked.is_file() or not linked.is_relative_to(ROOT):
                errors.append(f"Invalid relative link: {file.relative_to(ROOT)} -> {target}")
        patterns = {
            "absolute Windows path": r"(?i)\b[A-Z]:[\\/]",
            "session identifier": r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            "credential pattern": r"(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})",
        }
        for label, pattern in patterns.items():
            if re.search(pattern, text):
                errors.append(f"Public-package {label}: {file.relative_to(ROOT)}")
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {len(files)} Markdown files; required files, frontmatter, links, public-package patterns")


if __name__ == "__main__":
    main()
