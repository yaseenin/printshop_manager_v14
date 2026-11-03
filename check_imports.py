r"""
check_imports.py - finds every "cannot import name X from Y" BEFORE you run the app.

Put it in the project folder (next to main.py) and run:   python check_imports.py
It only reads your files; it changes nothing.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".vscode", "__pycache__", "venv", ".venv", "env", "node_modules",
             "dbfix", "backup", "backups", "assets", "logs", "build", "dist"}


def skip(path):
    parts = path.relative_to(ROOT).parts
    return any(p in SKIP_DIRS or p.startswith("_LOCAL_HISTORY") for p in parts[:-1])


def module_file(mod):
    base = ROOT.joinpath(*mod.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.is_dir():
        return base            # plain folder used as a package
    return None


def _targets(t, names):
    if isinstance(t, ast.Name):
        names.add(t.id)
    elif isinstance(t, (ast.Tuple, ast.List)):
        for e in t.elts:
            _targets(e, names)


def _collect(body, names, flags):
    for n in body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            for t in n.targets:
                _targets(t, names)
        elif isinstance(n, (ast.AnnAssign, ast.AugAssign)):
            _targets(n.target, names)
        elif isinstance(n, ast.Import):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "*":
                    flags["star"] = True
                else:
                    names.add(a.asname or a.name)
        elif isinstance(n, (ast.If, ast.For, ast.While, ast.With, ast.AsyncWith)):
            _collect(n.body, names, flags)
            _collect(getattr(n, "orelse", []), names, flags)
        elif isinstance(n, ast.Try):
            _collect(n.body, names, flags)
            for h in n.handlers:
                _collect(h.body, names, flags)
            _collect(n.orelse, names, flags)
            _collect(n.finalbody, names, flags)


_cache = {}


def top_level_names(path):
    if path not in _cache:
        names, flags = set(), {"star": False}
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            _collect(tree.body, names, flags)
        except SyntaxError as e:
            flags["error"] = str(e)
        _cache[path] = (names, flags)
    return _cache[path]


def local_roots():
    roots = set()
    for p in ROOT.iterdir():
        if p.name in SKIP_DIRS or p.name.startswith("_LOCAL_HISTORY"):
            continue
        if p.is_file() and p.suffix == ".py":
            roots.add(p.stem)
        elif p.is_dir() and any(p.rglob("*.py")):
            roots.add(p.name)
    return roots


def main():
    roots = local_roots()
    problems = {}
    files = [p for p in ROOT.rglob("*.py") if not skip(p) and p.name != Path(__file__).name]

    for f in sorted(files):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as e:
            problems.setdefault(f, []).append(f"line {e.lineno}: SYNTAX ERROR in this file: {e.msg}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".")[0] not in roots:
                    continue
                target = module_file(node.module)
                if target is None:
                    problems.setdefault(f, []).append(
                        f"line {node.lineno}: module '{node.module}' does not exist")
                    continue
                if target.is_dir():
                    continue
                names, flags = top_level_names(target)
                if flags["star"]:
                    continue
                for a in node.names:
                    if a.name == "*" or a.name in names:
                        continue
                    if module_file(f"{node.module}.{a.name}"):
                        continue        # it's a submodule
                    problems.setdefault(f, []).append(
                        f"line {node.lineno}: '{a.name}' is missing from {node.module} "
                        f"({target.relative_to(ROOT)})")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in roots and module_file(a.name) is None:
                        problems.setdefault(f, []).append(
                            f"line {node.lineno}: module '{a.name}' does not exist")

    print(f"Checked {len(files)} files in {ROOT}\n")
    if not problems:
        print("No missing imports found.")
        return
    total = sum(len(v) for v in problems.values())
    print(f"Found {total} problem(s):\n")
    for f, items in problems.items():
        print(f.relative_to(ROOT))
        for it in items:
            print("   ", it)
        print()
    print("Each missing name was probably added after your last commit. Restore that file from a")
    print("backup / Local History if you can; otherwise paste the file here and I'll rebuild the function.")


if __name__ == "__main__":
    main()
