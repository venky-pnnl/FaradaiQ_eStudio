"""FaradaIQ Studio v307 resilient VersaStudio folder loading.

Folder-level behavior is intentionally conservative:
- exact/case-insensitive .par -> .psbd/.par.psbd pairing remains authoritative;
- orphan .par method files are skipped with explicit provenance rather than
  aborting every other valid experiment in the folder;
- fuzzy filename matching is never used to attach a binary file to a method;
- a single-file .par load remains fail-fast when its binary companion is absent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, MutableMapping


def install(core: MutableMapping[str, Any]) -> None:
    Collection = core["PSSessionCollection"]
    HEKA_SUPPORT_EXTS = core["HEKA_SUPPORT_EXTS"]
    HEKAPotmasterParser = core["HEKAPotmasterParser"]
    VersaStudioParser = core["VersaStudioParser"]
    SolartronParser = core["SolartronParser"]
    BioLogicParser = core["BioLogicParser"]
    PSSessionParser = core["PSSessionParser"]

    if getattr(Collection, "_v307_versastudio_resilience_installed", False):
        return

    _old_load = Collection.load
    _old_summarize = Collection.summarize
    _old_bundle = Collection.build_parsed_npy_bundle

    def _load(self, path: str):
        self.load_warnings = []
        self.skipped_entries = []
        return _old_load(self, path)

    def _warn_skip(self, root: Path, p: Path, reason: str, detail: str = "") -> None:
        try:
            rel = str(p.relative_to(root)).replace("\\", "/")
        except Exception:
            rel = p.name
        item = {
            "kind": "versastudio_skipped_entry",
            "relative_path": rel,
            "reason": reason,
            "detail": detail,
        }
        self.skipped_entries.append(item)
        self.load_warnings.append(
            f"VersaStudio skipped {rel}: {reason}. Valid paired experiments continue loading."
        )
        print(f"[WARN] VersaStudio skipped {rel}: {reason}. Valid paired experiments continue loading.")

    def _load_directory(self, dir_path: str):
        root = Path(dir_path)
        paths = sorted([
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in {
                ".pssession", ".dat", ".pul", ".pgf", ".amp", ".mrk", ".mth", ".onl",
                ".par", ".cor", ".z", ".mpr", ".mps", ".mgr"
            }
        ])
        if not paths:
            raise RuntimeError(
                "No .pssession, HEKA .dat/.pul support files, VersaStudio .par, "
                "Solartron .cor/.z, or BioLogic .mpr/.mps/.mgr files found inside the selected folder."
            )

        selected = []
        seen_heka = set()
        seen_bio = set()
        for p in paths:
            suf = p.suffix.lower()
            if suf in HEKA_SUPPORT_EXTS and HEKAPotmasterParser.can_open_path(p):
                key = (str(p.parent), p.with_suffix("").name)
                if key in seen_heka:
                    continue
                seen_heka.add(key)
                group = HEKAPotmasterParser._siblings_for(p)
                chosen = group.get(".dat") or group.get(".pul") or p
                selected.append(chosen)
            elif suf in {".mpr", ".mps", ".mgr"}:
                key = (str(p.parent), BioLogicParser._norm_key(p.name))
                if key in seen_bio:
                    continue
                seen_bio.add(key)
                group = BioLogicParser._pair_from_directory(str(p))
                chosen = group.get("mpr") or group.get("mps") or group.get("mgr")
                if chosen is not None:
                    selected.append(chosen)
            elif suf == ".par":
                # Never fuzzy-pair a VersaStudio method file. If its exact
                # binary companion is missing, keep processing other complete pairs.
                try:
                    VersaStudioParser._resolve_psbd_path(p)
                except Exception as exc:
                    _warn_skip(
                        self, root, p,
                        "no unambiguous .psbd/.par.psbd binary companion",
                        str(exc),
                    )
                    continue
                selected.append(p)
            else:
                selected.append(p)

        for p in selected:
            if p.suffix.lower() in HEKA_SUPPORT_EXTS and HEKAPotmasterParser.can_open_path(p):
                parser = HEKAPotmasterParser()
            elif p.suffix.lower() == ".par":
                parser = VersaStudioParser()
            elif p.suffix.lower() in {".cor", ".z"}:
                parser = SolartronParser()
            elif p.suffix.lower() in {".mpr", ".mps", ".mgr"}:
                parser = BioLogicParser()
            else:
                parser = PSSessionParser()

            try:
                parser.load(str(p))
            except Exception as exc:
                # A malformed VersaStudio pair must not destroy a large mixed
                # folder import. Other parser families retain prior fail-fast behavior.
                if p.suffix.lower() == ".par":
                    _warn_skip(self, root, p, "paired VersaStudio data could not be decoded", str(exc))
                    continue
                raise

            rel_name = str(p.relative_to(root)).replace("\\", "/")
            self.entries.append({
                "entry_name": rel_name,
                "virtual_source": str(p),
                "parser": parser,
            })

        if not self.entries:
            skipped = len(getattr(self, "skipped_entries", []))
            if skipped:
                raise RuntimeError(
                    f"No loadable experiments remained after inspection. {skipped} VersaStudio .par file(s) "
                    "were skipped because a safe binary companion could not be established or decoded."
                )
            raise RuntimeError("No loadable experiment entries were found inside the selected folder.")

        if getattr(self, "skipped_entries", None):
            print(
                f"[WARN] VersaStudio folder import continued with {len(self.entries)} loadable experiment(s); "
                f"{len(self.skipped_entries)} incomplete/unsafe .par entr{'y' if len(self.skipped_entries)==1 else 'ies'} skipped."
            )

    def _summarize(self):
        result = _old_summarize(self)
        if getattr(self, "load_warnings", None):
            result["load_warnings"] = list(self.load_warnings)
            result["skipped_entries"] = list(self.skipped_entries)
            result["n_skipped_entries"] = len(self.skipped_entries)
        return result

    def _bundle(self, experimental_description: str):
        result = _old_bundle(self, experimental_description)
        if getattr(self, "load_warnings", None):
            summary = result.setdefault("collection_summary", {})
            summary["load_warnings"] = list(self.load_warnings)
            summary["skipped_entries"] = list(self.skipped_entries)
            summary["n_skipped_entries"] = len(self.skipped_entries)
        return result

    Collection.load = _load
    Collection._load_directory = _load_directory
    Collection.summarize = _summarize
    Collection.build_parsed_npy_bundle = _bundle
    Collection._v307_versastudio_resilience_installed = True
