"""Собирает ZIP-сабмит по Техническому регламенту: все файлы в корне архива, без папок.

Платформа переносит только корневые .py и requirements.txt, поэтому модули legal_core
кладутся в корень как lc_<имя>.py, а импорты в них переписываются.

  python submission/build_zip.py            # -> dist/submission.zip
"""

import argparse
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ["config", "parsing", "chunking", "lexical", "embeddings", "index", "retrieval",
           "llm", "prompts", "guards", "verify", "pipeline"]


def flatten_imports(source):
    source = re.sub(r"from legal_core\.(\w+) import", r"from lc_\1 import", source)
    return re.sub(r"from legal_core import (\w+)", r"import lc_\1 as \1", source)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist" / "submission.zip"))
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("solution.py", flatten_imports((ROOT / "submission" / "solution.py").read_text(encoding="utf-8")))
        zf.write(ROOT / "submission" / "requirements.txt", "requirements.txt")
        for name in MODULES:
            source = (ROOT / "legal_core" / f"{name}.py").read_text(encoding="utf-8")
            zf.writestr(f"lc_{name}.py", flatten_imports(source))
    leftovers = [n for n in zipfile.ZipFile(out).namelist() if "legal_core" in zipfile.ZipFile(out).read(n).decode("utf-8")
                 and n != "solution.py"]
    if leftovers:
        raise SystemExit(f"Остались импорты legal_core в: {leftovers}")
    print(f"Архив: {out} ({out.stat().st_size / 1024:.0f} КБ), файлов: {len(zipfile.ZipFile(out).namelist())}")


if __name__ == "__main__":
    main()
