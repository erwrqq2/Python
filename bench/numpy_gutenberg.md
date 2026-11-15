| Query | Postings scored | Docs matched | Lab 3 scorer, ms | NumPy scorer, ms | Speedup |
|---|---|---|---|---|---|
| `the` | 143 | 143 | 0.104 | 0.031 | **3.3×** |
| `river` | 107 | 107 | 0.088 | 0.030 | **2.9×** |
| `locomotive` | 16 | 16 | 0.020 | 0.028 | **0.7×** |
| `river ship captain` | 271 | 56 | 0.121 | 0.061 | **2.0×** |
| `river OR ship OR captain` | 271 | 124 | 0.180 | 0.062 | **2.9×** |
