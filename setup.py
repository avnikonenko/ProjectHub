"""Assemble the built UI and repository resources into a release wheel."""
from pathlib import Path

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


def release_files():
    frontend = ROOT / "frontend" / "dist"
    assets = frontend / "assets"
    index = frontend / "index.html"
    compiled = sorted(
        path for path in assets.rglob("*") if path.is_file() and path.suffix in ASSET_SUFFIXES
    )
    if not index.is_file() or not any(path.suffix in {".js", ".mjs"} for path in compiled):
        raise RuntimeError(
            "The frontend build is missing. Run npm ci and npm run build in frontend/ before building a ProjectHub wheel."
        )
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
