#!/usr/bin/env python3
"""Compute the Connect provider interface and the VoIP Innovations gap from code.

Parses the core ``connect`` module and every provider module with ``ast`` (Python)
and a narrow regex pass (manifest assets, OWL registry registrations, patches).
An extension point is anything a module plugs into the core: an ``_inherit``
target, a method or field added to it, a ``selection_add`` value, an http route,
a provider-named model, an asset bundle, a JS registry key or a patch.

Provider names are normalised to ``{provider}`` so that parallel implementations
line up: ``/twilio/webhook/number`` and ``/voicetel/webhook/number`` are one
point, ``/{provider}/webhook/number``. An interface slot is an extension point
that two or more provider modules implement.

Each slot is then scored for ``connect_voip_innovations`` against the design
mapping in ``provider_graph/vi_mapping.json`` and the VI catalog in
``provider_graph/vi_catalog.json``:

* ``implemented`` - the module exists and fills the slot;
* ``missing``     - VI methods (or the module alone) can fill it, and nothing does yet;
* ``unmapped``    - no VI method maps to it and the mapping does not claim it;
* ``blocked``     - it depends on at least one unverified VI capability.

Output: ``graph.json`` with ``generated_at``, ``connect_addons_sha``, ``nodes``,
``edges`` and ``totals``. Re-run it after any change to recompute the remaining work.

    python3 scripts/provider_graph.py [--root DIR] [--out FILE]
"""
import argparse
import ast
import datetime
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "provider_graph"

CORE = "connect"
PROVIDERS = ["connect_twilio", "connect_voicetel", "connect_pbx", "connect_byoc"]
TARGET = "connect_voip_innovations"
TARGET_TOKENS = ["voip_innovations", "vi"]
SKIP_DIRS = {"tests", "migrations", "upgrades", "doc", "docs"}
SKIP_METHODS = {"__init__"}
REGISTRY_RE = re.compile(r"""registry\s*\.\s*category\(\s*["']([\w.]+)["']\s*\)\s*\.add\(\s*["']([^"']+)["']""")
PATCH_RE = re.compile(r"""\bpatch\(\s*([A-Za-z_$][\w$.]*)""")
SETTINGS_TARGETS = {"connect.settings", "res.config.settings", "connect.user"}


def rel(path, root):
    return str(path.relative_to(root))


def git_sha(root):
    try:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def git_dirty(root, modules):
    try:
        out = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", *modules],
                             capture_output=True, text=True, check=True).stdout
        return bool(out.strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def manifest(root, module):
    path = root / module / "__manifest__.py"
    return ast.literal_eval(path.read_text()) if path.exists() else None


def tokens_for(module, depends):
    """Provider name tokens a module's identifiers carry: its own short name and its provider family."""
    short = module[len("connect_"):] if module.startswith("connect_") else module
    toks = [short]
    for dep in depends:
        if dep in PROVIDERS and dep != module:
            toks.append(dep[len("connect_"):])
    if module == TARGET:
        toks = list(TARGET_TOKENS)
    return sorted(set(toks), key=len, reverse=True)


def normaliser(tokens):
    if not tokens:
        return lambda s: s
    alt = "|".join(re.escape(t) for t in tokens)
    rx = re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % alt, re.IGNORECASE)
    return lambda s: rx.sub("{provider}", s)


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def const_strs(node):
    if isinstance(node, (ast.List, ast.Tuple)):
        return [s for s in (const_str(e) for e in node.elts) if s]
    s = const_str(node)
    return [s] if s else []


def call_name(node):
    f = node.func if isinstance(node, ast.Call) else node
    parts = []
    while isinstance(f, ast.Attribute):
        parts.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        parts.append(f.id)
    return ".".join(reversed(parts))


