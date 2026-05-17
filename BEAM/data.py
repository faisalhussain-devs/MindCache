import pandas as pd
from pathlib import Path
path = Path(r"E:\MindCache\BEAM\1M-00000-of-00001.parquet")
df = pd.read_parquet(path)
print(df)
