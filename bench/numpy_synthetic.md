Synthetic corpus: 20,000 docs x 150 words, Zipf vocabulary of 2,000 words

| Query | Postings scored | Docs matched | Lab 3 scorer, ms | NumPy scorer, ms | Speedup |
|---|---|---|---|---|---|
| `w0` | 20,000 | 20,000 | 15.52 | 0.26 | **60.4×** |
| `w5` | 19,066 | 19,066 | 15.00 | 0.26 | **57.4×** |
| `w500` | 719 | 719 | 0.54 | 0.04 | **13.6×** |
| `w0 OR w1 OR w2` | 59,956 | 20,000 | 43.89 | 0.67 | **65.4×** |