def parse_python(root, module):
    """Classes with their model identity, fields, methods and routes."""
    out = []
    for path in sorted((root / module).rglob("*.py")):
        if SKIP_DIRS & set(path.relative_to(root / module).parts[:-1]):
            continue
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError as exc:
            print("skip %s: %s" % (path, exc), file=sys.stderr)
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            info = {"class": cls.name, "file": rel(path, root), "line": cls.lineno,
                    "name": None, "inherit": [], "fields": [], "methods": [], "routes": [],
                    "bases": [call_name(b) for b in cls.bases]}
            for stmt in cls.body:
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
                    tgt, val = stmt.targets[0].id, stmt.value
                    if tgt == "_name":
                        info["name"] = const_str(val)
                    elif tgt == "_inherit":
                        info["inherit"] = const_strs(val)
                    elif isinstance(val, ast.Call) and call_name(val).startswith("fields."):
                        kw = {k.arg: k.value for k in val.keywords if k.arg}
                        sel_add = []
                        if "selection_add" in kw and isinstance(kw["selection_add"], (ast.List, ast.Tuple)):
                            for e in kw["selection_add"].elts:
                                if isinstance(e, (ast.Tuple, ast.List)) and e.elts:
                                    sel_add.append(const_str(e.elts[0]))
                        info["fields"].append({"name": tgt, "type": call_name(val).split(".", 1)[1],
                                               "line": stmt.lineno, "selection_add": [s for s in sel_add if s],
                                               "string": const_str(kw.get("string")) if kw.get("string") else None})
                elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    routes = []
                    for dec in stmt.decorator_list:
                        if isinstance(dec, ast.Call) and call_name(dec).split(".")[-1] == "route":
                            paths = const_strs(dec.args[0]) if dec.args else []
                            kw = {k.arg: k.value for k in dec.keywords if k.arg}
                            if not paths and "route" in kw:
                                paths = const_strs(kw["route"])
                            routes.append({"paths": paths,
                                           "type": const_str(kw.get("type")) if kw.get("type") else None,
                                           "auth": const_str(kw.get("auth")) if kw.get("auth") else None,
                                           "methods": const_strs(kw["methods"]) if "methods" in kw else []})
                    args = [a.arg for a in stmt.args.args if a.arg not in ("self", "cls")]
                    if stmt.args.vararg:
                        args.append("*" + stmt.args.vararg.arg)
                    if stmt.args.kwarg:
                        args.append("**" + stmt.args.kwarg.arg)
                    info["methods"].append({"name": stmt.name, "line": stmt.lineno, "args": args})
                    for r in routes:
                        r.update(func=stmt.name, line=stmt.lineno)
                        info["routes"].append(r)
            if info["name"] or info["inherit"] or info["routes"]:
                out.append(info)
    return out


def parse_js(root, module):
    regs, patches = [], []
    static = root / module / "static" / "src"
    if not static.exists():
        return regs, patches
    for path in sorted(static.rglob("*.js")):
        if "tests" in path.parts:
            continue
        text = path.read_text(errors="replace")
        for m in REGISTRY_RE.finditer(text):
            regs.append({"category": m.group(1), "key": m.group(2), "file": rel(path, root),
                         "line": text.count("\n", 0, m.start()) + 1})
        for m in PATCH_RE.finditer(text):
            patches.append({"symbol": m.group(1), "file": rel(path, root),
                            "line": text.count("\n", 0, m.start()) + 1})
    return regs, patches


def model_owner_index(parsed):
    """model name -> module that defines it (a _name not listed in its own _inherit)."""
    owner = {}
    for module, classes in parsed.items():
        for c in classes:
            if c["name"] and c["name"] not in c["inherit"]:
                owner.setdefault(c["name"], module)
    return owner


