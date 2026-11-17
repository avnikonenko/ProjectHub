"""Assemble the built UI and repository resources into a release wheel."""
import json
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.install_lib import install_lib
from setuptools.command.sdist import sdist


ROOT = Path(__file__).resolve().parent
BUNDLE = Path("backend", "app", "_distribution")
ASSET_SUFFIXES = {
    ".js", ".mjs", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".ico", ".woff", ".woff2", ".ttf", ".wasm",
}


class FrontendEntries(HTMLParser):
    """Collect the bundled assets used directly by the Vite entry page."""

    def __init__(self):
        super().__init__()
        self.assets = set()
        self.scripts = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'script':
            reference = attributes.get('src')
        elif tag == 'link' and {'stylesheet', 'modulepreload', 'preload', 'icon'}.intersection(
                (attributes.get('rel') or '').lower().split()):
            reference = attributes.get('href')
        else:
            return
        if not reference:
            return
        url = urlsplit(reference)
        if url.scheme or url.netloc or not url.path:
            return
        path = PurePosixPath(url.path.lstrip('/'))
        self.assets.add(path)
        if tag == 'script' and path.suffix in {'.js', '.mjs'}:
            self.scripts.add(path)


def manifest_assets(frontend, entry_scripts):
    """Return every asset emitted by Vite, including lazy chunks and their dependencies."""
    path = frontend / '.vite' / 'manifest.json'
    rebuild = 'Rebuild the frontend before packaging.'
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError(f'The frontend build manifest is missing or symlinked. {rebuild}')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError(f'The frontend build manifest is invalid. {rebuild}') from exc
    if not isinstance(manifest, dict) or not all(isinstance(record, dict) for record in manifest.values()):
        raise RuntimeError(f'The frontend build manifest is invalid. {rebuild}')

    files = set()
    for record in manifest.values():
        file = record.get('file')
        if not isinstance(file, str) or not file:
            raise RuntimeError(f'The frontend build manifest is missing a file path. {rebuild}')
        files.add(PurePosixPath(file))
        for field in ('css', 'assets', 'imports', 'dynamicImports'):
            references = record.get(field, [])
            if not isinstance(references, list) or not all(isinstance(ref, str) and ref for ref in references):
                raise RuntimeError(f'The frontend build manifest has malformed {field}. {rebuild}')
            if field in ('css', 'assets'):
                files.update(PurePosixPath(ref) for ref in references)
            elif any(ref not in manifest for ref in references):
                raise RuntimeError(f'The frontend build manifest has missing chunk entries. {rebuild}')
    entry = manifest.get('index.html', {})
    if not entry.get('isEntry') or PurePosixPath(entry['file']) not in entry_scripts:
        raise RuntimeError(f'The frontend build manifest does not match index.html. {rebuild}')
    return files


def release_files():
    frontend = ROOT / "frontend" / "dist"
    assets = frontend / "assets"
    index = frontend / "index.html"
    compiled = sorted(
        path for path in assets.rglob("*") if path.is_file() and path.suffix in ASSET_SUFFIXES
    )
    if not index.is_file():
        raise RuntimeError(
            "The frontend build is missing. Run npm ci and npm run build in frontend/ before building a ProjectHub wheel."
        )
    entries = FrontendEntries()
    entries.feed(index.read_text(encoding='utf-8'))
    entries.close()
    if not entries.scripts:
        raise RuntimeError('The frontend index has no local JavaScript entry. Rebuild the frontend before packaging.')
    compiled_paths = {path.relative_to(frontend) for path in compiled}
    missing = entries.assets - compiled_paths
    if missing:
        paths = ', '.join(str(path) for path in sorted(missing))
        raise RuntimeError(f'Frontend assets referenced by index.html are missing from the wheel inputs: {paths}')
    missing = manifest_assets(frontend, entries.scripts) - compiled_paths
    if missing:
        paths = ', '.join(str(path) for path in sorted(missing))
        raise RuntimeError(f'Frontend assets referenced by the Vite manifest are missing from the wheel inputs: {paths}')
    files = [(index, BUNDLE / "frontend" / "index.html")]
    files.extend((path, BUNDLE / "frontend" / path.relative_to(frontend)) for path in compiled)
    migrations = ROOT / "migrations"
    revisions = sorted((migrations / "versions").glob("*.py"))
    if not revisions:
        raise RuntimeError("ProjectHub database migration files are missing.")
    files.extend((path, BUNDLE / path.relative_to(ROOT)) for path in [
        migrations / "env.py", *revisions, ROOT / "AI_RECOMMENDATIONS.md",
        ROOT / "skills" / "projecthub-notes" / "SKILL.md",
    ])
    for path, _ in files:
        if not path.is_file() or any(
            parent.is_symlink() for parent in (path, *path.parents) if parent != ROOT and ROOT in parent.parents
        ):
            raise RuntimeError(f"Missing or symlinked ProjectHub release resource: {path.relative_to(ROOT)}")
    return files


class BuildModules(build_py):
    def find_package_modules(self, package, package_dir):
        modules = super().find_package_modules(package, package_dir)
        if package == "scripts":
            # The installed CLI needs this bridge. Other scripts serve checkout
            # development or test workflows and are kept out of the package.
            return [entry for entry in modules if entry[1] in {"notes_cli", "__init__"}]
        return modules


class BundleResources(install_lib):
    """Add resources to the wheel staging directory, including sdist builds."""

    def install(self):
        # Setuptools normally copies the entire build directory. Use declared
        # inputs so a stale module left by an earlier build cannot be shipped.
        files = [(Path(source), Path(source).relative_to(self.build_dir)) for source in self.get_inputs()]
        files.extend(release_files())
        installed = []
        for source, relative in files:
            destination = Path(self.install_dir) / relative
            self.mkpath(str(destination.parent))
            self.copy_file(str(source), str(destination))
            installed.append(str(destination))
        return installed

    def get_outputs(self):
        return super().get_outputs() + [
            str(Path(self.install_dir) / relative) for _, relative in release_files()
        ]


class ReleaseSdist(sdist):
    def run(self):
        release_files()
        super().run()


setup(cmdclass={"build_py": BuildModules, "install_lib": BundleResources, "sdist": ReleaseSdist})
