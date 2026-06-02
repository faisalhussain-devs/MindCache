import pandas as pd
import json
from pathlib import Path

path = Path(r"E:\MindCache\BEAM\1M-00000-of-00001.parquet")
output_path = Path(r"E:\MindCache\BEAM\beam_data2.json")

df = pd.read_parquet(path)

# Convert to a list of dicts (one per row) for clean JSON output
records = df.to_dict(orient="records")[16]

# Write to JSON with indentation for human readability
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(records, f, indent=3, ensure_ascii=False, default=str)

print(f"Wrote {len(records)} records to {output_path}")
print(f"Columns: {list(df.columns)}")
