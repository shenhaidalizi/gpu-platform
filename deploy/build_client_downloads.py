"""Build the static client downloads before the Vite production build."""
import argparse
from html import escape
import shlex
from urllib.parse import urlsplit
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ("gpu-platform-client", "gpu-platform-mcp")


def build_wheels(scratch):
    wheels = scratch / "wheels"
    sources = []
    for project in PROJECTS:
        source = ROOT / "packages" / project
        clean = scratch / project
        clean.mkdir()
        shutil.copyfile(source / "pyproject.toml", clean / "pyproject.toml")
        shutil.copytree(source / "src", clean / "src",
                        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc"))
        sources.append(clean)
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", wheels,
                    *sources], check=True)
    return wheels


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-dir", type=Path, help="package the prebuilt wheel pair for a deployed release")
    parser.add_argument("--platform-url", default="http://10.249.41.63:8765",
                        help="advertised address reachable from client machines")
    args = parser.parse_args()
    origin = args.platform_url.rstrip("/")
    url = urlsplit(origin)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.query or url.fragment:
        parser.error("--platform-url must be an HTTP(S) platform address without credentials, query or fragment")
    output = ROOT / "web/public/downloads"
    with tempfile.TemporaryDirectory() as scratch:
        wheels = args.wheel_dir or build_wheels(Path(scratch))
        page = (ROOT / "deploy/client-downloads/index.html").read_text()
        page = page.replace("@platform-url@", escape(origin)).replace("@platform-shell@", escape(shlex.quote(origin)))
        output.mkdir(parents=True, exist_ok=True)
        for project in PROJECTS:
            matches = list(wheels.glob(project.replace("-", "_") + "-*-py3-none-any.whl"))
            if len(matches) != 1:
                raise RuntimeError(f"Missing expected wheel for {project}")
            wheel = matches[0]
            shutil.copyfile(wheel, output / wheel.name)
            page = page.replace("@" + project + "@", wheel.name)
        shutil.copyfile(ROOT / "skills/gpu-jobs/SKILL.md", output / "SKILL.md")
        (output / "index.html").write_text(page)


if __name__ == "__main__":
    main()
