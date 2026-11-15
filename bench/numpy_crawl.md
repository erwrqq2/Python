| Query | Postings scored | Docs matched | Lab 3 scorer, ms | NumPy scorer, ms | Speedup |
|---|---|---|---|---|---|
| `book` | 111 | 111 | 0.091 | 0.029 | **3.2×** |
| `the` | 464 | 464 | 0.333 | 0.036 | **9.3×** |
| `travel` | 20 | 20 | 0.024 | 0.027 | **0.9×** |
| `a OR the OR and` | 1,351 | 473 | 0.853 | 0.075 | **11.4×** |
