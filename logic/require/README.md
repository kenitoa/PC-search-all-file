# require

This folder owns the requirement-facing search engine for the upper search
interface.

The runtime order is fixed:

1. `require`: validate and normalize the interface request.
2. `counting`: count the same resolved roots that will be searched.
3. `search`: build, refresh, or load the index and search it.

The lower `logic/search` module still owns index construction and lookup. This
folder owns the interface contract and the required execution order.

## Run

Search a folder through the full requirement flow:

```powershell
python logic\require\engine.py "report" "." --json
```

Search with a saved index:

```powershell
python logic\require\engine.py "report" "." --index search-index.json
```

When no root is supplied but the index exists, the count step uses the roots
stored in that index before search refreshes or loads it.