def extension_points(module, classes, regs, patches, man, owner, norm):
    """Every point this module plugs into, keyed by normalised slot id, with file:line evidence."""
    pts = defaultdict(list)

    def add(key, kind, label, ref, detail=None):
        pts[key].append({"kind": kind, "label": label, "ref": ref, "detail": detail or {}})

    for c in classes:
        ref = "%s:%d" % (c["file"], c["line"])
        defines = c["name"] and c["name"] not in c["inherit"]
        targets = [c["name"]] if defines else (c["inherit"] if c["inherit"] else [])
        if defines:
            nm = norm(c["name"])
            if "{provider}" in nm:
                add("define:" + nm, "provider_model", nm, ref, {"model": c["name"], "mixins": c["inherit"],
                                                                "fields": ["%s: %s" % (norm(f["name"]), f["type"]) for f in c["fields"]]})
            else:
                continue  # a module-private model is not an extension point
        else:
            own = [t for t in targets if owner.get(t) not in PROVIDERS or norm(t) != t]
            if not own:
                continue
            for t in c["inherit"]:
                add("inherit:" + norm(t), "inherit", norm(t), ref, {"model": t, "class": c["class"]})
        tgt = norm(targets[0]) if targets else None
        if tgt is None:
            continue
        for m in c["methods"]:
            if m["name"] in SKIP_METHODS:
                continue
            add("method:%s.%s" % (tgt, norm(m["name"])), "method", "%s.%s()" % (tgt, norm(m["name"])),
                "%s:%d" % (c["file"], m["line"]), {"signature": "%s(%s)" % (m["name"], ", ".join(m["args"]))})
        for f in c["fields"]:
            if f["selection_add"]:
                add("selection:%s.%s" % (tgt, f["name"]), "selection_add", "%s.%s += %s" % (tgt, f["name"], "/".join(f["selection_add"])),
                    "%s:%d" % (c["file"], f["line"]), {"values": f["selection_add"]})
            elif tgt in SETTINGS_TARGETS:
                add("field:%s.%s" % (tgt, norm(f["name"])), "settings_field",
                    "%s.%s" % (tgt, norm(f["name"])), "%s:%d" % (c["file"], f["line"]),
                    {"type": f["type"], "name": f["name"], "string": f["string"]})
    for c in classes:
        for r in c["routes"]:
            for p in r["paths"]:
                np = norm(p)
                kind = "webhook" if r["auth"] in ("public", "none") else "route"
                add("route:" + np, kind, np, "%s:%d" % (c["file"], r["line"]),
                    {"path": p, "type": r["type"], "auth": r["auth"], "methods": r["methods"], "func": r["func"]})
    for bundle, items in ((man or {}).get("assets") or {}).items():
        add("asset:" + bundle, "asset", bundle, "%s/__manifest__.py" % module, {"entries": len(items)})
    for r in regs:
        add("js:%s/%s" % (r["category"], norm(r["key"])), "js_registry", "%s / %s" % (r["category"], norm(r["key"])),
            "%s:%d" % (r["file"], r["line"]), {"key": r["key"]})
    for p in patches:
        add("patch:" + p["symbol"], "js_patch", "patch " + p["symbol"], "%s:%d" % (p["file"], p["line"]), {})
    return pts


def core_keyed_registrations(regs):
    """Core registrations into a connect.* registry keyed by a provider name count as that provider's."""
    out = defaultdict(list)
    for r in regs:
        if r["category"].startswith("connect."):
            out[r["key"]].append(r)
    return out


def core_points(classes, regs, owner):
    """Slot keys the core itself declares: models, methods, selection fields, connect.* registries."""
    decl = {}
    for c in classes:
        targets = [c["name"]] if c["name"] and c["name"] not in c["inherit"] else c["inherit"]
        if not targets:
            continue
        t = targets[0]
        decl.setdefault("inherit:" + t, "%s:%d" % (c["file"], c["line"]))
        for m in c["methods"]:
            decl.setdefault("method:%s.%s" % (t, m["name"]), "%s:%d" % (c["file"], m["line"]))
        for f in c["fields"]:
            if f["type"] == "Selection":
                decl.setdefault("selection:%s.%s" % (t, f["name"]), "%s:%d" % (c["file"], f["line"]))
    for r in regs:
        if r["category"].startswith("connect."):
            decl.setdefault("registry:" + r["category"], "%s:%d" % (r["file"], r["line"]))
    return decl


