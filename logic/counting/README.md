# counting

This folder owns file and folder counting logic for the local PC.

By default, full-drive scans exclude Windows setup/system folders such as
`C:\Windows`, `C:\Program Files`, `C:\ProgramData`, `C:\Recovery`, and
`C:\System Volume Information`. The reported `directories_counted` value is the
number of folders counted after those Windows setup folders are excluded.

## Run

Count files in a specific folder. Normal output is a single number only:

```powershell
python logic\counting\counter.py "."
```

Count folders instead of files:

```powershell
python logic\counting\counter.py --count directories "."
```

Count local fixed drives while excluding Windows setup folders:

```powershell
python logic\counting\counter.py
```

Include Windows setup folders when an audit explicitly needs them:

```powershell
python logic\counting\counter.py --include-windows-setup
```

Access failures are recorded in `errors` instead of stopping the whole scan.
Use `--json` only when detailed audit data is needed.
