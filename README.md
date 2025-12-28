# DeckMaster – interactive due-date triage for Taskwarrior

```bash
curl -LO https://raw.githubusercontent.com/catanadj/taskwarrior-deckmaster/main/DeckMaster.py
chmod +x DeckMaster.py
```

## What it does
- Shows **today / yesterday / overdue** tasks **one-by-one** in rich panels  
- Single-key actions:  
  `1-9`  →  +N days  
  `0`    →  today  
  `c`    →  mark done (with optional annotation)  
  `d`    →  delete  
  `b`    →  bulk-checkbox mode (multi-select + mass-action)  
  `r`    →  refresh (newly added tasks appear at the end)  
- **Smart re-schedule**: tasks older than yesterday are bumped from *today*, not from their stale due-date  
- Time-zone aware, emoji age-indicators, optional duration / value / CP fields  
- Dry-run feel: nothing is executed until you confirm; Ctrl-C leaves your DB untouched  

## Usage
```bash
./deckmaster.py              # today (default)
./deckmaster.py overdue      # blaze through backlog (short use "o")
./deckmaster.py yesterday -b # batch-process what slipped (short "y")
```

## Keys
| Key | Action |
|-----|--------|
| `1-9` | postpone N days |
| `0` | set due today |
| `c [note]` | complete (with annotation) |
| `d` | delete |
| `b` | batch multi-select |
| `r` | refresh queue |
| `s` | skip |
| `q` | quit |

## Install
Python ≥ 3.8  
```bash
pip install rich dateutil pytz      # required
pip install questionary             # optional (nicer batch UI)
```
---

## Support

If you find this tool helpful, any support will be greatly apreciated.

You can do so [here](https://buymeacoffee.com/catanadj). Thank you.

---
