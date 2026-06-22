# search

This folder owns indexed file search for the local PC.

The search flow is two-step:

1. Build an index by walking accessible files once.
2. Search the saved index through hash tables for file names, extensions,
   path tokens, and path/name n-grams.

When a selected root is the `PC 전체 접근 권한` workspace folder, indexing expands
to that folder's parent first. This makes the first index pass include sibling
files in the upper folder instead of only files inside this implementation
workspace.

## Run

Create the first index immediately after the folder is installed. This command
is parent-aware, so an installed `PC 전체 접근 권한` folder indexes its upper
folder first:

```powershell
python logic\search\searcher.py install "C:\Users\me\Desktop\PC 전체 접근 권한" --index pc-search-index.json
```

Build an index for a specific root:

```powershell
python logic\search\searcher.py index "." --output search-index.json
```

Search a saved index:

```powershell
python logic\search\searcher.py find "report" --index search-index.json
```

When a saved index is stale, `find --index` refreshes it before searching unless
`--no-auto-refresh` is supplied.

Search by extension:

```powershell
python logic\search\searcher.py find ".pdf" --mode extension --index search-index.json
```

Build an index for local fixed drives when no root is supplied:

```powershell
python logic\search\searcher.py index --output pc-search-index.json
```

Keep the index current while files change:

```powershell
python logic\search\searcher.py watch --index pc-search-index.json --interval 5
```

Re-check unresolved access errors in a saved index:

```powershell
python logic\search\searcher.py verify-errors --index pc-search-index.json --update --json
```

Access failures are recorded in `errors` instead of stopping the scan. The index
builder verifies those errors a second time and stores only the unresolved
errors plus recovery counts. Directory links are not followed, which prevents
cycles during full-PC indexing.
