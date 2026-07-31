# GkeepAlexa

Bidirectional synchronization between Google Keep checklists and Amazon Alexa lists, written in Python.

> **Disclaimer:** All product and company names or logos are trademarks™ or registered® trademarks of their respective holders. Use of them does not imply any affiliation with or endorsement by them or any associated subsidiaries. This is a personal project maintained in spare time and has no business goal. GOOGLE KEEP is a trademark of Google LLC. ALEXA is a trademark of AMAZON TECHNOLOGIES, INC.

This project is licensed under the [MIT License](LICENSE).

## Demo

<!-- demo GIF or video goes here -->

## Installation

Requires **Python 3.10 or later** (`pyalexalist` requires 3.10+; `tomli` is installed automatically on 3.10 as a backfill for `tomllib`).

Install the required Python packages before running anything.

**Without a virtual environment:**

```
pip install -r requirements.txt
```

**With a virtual environment (recommended):**

Using a venv keeps dependencies isolated from your system Python installation.

Linux & macOS:
```
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
deactivate
```

Windows:
```
if not exist .venv py -m venv .venv
.\.venv\Scripts\activate.bat
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
deactivate
```

## Prerequisites

### 1. Google Keep credentials

Google Keep access requires a long-lived master token tied to your Google account. See the gkeepapi docs for full details: [Obtaining a Master Token](https://gkeepapi.readthedocs.io/en/latest/#obtaining-a-master-token).

**Step 1 — Create `config/service_auth.json`** using `config/service_auth.json.example` as a reference. Fill in your Google username and OAuth token; leave `alexa.refresh_token` empty for now.

Obtain an OAuth token for your Google account (the gkeepapi docs above cover several methods), then paste it into `google.oauth_token`.

**Step 2 — Exchange it for a master token:**

```
python scripts/get_gkeep_master_token.py
```

This reads `google.oauth_token` from `config/service_auth.json`, exchanges it for a master token via `gpsoauth`, and writes `google.username` + `google.master_token` into `config/runtime_credentials.json`.

---

### 2. Alexa credentials

Alexa access uses session cookies obtained by signing in through a proxy login flow.

> **Note:** The full credential setup steps are also documented in [`pyalexalist/README.md`](pyalexalist/README.md), which covers usage of the module directly.

> **Note:** GkeepAlexa passes `config/service_auth.json` (relative to `GKeepAlexa.py`) to pyalexalist, which derives `config/runtime_credentials.json` from the same directory — so the working directory you launch from does not matter.

**Step 1 — Download the Alexa Cookie CLI:**

Using the helper script:
```
python scripts/setup_alexa_cli.py
```
Or via the module directly:
```
python -m pyalexalist.setup_alexa_cli
```

This fetches the latest release from [adn77/alexa-cookie-cli](https://github.com/adn77/alexa-cookie-cli/releases) and saves the right binary for your OS in `alexa-cookie-cli/`. Skips the download if a binary is already present.

Optional arguments:

| Argument | Description |
|---|---|
| `--list` / `-l` | List all available release versions and their descriptions |
| `--version VERSION` / `-v VERSION` | Download a specific version (e.g. `--version 5.0.1`) instead of latest |
| `--force` / `-f` | Re-download even if a binary already exists |

```
# list available versions
python scripts/setup_alexa_cli.py --list

# download a specific version
python scripts/setup_alexa_cli.py --version 5.0.1
```

**Step 2 — Sign in and get a refresh token** by running the CLI for your platform:

```
# Windows
.\alexa-cookie-cli\alexa-cookie-cli-win.exe -p amazon.com -b amazon.com -a en_US -L en-US

# Linux
./alexa-cookie-cli/alexa-cookie-cli-linux -p amazon.com -b amazon.com -a en_US -L en-US

# macOS
./alexa-cookie-cli/alexa-cookie-cli-macos -p amazon.com -b amazon.com -a en_US -L en-US
```

The `-a en_US -L en-US` flags force the sign-in page to English (defaults to German otherwise). After signing in, copy the refresh token from the output and paste it into `alexa.refresh_token` in `config/service_auth.json`.

**Step 3 — Exchange for session cookies (optional):**

Using the helper script:
```
python scripts/get_alexa_cookies.py
```
Or via the module directly:
```
python -m pyalexalist.get_alexa_cookies
```

This reads `alexa.refresh_token` from `config/service_auth.json` and writes the resulting session cookies into `config/runtime_credentials.json`.

> **Note:** This step is optional. If `runtime_credentials.json` does not exist when GKeepAlexa starts, it will exchange the refresh token automatically on first run.

---

### 3. Configure Lists

Edit `config/lists_sync_config.toml` to set which lists to sync and control sync behaviour:

```toml
# === Sync Settings ===

[settings]
clear_on_startup       = true   # clear checked items from all lists when the process starts
clear_hourly           = true   # clear checked items from all lists every clear_interval_seconds
clear_interval_seconds = 3600   # how often (in seconds) to clear checked items
max_iterations         = 0      # max sync cycles to run; 0 = run indefinitely
sync_interval_seconds  = 20     # seconds to wait between sync iterations (recommended: 20+, floor: 5)

log_to_console         = true   # print log output to the terminal
log_to_file            = true   # write log output to logs/gkeepalexa.log
log_level              = "INFO" # console verbosity: DEBUG, INFO, WARNING, ERROR

[settings.gkeep]
pinned_only = true              # true = only sync pinned Google Keep notes; false = sync all notes
sort        = "none"            # item sort order applied to all lists — takes effect next iteration
                                # az, za, newest, oldest, none

# === List Sync Configuration ===

[[lists]]
name     = "Groceries"                 # friendly label used in logs and per-list log file names
category = "shopping"                  # your own label — does not affect sync behaviour
gkeep    = "Groceries/Shopping List"   # exact title of the Google Keep checklist note
alexa    = "SHOP"                      # exact name of the Alexa list
enabled  = true
# gkeep_sort = "az"                    # optional — overrides [settings.gkeep] sort for this list only

[[lists]]
name     = "Daily Tasks"
category = "todo"
gkeep    = "TODO#Alexa"
alexa    = "TODO"
enabled  = false                       # set false to pause without removing the entry
```

Add a `[[lists]]` block for each GKeep ↔ Alexa pair you want to sync.

**Sort order:** this setting applies only to the Google Keep side. Alexa clients (Alexa app, Echo Show, etc.) already provide native ways to display a list alphabetically or by date added. Google Keep does not have an equivalent automatic sort for checklist items; items remain in the order they were added or last manually rearranged.
When a sort order is selected, the app reorders the items within the Google Keep note after every sync. This keeps the note consistently sorted regardless of when the items were originally added.

The `sort` key under `[settings.gkeep]` sets a default order applied to all lists. Add `gkeep_sort` to a `[[lists]]` block to override it for that list only.

| Value | Order |
|---|---|
| `az` | A → Z (alphabetical, case-insensitive) |
| `za` | Z → A (reverse alphabetical, case-insensitive) |
| `oldest` | oldest → newest (by last-updated timestamp) |
| `newest` | newest → oldest (by last-updated timestamp) |
| `none` | no sort applied (default) |

**Quantity support:** Alexa shopping lists support a native quantity field; Alexa to-do lists (named `TODO`) do not. Quantity is automatically enabled or disabled based on the Alexa list name — no extra configuration needed.

**Google Keep note type:** only checklist notes (notes with checkboxes) are synced. Google Keep supports several note types — plain text, drawings, images, and checklists — but only checklist notes are recognised by this tool. All other types are silently skipped, even if they are pinned. Make sure the Google Keep note you point `gkeep` at is in checklist format (the checkbox list mode, not a plain text note).

### 4. Item Quantities

Alexa supports a native quantity field on list items. Since Google Keep has no quantity field, quantities are encoded in the item name text.

When reading from Google Keep, any of the following formats are recognised (case-insensitive):

| Format | Example |
|---|---|
| Suffix `×N` | `Potatoes ×4` |
| Suffix `xN` / `XN` | `Potatoes x4` |
| Suffix `* N` | `Potatoes * 4` |
| Suffix `(N)` | `Potatoes (4)` |
| Prefix `Nx` / `NX` | `4x Potatoes` |
| Prefix `N *` | `4 * Potatoes` |
| Prefix `(N)` | `(4) Potatoes` |

When writing back to Google Keep the canonical form is always used: `Potatoes x4`.

Quantities of 1 or less are treated as no quantity — the suffix is omitted and the Alexa quantity field is cleared.

### 5. Run the Sync

```
python GKeepAlexa.py
```