"""Build the packaged, attributed AUSNUT seed from the official XLSX (standard library only)."""

import gzip
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

DATA = Path(__file__).resolve().parents[1] / "app" / "data"
SOURCE = "https://www.foodstandards.gov.au/sites/default/files/2025-09/AUSNUT%202023%20-%20Food%20nutrient%20profiles.xlsx?v=20250903"


def convert(path):
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    selected = {r["id"]: r for r in json.loads((DATA / "foods.json").read_text())}
    with ZipFile(path) as archive:
        strings = [
            "".join(x.itertext())
            for x in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("m:si", ns)
        ]
        rows = ET.fromstring(archive.read("xl/worksheets/sheet2.xml")).findall(".//m:row", ns)
        result = []
        for row in rows[3:]:
            cells = {}
            for c in row:
                value = c.find("m:v", ns)
                if value is not None:
                    cells[re.sub(r"\d", "", c.attrib["r"])] = (
                        strings[int(value.text)] if c.attrib.get("t") == "s" else value.text
                    )
            if not cells.get("B", "").startswith("F"):
                continue
            nutrients = [Decimal(cells[col]) for col in ("E", "H", "K", "I")]
            if any(not v.is_finite() or v < 0 for v in nutrients):
                raise ValueError("Invalid nutrient data: " + cells["B"])
            entry = dict(
                id=cells["B"],
                name=cells["D"],
                aliases=[],
                preparation_state=cells["D"],
                energy_kcal_per_100g=str((nutrients[0] / Decimal("4.184")).quantize(Decimal(".000001"))),
                protein_g_per_100g=str(nutrients[1]),
                carbohydrate_g_per_100g=str(nutrients[2]),
                fat_g_per_100g=str(nutrients[3]),
                allergens=[],
                dietary_tags=[],
                dietary_status_known=False,
                source_url=SOURCE,
                catalogue_version="ausnut-2023-full-v2",
                origin="Imported",
            )
            # Only the original explicitly annotated foods have known dietary metadata.
            if entry["id"] in selected:
                entry.update({k: selected[entry["id"]][k] for k in ("allergens", "dietary_tags")})
                entry["dietary_status_known"] = True
            result.append(entry)
    assert len(result) == len({r["id"] for r in result}) == 3741
    (DATA / "foods.seed.json.gz").write_bytes(
        gzip.compress(json.dumps(result, ensure_ascii=False).encode(), mtime=0)
    )
    (DATA / "foods.seed.manifest.json").write_text(
        json.dumps(
            dict(
                record_count=len(result),
                version="ausnut-2023-full-v2",
                source_url=SOURCE,
                source_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                attribution="Food Standards Australia New Zealand, AUSNUT 2023",
                license="CC BY 4.0",
                basis="Per 100 g edible portion in named state; kJ divided by 4.184. Available carbohydrate excludes sugar alcohols.",
                limitation="Most entries have unknown allergen/dietary status; original ten retain application annotations.",
            ),
            indent=2,
        )
        + "\n"
    )
    print("Converted", len(result), "foods")


if __name__ == "__main__":
    convert(sys.argv[1])
