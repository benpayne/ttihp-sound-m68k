# SPDX-FileCopyrightText: © 2026 Ben Payne
# SPDX-License-Identifier: Apache-2.0
"""Shared functional-coverage export helper (constitution Principle II).

`cocotb_coverage.coverage.CoverageDB` is a process-wide singleton (its own
docstring calls it out as one): every test module that does
`from cocotb_coverage.coverage import coverage_db` shares the literal same
dict, keyed by coverpoint name, for the lifetime of the Python process.
Its own `CoverageDB.export_to_yaml()` has no scoping parameter -- it
iterates every key ever registered in the whole process and writes all of
them, unconditionally (see `cocotb_coverage/coverage.py`'s
`export_to_yaml`). `CoverPoint`/`CoverCross` decorators register their
coverpoint in the singleton at *decoration* time (i.e. module import),
before any test has run, so a module imported early but not yet exercised
still shows up -- at 0% -- in another module's export.

That's invisible when each test module happens to run alone in its own
process (the only case this suite exercised until now): only that
module's own coverpoints exist in the db at all, so an unscoped export
looks correctly scoped by coincidence. It stops being invisible the
moment more than one coverage-bearing suite shares a process (e.g. a
combined run across all of this project's test modules): each module's
"own" exported YAML would then also contain every other coverage-bearing
module's keys, with modules that haven't run yet in that process showing
up as spurious 0%-covered stubs rather than a real gap.

`export_scoped_yaml` fixes this by filtering the shared singleton to just
one coverpoint namespace before writing -- it never mutates or clears the
shared db, only reads it, so it is safe to call from multiple modules
sharing one process. Reusable by any project on this bench (this file
lives in `tbutil/` specifically so ttihp-spi-m68k and similar sibling
projects can pick it up too).
"""

from typing import Any, Dict

import yaml
from cocotb_coverage.coverage import CoverItem, coverage_db


def _belongs_to(name: str, prefix: str) -> bool:
    """True if `name` is `prefix` itself or a dotted descendant of it.

    Deliberately not a bare `str.startswith(prefix)`: that would also
    match an unrelated coverpoint whose name merely starts with the same
    characters (e.g. prefix "i2s" matching a hypothetical "i2something").
    """
    return name == prefix or name.startswith(prefix + ".")


def export_scoped_yaml(prefix: str, filename: str) -> None:
    """Write only the `coverage_db` entries under `prefix` to `filename`.

    `prefix` is a coverpoint namespace root with no trailing dot (e.g.
    "i2s", "psg", "spdif") -- every coverpoint/covergroup name equal to
    `prefix` or starting with `prefix + "."` is included; everything else
    in the shared singleton is left out. Output format matches
    `CoverageDB.export_to_yaml()` exactly (a top-level dict of per-item
    attribute dicts, sorted by name, written with `yaml.dump`), so
    existing consumers of these files see no format change -- only a
    narrower key set.
    """
    export_data: Dict[str, Any] = {}
    for name in sorted(coverage_db, key=str.lower):
        if not _belongs_to(name, prefix):
            continue

        item = coverage_db[name]
        attrib_dict: Dict[str, Any] = {
            "type": str(type(item)),
            "size": item.size,
            "coverage": item.coverage,
            "cover_percentage": round(item.cover_percentage, 2),
        }

        if type(item) is not CoverItem:
            attrib_dict["weight"] = item.weight
            attrib_dict["at_least"] = item.at_least

            bins = []
            hits = []
            for key, value in item.detailed_coverage.items():
                if hasattr(key, "__iter__"):  # convert iterables to string
                    key = str(key)
                bins.append(key)
                hits.append(value)
            attrib_dict["bins:_hits"] = dict(zip(bins, hits))

        export_data[name] = attrib_dict

    with open(filename, "w") as outfile:
        yaml.dump(export_data, outfile, default_flow_style=False)