def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=str(HERE.parent), help="connect_addons checkout")
    ap.add_argument("--out", default=str(DATA / "graph.json"))
    args = ap.parse_args()
    root = Path(args.root).resolve()

    modules = [CORE] + PROVIDERS + ([TARGET] if (root / TARGET / "__manifest__.py").exists() else [])
    mans = {m: manifest(root, m) for m in modules}
    parsed = {m: parse_python(root, m) for m in modules}
    js = {m: parse_js(root, m) for m in modules}
    owner = model_owner_index(parsed)

    per_module = {}
    for m in modules[1:]:
        deps = (mans[m] or {}).get("depends", [])
        norm = normaliser(tokens_for(m, deps))
        per_module[m] = extension_points(m, parsed[m], js[m][0], js[m][1], mans[m], owner, norm)
    families = {m: (sorted(set(tokens_for(m, (mans[m] or {}).get("depends", []))) - {m[8:]}) or [m[8:]])[0]
                for m in PROVIDERS}

    # core registrations into connect.* registries, keyed by provider name, belong to that provider
    for key, regs in core_keyed_registrations(js[CORE][0]).items():
        prov = "connect_" + key.split("_")[0]
        if prov not in PROVIDERS:
            continue
        nkey = normaliser([prov[len("connect_"):]])(key)
        for r in regs:
            per_module[prov]["js:%s/%s" % (r["category"], nkey)].append(
                {"kind": "js_registry", "label": "%s / %s" % (r["category"], nkey),
                 "ref": "%s:%d" % (r["file"], r["line"]), "detail": {"key": key, "in_core": True}})

    core_decl = core_points(parsed[CORE], js[CORE][0], owner)

    slots = {}
    for key in sorted({k for m in PROVIDERS for k in per_module[m]}):
        implementers = [m for m in PROVIDERS if key in per_module[m]]
        if len(implementers) < 2:
            continue
        sample = per_module[implementers[0]][key][0]
        core_ref = core_decl.get(key.replace("inherit:", "inherit:", 1))
        if key.startswith("js:"):
            core_ref = core_decl.get("registry:" + key[3:].split("/")[0])
        slots[key] = {
            "id": key, "kind": sample["kind"], "label": sample["label"],
            "implementers": implementers,
            "families": sorted({families[m] for m in implementers}),
            "core_ref": core_ref,
            "refs": {m: [p["ref"] for p in per_module[m][key]] for m in implementers},
            "details": {m: per_module[m][key][0]["detail"] for m in implementers},
        }

    catalog = load_json(DATA / "vi_catalog.json", {"objects": [], "methods": [], "apidaze": []})
    mapping = load_json(DATA / "vi_mapping.json", {"slots": {}})
    vi_caps = {}
    for o in catalog.get("objects", []):
        vi_caps.setdefault("obj:" + o["name"], dict(o, layer=1, type="vi_object"))
    for mth in catalog.get("methods", []):
        vi_caps["vim:" + mth["name"]] = dict(mth, layer=2, type="vi_method", availability=mth.get("availability", "documented"))
    for cap in catalog.get("apidaze", []):
        vi_caps["apz:" + cap["name"]] = dict(cap, layer=2, type="apidaze", availability=cap.get("availability", "unverified"))

    def cap_id(name):
        for pre in ("vim:", "apz:"):
            if pre + name in vi_caps:
                return pre + name
        return None

    has_target = TARGET in per_module
    nodes, edges = [], []
    for cid, c in vi_caps.items():
        nodes.append({"id": cid, "layer": c["layer"], "type": c["type"], "label": c["name"],
                      "group": c.get("group") or c.get("kind"),
                      "availability": c.get("availability"),
                      "fields": c.get("fields"), "signature": c.get("signature"),
                      "description": c.get("description"), "provenance": c.get("provenance"),
                      "inferred": c.get("inferred", False)})
    for cid, c in vi_caps.items():
        if c["layer"] != 2:
            continue
        for rel_kind in ("reads", "writes"):
            for obj in c.get(rel_kind) or []:
                if "obj:" + obj in vi_caps:
                    edges.append({"source": "obj:" + obj, "target": cid, "kind": rel_kind})

    unknown_caps = set()
    totals = defaultdict(int)
    for key, s in slots.items():
        mp = mapping.get("slots", {}).get(key, {})
        mapped = []
        for name in mp.get("vi", []):
            cid = cap_id(name)
            if cid:
                mapped.append(cid)
            else:
                unknown_caps.add(name)
        if has_target and key in per_module[TARGET]:
            status = "implemented"
        elif any(vi_caps[c].get("availability") == "unverified" for c in mapped):
            status = "blocked"
        elif mapped or mp.get("internal"):
            status = "missing"
        else:
            status = "unmapped"
        totals[status] += 1
        nodes.append({"id": "slot:" + key, "layer": 3, "type": "slot", "label": s["label"], "kind": s["kind"],
                      "implementers": s["implementers"], "families": s["families"], "core_ref": s["core_ref"],
                      "refs": s["refs"], "details": s["details"], "status": status})
        for impl in s["implementers"]:
            iid = "impl:%s:%s" % (impl, key)
            nodes.append({"id": iid, "layer": 4, "type": "provider_impl", "label": "%s: %s" % (impl[8:], s["label"]),
                          "module": impl, "slot": key, "refs": s["refs"][impl], "detail": s["details"][impl]})
            edges.append({"source": "slot:" + key, "target": iid, "kind": "implemented_by"})
            edges.append({"source": iid, "target": "mod:" + key, "kind": "precedent"})
        nodes.append({"id": "mod:" + key, "layer": 5, "type": "module_slot", "label": s["label"], "kind": s["kind"],
                      "status": status, "note": mp.get("note") or mp.get("internal"),
                      "refs": [p["ref"] for p in per_module[TARGET][key]] if has_target and key in per_module[TARGET] else []})
        edges.append({"source": "slot:" + key, "target": "mod:" + key, "kind": "fills"})
        for cid in mapped:
            edges.append({"source": cid, "target": "slot:" + key, "kind": "maps_to",
                          "blocked": vi_caps[cid].get("availability") == "unverified"})

    stale = sorted(set(mapping.get("slots", {})) - set(slots))
    by_kind = defaultdict(int)
    for s in slots.values():
        by_kind[s["kind"]] += 1
    graph = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "connect_addons_sha": git_sha(root),
        "worktree_dirty": git_dirty(root, modules),
        "generator": "scripts/provider_graph.py",
        "modules": {m: {"version": (mans.get(m) or {}).get("version"), "present": mans.get(m) is not None,
                        "family": families.get(m)} for m in modules + ([] if has_target else [TARGET])},
        "layers": [
            {"n": 1, "name": "VI data objects", "source": "vi_catalog.json"},
            {"n": 2, "name": "VI API methods and Apidaze capabilities", "source": "vi_catalog.json"},
            {"n": 3, "name": "Interface slots", "source": "computed from code"},
            {"n": 4, "name": "Provider implementations", "source": "computed from code"},
            {"n": 5, "name": TARGET, "source": "computed from code + vi_mapping.json"},
        ],
        "catalog_meta": {k: catalog.get(k) for k in ("seen", "sources", "transport")},
        "nodes": nodes,
        "edges": edges,
        "totals": {"by_status": {k: totals.get(k, 0) for k in ("implemented", "missing", "unmapped", "blocked")},
                   "slots": len(slots), "slots_by_kind": dict(sorted(by_kind.items())),
                   "extension_points": {m: len(per_module[m]) for m in per_module},
                   "vi_objects": sum(1 for c in vi_caps.values() if c["layer"] == 1),
                   "vi_methods": sum(1 for c in vi_caps.values() if c["type"] == "vi_method"),
                   "apidaze": sum(1 for c in vi_caps.values() if c["type"] == "apidaze"),
                   "remaining": sum(totals.get(k, 0) for k in ("missing", "unmapped", "blocked"))},
        "mapping_warnings": {"unknown_capabilities": sorted(unknown_caps), "stale_slot_keys": stale},
    }
    Path(args.out).write_text(json.dumps(graph, indent=1, sort_keys=False) + "\n")
    t = graph["totals"]
    print("%s  sha=%s  slots=%d  %s  remaining=%d" % (graph["generated_at"], (graph["connect_addons_sha"] or "?")[:8],
                                                     t["slots"], json.dumps(t["by_status"]), t["remaining"]))
    if unknown_caps or stale:
        print("mapping warnings: %s" % json.dumps(graph["mapping_warnings"]), file=sys.stderr)


if __name__ == "__main__":
    main()
