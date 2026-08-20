# Finance, Inventory & POS System

Django ERP for **Abu Ahmad's Supermarket**: purchasing, inventory, point of sale,
sales invoicing and double-entry accounting, in Arabic and English.

---

## Setting up from scratch

Follow these in order. Several steps are not obvious and the error messages do
not tell you what to do, so none of them are optional.

### 1. Virtual environment

```bash
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
# source venv/bin/activate       # macOS / Linux
```

### 2. Dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

`playwright install` downloads a headless browser used to render PDF exports.
Without it the PDF buttons **silently return an HTML page instead of a PDF** —
no error, just the wrong file.

> Never regenerate `requirements.txt` with `pip freeze > requirements.txt` in
> PowerShell. It writes UTF-16, which `pip install -r` cannot read on Linux.
> Use `pip freeze | Out-File -Encoding utf8 requirements.txt`.

### 3. Database

Create a PostgreSQL database, then copy the environment file:

```bash
copy .env.example .env
```

Fill in `DB_NAME`, `DB_USER`, `DB_PASSWORD`. `.env` is gitignored and must
never be committed.

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Seed the accounting data

```bash
python manage.py loaddata fixtures/chart_of_accounts.json
python manage.py loaddata fixtures/tax_rates.json
```

The chart of accounts is **required**. Every posting service looks up accounts
by code, and a missing one aborts the whole operation — including the stock
movement, because the services are atomic.

| Code | Used by |
|---|---|
| 1100 / 1200 / 1210 | cash, bank, card clearing |
| 1300 / 2100 | customer and supplier balances |
| 1400 | inventory |
| 1500 / 2200 | purchase and sales tax |
| 4100 / 5100 | sales revenue, cost of goods sold |
| 4300 / 6310 | stock adjustment gain and loss |
| 6300 | waste and loss |

### 5. Open a fiscal year

Start the server, then go to **`/finance/periods/create-year/`** and create the
current year. This creates its twelve monthly periods.

**Nothing will post to accounting until you do this.** You get
`No fiscal year has been configured for this date`, which does not hint at the
fix. This catches everyone setting up fresh.

### 6. Run it

```bash
python manage.py runserver
```

---

## Daily commands

```bash
python manage.py test                         # 190 tests
python manage.py check                        # configuration problems
python manage.py mark_expired_batches         # flag batches past their date
python manage.py generate_notifications       # low stock and expiry alerts
python manage.py check_stock                  # balances vs batches; --fix repairs
```

`mark_expired_batches` and `generate_notifications` are safe to run repeatedly
and are meant for a daily schedule.

`check_stock` compares `StockBalance` against the batches behind it. They can
drift if a service fails halfway. Symptom: an item shows stock but POS refuses
to sell it.

---

## Translations

The UI is Arabic-first. Every user-facing string goes through `{% trans %}` or
`gettext_lazy`.

`gettext` is not installed on the team's machines, so `.mo` files are compiled
with **polib** instead of `compilemessages`:

```bash
python -c "import polib; po=polib.pofile('locale/ar/LC_MESSAGES/django.po'); po.save_as_mofile('locale/ar/LC_MESSAGES/django.mo')"
```

### When `django.mo` conflicts in a merge

It is compiled output and git cannot merge it. Do not edit it by hand:

```bash
# resolve django.po normally, then rebuild the .mo from it
python -c "import polib; po=polib.pofile('locale/ar/LC_MESSAGES/django.po'); po.save_as_mofile('locale/ar/LC_MESSAGES/django.mo')"
git add locale/ar/LC_MESSAGES/django.po locale/ar/LC_MESSAGES/django.mo
```

### After running `makemessages`

**Always review fuzzy entries before committing.** `gettext` guesses
translations from similar strings and the guesses are often badly wrong — we
have had "Usage Limit" rendered as "Credit Limit" and a cart error rendered as
a message about waste quantity.

```bash
python -c "import polib; po=polib.pofile('locale/ar/LC_MESSAGES/django.po'); print(len(po.fuzzy_entries()), 'fuzzy,', len(po.untranslated_entries()), 'untranslated')"
```

Both numbers should be zero before you push. Also run
`makemessages --ignore=venv`, or Django's own internal strings get pulled in.

---

## How the modules fit together

```
Purchase invoice ──confirm──▶ stock batches ──▶ StockBalance
                                    │                 │
                     Warehouse transfer          POS sale (FEFO)
                     Stock adjustment            Sales invoice
                     Waste & loss                     │
                                    └──────────▶ Journal entries
```

Rules that hold everywhere:

- **Nothing changes stock without a document.** No screen edits a quantity
  directly; corrections go through a stock adjustment.
- **Drafts are editable, confirmed documents are locked.** Corrections use a
  return, cancellation, reversal or adjustment.
- **FEFO**: sales draw from the batch expiring soonest, and never from an
  expired one.
- **Stock never goes negative.**
- **Every posting is atomic.** If the accounting step fails, the stock movement
  rolls back with it.

### Where stock comes from and goes

| Direction | Document |
|---|---|
| In | Purchase invoice, stock adjustment surplus, transfer in |
| Out | POS sale, sales invoice, waste & loss, adjustment shortage, transfer out |

---

## Apps

| App | Owns |
|---|---|
| `core` | company settings, permissions, dashboard, shared test helpers |
| `accounts` | users and roles |
| `customers`, `suppliers` | parties |
| `inventory` | products, batches, warehouses, transfers, adjustments, waste, expiry |
| `purchasing` | purchase invoices |
| `sales` | sales invoices, payments, credit notes |
| `pos` | register sessions, sales, held orders, discount codes |
| `quotations` | price offers, converted into sales invoices |
| `finance` | accounts, journals, vouchers, fiscal periods, reports, exports |
| `notifications` | low stock and expiry alerts |

---

## Working as a team

Conflicts on this project have come almost entirely from two people editing the
same app at the same time. Agree who owns what, and:

```bash
git pull --no-edit        # before you start and before you push
python manage.py test     # before every push
git status --short        # check nothing is left behind, then add it
```

`git pull` fixes a rejected push. It does **not** prevent conflicts — it is
what reveals them. Pulling every hour gives small conflicts; pulling once a day
gives one large one.

Never commit a file containing `<<<<<<<`. To check:

```bash
git grep -n "^<<<<<<<"
```

---

## Gotchas worth knowing

- **Arabic decimal separators.** Django renders `0.350` as `0,350` in Arabic.
  Any number that JavaScript will parse must use `|unlocalize`, or
  `Number()` returns `NaN`. This broke the whole POS cart once.
- **`{# #}` comments are single-line only.** A multi-line one leaves its
  contents live — a template once included itself and hit the recursion limit.
  Use `{% comment %}` for anything longer than a line.
- **Status value casing is not consistent between apps.** `finance`,
  `purchasing`, `pos` and `sales` use lowercase (`draft`); `inventory` uses
  uppercase (`DRAFT`). Comparing the wrong case in a template fails silently
  and hides buttons.
- **`loaddata` skips `auto_now_add`.** Fixtures must set `created_at` and
  `updated_at` explicitly or the insert fails on a not-null constraint.
- **Uploaded files** live in `media/` and are gitignored. Never commit customer
  images.
