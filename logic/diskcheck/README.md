# diskcheck

This folder owns disk visibility checks before full-PC search.

It answers whether expected roots such as `C:\` and `D:\` are:

1. visible to normal filesystem access;
2. reported by Windows as logical drives;
3. fixed local drives that the search engine should include by default.

## Run

Check the default C/D contract:

```powershell
python logic\diskcheck\diskcheck.py --json
```

Check specific roots:

```powershell
python logic\diskcheck\diskcheck.py C:\ D:\ E:\ --json
```

The report distinguishes a missing drive from a visible drive that is not a
fixed local disk, such as removable, network, or CD-ROM drives.
