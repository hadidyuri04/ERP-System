from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import Account, JournalEntry, JournalEntryLine


def get_account_balance(account, start_date=None, end_date=None):
    """
    Calculates running balance for a specific account within an optional date range.
    """
    lines = JournalEntryLine.objects.filter(
        account=account,
        journal_entry__status__in=[
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ]
    )
    
    if start_date:
        lines = lines.filter(journal_entry__date__gte=start_date)
    if end_date:
        lines = lines.filter(journal_entry__date__lte=end_date)

    totals = lines.aggregate(
        total_debit=Sum('debit'),
        total_credit=Sum('credit')
    )
    
    debit = totals['total_debit'] or Decimal('0.000')
    credit = totals['total_credit'] or Decimal('0.000')

    # Determine balance nature based on account type
    if account.account_type in [Account.AccountType.ASSET, Account.AccountType.EXPENSE]:
        return debit - credit
    else:
        return credit - debit


def generate_general_ledger(account_id, start_date=None, end_date=None):
    """
    Generates General Ledger line items and running balances for a selected account.
    """
    try:
        account = Account.objects.get(pk=account_id)
    except Account.DoesNotExist:
        raise ValueError(_("Selected account does not exist."))

    lines = JournalEntryLine.objects.filter(
        account=account,
        journal_entry__status__in=[
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ]
    ).select_related('journal_entry', 'customer', 'supplier').order_by('journal_entry__date', 'journal_entry__id')

    if start_date:
        lines = lines.filter(journal_entry__date__gte=start_date)
    if end_date:
        lines = lines.filter(journal_entry__date__lte=end_date)

    ledger_entries = []
    running_balance = Decimal('0.000')

    for line in lines:
        if account.account_type in [Account.AccountType.ASSET, Account.AccountType.EXPENSE]:
            running_balance += (line.debit - line.credit)
        else:
            running_balance += (line.credit - line.debit)

        ledger_entries.append({
            'date': line.journal_entry.date,
            'entry_number': line.journal_entry.entry_number,
            'description': line.description or line.journal_entry.description,
            'debit': line.debit,
            'credit': line.credit,
            'running_balance': running_balance,
            'source_type': line.journal_entry.source_type
        })

    return {
        'account': account,
        'entries': ledger_entries,
        'final_balance': running_balance
    }


def generate_trial_balance(start_date=None, end_date=None):
    """
    Generates Trial Balance grouping debit and credit totals by account 
    to verify that total debits equal total credits.
    """
    accounts = Account.objects.filter(is_active=True, allow_posting=True).order_by('code')
    
    trial_balance_rows = []
    total_debit_sum = Decimal('0.000')
    total_credit_sum = Decimal('0.000')

    for account in accounts:
        lines = JournalEntryLine.objects.filter(
            account=account,
            journal_entry__status__in=[
                JournalEntry.Status.POSTED,
                JournalEntry.Status.REVERSED,
            ]
        )
        
        if start_date:
            lines = lines.filter(journal_entry__date__gte=start_date)
        if end_date:
            lines = lines.filter(journal_entry__date__lte=end_date)

        totals = lines.aggregate(
            sum_debit=Sum('debit'),
            sum_credit=Sum('credit')
        )
        
        debit = totals['sum_debit'] or Decimal('0.000')
        credit = totals['sum_credit'] or Decimal('0.000')

        if debit > 0 or credit > 0:
            trial_balance_rows.append({
                'account_code': account.code,
                'account_name': account.name,
                'account_type': account.account_type,
                'total_debit': debit,
                'total_credit': credit
            })
            total_debit_sum += debit
            total_credit_sum += credit

    is_balanced = (total_debit_sum == total_credit_sum)

    return {
        'rows': trial_balance_rows,
        'total_debit': total_debit_sum,
        'total_credit': total_credit_sum,
        'is_balanced': is_balanced
    }


def generate_income_statement(start_date=None, end_date=None):
    accounts = Account.objects.filter(
        account_type__in=[
            Account.AccountType.REVENUE,
            Account.AccountType.EXPENSE,
        ]
    ).order_by("code")

    revenue_rows = []
    expense_rows = []
    total_revenue = Decimal("0.000")
    total_expenses = Decimal("0.000")

    for account in accounts:
        lines = JournalEntryLine.objects.filter(
            account=account,
            journal_entry__status__in=[
                JournalEntry.Status.POSTED,
                JournalEntry.Status.REVERSED,
            ],
        )

        if start_date:
            lines = lines.filter(journal_entry__date__gte=start_date)

        if end_date:
            lines = lines.filter(journal_entry__date__lte=end_date)

        totals = lines.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit"),
        )

        debit = totals["debit"] or Decimal("0.000")
        credit = totals["credit"] or Decimal("0.000")

        if account.account_type == Account.AccountType.REVENUE:
            amount = credit - debit

            if amount != 0:
                revenue_rows.append({
                    "account_code": account.code,
                    "account_name": account.name,
                    "amount": amount,
                })
                total_revenue += amount

        elif account.account_type == Account.AccountType.EXPENSE:
            amount = debit - credit

            if amount != 0:
                expense_rows.append({
                    "account_code": account.code,
                    "account_name": account.name,
                    "amount": amount,
                })
                total_expenses += amount

    net_profit = total_revenue - total_expenses

    return {
        "revenue_rows": revenue_rows,
        "expense_rows": expense_rows,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
    }


def generate_balance_sheet(as_of_date=None):
    accounts = Account.objects.filter(
        account_type__in=[
            Account.AccountType.ASSET,
            Account.AccountType.LIABILITY,
            Account.AccountType.EQUITY,
            Account.AccountType.REVENUE,
            Account.AccountType.EXPENSE,
        ]
    ).order_by("code")

    asset_rows = []
    liability_rows = []
    equity_rows = []
    total_assets = Decimal("0.000")
    total_liabilities = Decimal("0.000")
    total_equity = Decimal("0.000")
    total_revenue = Decimal("0.000")
    total_expenses = Decimal("0.000")

    for account in accounts:
        lines = JournalEntryLine.objects.filter(
            account=account,
            journal_entry__status__in=[
                JournalEntry.Status.POSTED,
                JournalEntry.Status.REVERSED,
            ],
        )
        if as_of_date:
            lines = lines.filter(journal_entry__date__lte=as_of_date)

        totals = lines.aggregate(debit=Sum("debit"), credit=Sum("credit"))
        debit = totals["debit"] or Decimal("0.000")
        credit = totals["credit"] or Decimal("0.000")

        if account.account_type == Account.AccountType.ASSET:
            amount = debit - credit
            if amount != 0:
                asset_rows.append({
                    "account_code": account.code,
                    "account_name": account.name,
                    "amount": amount,
                })
                total_assets += amount
        elif account.account_type == Account.AccountType.LIABILITY:
            amount = credit - debit
            if amount != 0:
                liability_rows.append({
                    "account_code": account.code,
                    "account_name": account.name,
                    "amount": amount,
                })
                total_liabilities += amount
        elif account.account_type == Account.AccountType.EQUITY:
            amount = credit - debit
            if amount != 0:
                equity_rows.append({
                    "account_code": account.code,
                    "account_name": account.name,
                    "amount": amount,
                })
                total_equity += amount
        elif account.account_type == Account.AccountType.REVENUE:
            total_revenue += credit - debit
        elif account.account_type == Account.AccountType.EXPENSE:
            total_expenses += debit - credit

    current_earnings = total_revenue - total_expenses
    total_equity_and_earnings = total_equity + current_earnings
    total_liabilities_and_equity = total_liabilities + total_equity_and_earnings
    difference = total_assets - total_liabilities_and_equity

    return {
        "asset_rows": asset_rows,
        "liability_rows": liability_rows,
        "equity_rows": equity_rows,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "current_earnings": current_earnings,
        "total_equity_and_earnings": total_equity_and_earnings,
        "total_liabilities_and_equity": total_liabilities_and_equity,
        "difference": difference,
        "is_balanced": difference == Decimal("0.000"),
    }


def _generate_party_statement(
    *,
    party,
    party_field,
    account_code,
    debit_normal,
    start_date=None,
    end_date=None,
):
    """Build a receivable/payable statement from posted journal lines."""
    lines = JournalEntryLine.objects.filter(
        **{party_field: party},
        account__code=account_code,
        journal_entry__status__in=[
            JournalEntry.Status.POSTED,
            JournalEntry.Status.REVERSED,
        ],
    ).select_related("journal_entry", "account")

    opening_lines = lines
    if start_date:
        opening_lines = opening_lines.filter(
            journal_entry__date__lt=start_date
        )
        lines = lines.filter(journal_entry__date__gte=start_date)
    else:
        opening_lines = lines.none()

    if end_date:
        lines = lines.filter(journal_entry__date__lte=end_date)

    opening = opening_lines.aggregate(
        debit=Sum("debit"),
        credit=Sum("credit"),
    )
    prior_activity = (
        (opening["debit"] or Decimal("0.000"))
        - (opening["credit"] or Decimal("0.000"))
    )
    if not debit_normal:
        prior_activity = -prior_activity

    opening_balance = party.opening_balance + prior_activity

    running_balance = opening_balance
    entries = []

    for line in lines.order_by(
        "journal_entry__date",
        "journal_entry_id",
        "id",
    ):
        movement = line.debit - line.credit
        if not debit_normal:
            movement = -movement
        running_balance += movement
        entries.append({
            "date": line.journal_entry.date,
            "entry_number": line.journal_entry.entry_number,
            "description": line.description or line.journal_entry.description,
            "debit": line.debit,
            "credit": line.credit,
            "balance": running_balance,
        })

    return {
        "party": party,
        "opening_balance": opening_balance,
        "entries": entries,
        "closing_balance": running_balance,
    }


def generate_customer_statement(customer, start_date=None, end_date=None):
    return _generate_party_statement(
        party=customer,
        party_field="customer",
        account_code="1300",
        debit_normal=True,
        start_date=start_date,
        end_date=end_date,
    )


def generate_supplier_statement(supplier, start_date=None, end_date=None):
    return _generate_party_statement(
        party=supplier,
        party_field="supplier",
        account_code="2100",
        debit_normal=False,
        start_date=start_date,
        end_date=end_date,
    )


AGING_BUCKETS = (
    "current",
    "days_1_30",
    "days_31_60",
    "days_61_90",
    "days_90_plus",
)


def _aging_bucket(due_date, as_of_date):
    days_overdue = (as_of_date - due_date).days
    if days_overdue <= 0:
        return "current", max(days_overdue, 0)
    if days_overdue <= 30:
        return "days_1_30", days_overdue
    if days_overdue <= 60:
        return "days_31_60", days_overdue
    if days_overdue <= 90:
        return "days_61_90", days_overdue
    return "days_90_plus", days_overdue


def _source_documents(lines, item_type):
    """Return source-id mappings used for invoice numbers and due dates."""
    if item_type == "receivable":
        from pos.models import POSSale

        source_ids = {
            line.journal_entry.source_id
            for line in lines
            if line.journal_entry.source_type == JournalEntry.SourceType.POS_SALE
            and line.journal_entry.source_id
        }
        return {
            sale.pk: sale
            for sale in POSSale.objects.filter(pk__in=source_ids)
        }

    from purchasing.models import PurchaseInvoice

    source_ids = {
        line.journal_entry.source_id
        for line in lines
        if line.journal_entry.source_type == JournalEntry.SourceType.PURCHASE
        and line.journal_entry.source_id
    }
    return {
        invoice.pk: invoice
        for invoice in PurchaseInvoice.objects.filter(pk__in=source_ids)
    }


def _line_document(line, item_type, source_documents):
    journal = line.journal_entry
    document_number = journal.entry_number
    due_date = journal.date

    if item_type == "receivable" and journal.source_type == JournalEntry.SourceType.POS_SALE:
        sale = source_documents.get(journal.source_id)
        if sale:
            document_number = sale.sale_number
    elif item_type == "payable" and journal.source_type == JournalEntry.SourceType.PURCHASE:
        invoice = source_documents.get(journal.source_id)
        if invoice:
            document_number = invoice.invoice_number
            due_date = invoice.due_date or invoice.invoice_date

    return document_number, due_date


def _apply_fifo(open_items, amount):
    for item in open_items:
        if amount <= 0:
            break
        if item["remaining"] <= 0:
            continue
        applied = min(item["remaining"], amount)
        item["remaining"] -= applied
        amount -= applied
    return amount


def _generate_aging_report(*, item_type, as_of_date=None, party=None):
    """Age posted receivables/payables and apply settlements oldest-first."""
    from customers.models import Customer
    from suppliers.models import Supplier

    as_of_date = as_of_date or timezone.localdate()
    is_receivable = item_type == "receivable"
    party_field = "customer" if is_receivable else "supplier"
    account_code = "1300" if is_receivable else "2100"
    party_model = Customer if is_receivable else Supplier

    line_filters = {
        f"{party_field}__isnull": False,
        "account__code": account_code,
        "journal_entry__status": JournalEntry.Status.POSTED,
        "journal_entry__date__lte": as_of_date,
    }
    if party is not None:
        line_filters[party_field] = party

    lines = list(
        JournalEntryLine.objects.filter(**line_filters)
        .exclude(journal_entry__source_type=JournalEntry.SourceType.REVERSAL)
        .select_related("journal_entry", party_field)
        .order_by("journal_entry__date", "journal_entry_id", "id")
    )
    source_documents = _source_documents(lines, item_type)

    parties = {getattr(line, f"{party_field}_id"): getattr(line, party_field) for line in lines}
    opening_parties = party_model.objects.filter(opening_balance__isnull=False).exclude(
        opening_balance=0
    )
    if party is not None:
        opening_parties = opening_parties.filter(pk=party.pk)
    for opening_party in opening_parties:
        if opening_party.created_at.date() <= as_of_date:
            parties[opening_party.pk] = opening_party

    state = {
        party_id: {"party": party_object, "items": [], "unapplied_credit": Decimal("0.000")}
        for party_id, party_object in parties.items()
    }

    for party_id, party_state in state.items():
        opening_balance = party_state["party"].opening_balance or Decimal("0.000")
        if opening_balance > 0:
            opening_date = party_state["party"].created_at.date()
            party_state["items"].append({
                "document_number": str(_("Opening balance")),
                "document_date": opening_date,
                "due_date": opening_date,
                "original_amount": opening_balance,
                "remaining": opening_balance,
            })
        elif opening_balance < 0:
            party_state["unapplied_credit"] = -opening_balance

    for line in lines:
        party_state = state[getattr(line, f"{party_field}_id")]
        movement = line.debit - line.credit
        if not is_receivable:
            movement = -movement

        if movement > 0:
            document_number, due_date = _line_document(
                line, item_type, source_documents
            )
            amount = movement
            if party_state["unapplied_credit"]:
                applied = min(amount, party_state["unapplied_credit"])
                amount -= applied
                party_state["unapplied_credit"] -= applied
            party_state["items"].append({
                "document_number": document_number,
                "document_date": line.journal_entry.date,
                "due_date": due_date,
                "original_amount": movement,
                "remaining": amount,
            })
        elif movement < 0:
            excess = _apply_fifo(party_state["items"], -movement)
            party_state["unapplied_credit"] += excess

    rows = []
    grand_totals = {bucket: Decimal("0.000") for bucket in AGING_BUCKETS}
    grand_totals.update({
        "total_outstanding": Decimal("0.000"),
        "unapplied_credit": Decimal("0.000"),
        "balance": Decimal("0.000"),
    })

    for party_id in sorted(state, key=lambda value: state[value]["party"].name.lower()):
        party_state = state[party_id]
        bucket_totals = {bucket: Decimal("0.000") for bucket in AGING_BUCKETS}
        documents = []

        for item in party_state["items"]:
            if item["remaining"] <= 0:
                continue
            bucket, days_overdue = _aging_bucket(item["due_date"], as_of_date)
            bucket_totals[bucket] += item["remaining"]
            documents.append({
                **item,
                "bucket": bucket,
                "days_overdue": days_overdue,
            })

        total_outstanding = sum(bucket_totals.values(), Decimal("0.000"))
        unapplied_credit = party_state["unapplied_credit"]
        if total_outstanding == 0 and unapplied_credit == 0:
            continue
        balance = total_outstanding - unapplied_credit
        row = {
            "party": party_state["party"],
            **bucket_totals,
            "total_outstanding": total_outstanding,
            "unapplied_credit": unapplied_credit,
            "balance": balance,
            "documents": documents,
        }
        rows.append(row)
        for key in grand_totals:
            grand_totals[key] += row[key]

    return {
        "item_type": item_type,
        "as_of_date": as_of_date,
        "rows": rows,
        "totals": grand_totals,
    }


def generate_receivables_aging(as_of_date=None, customer=None):
    return _generate_aging_report(
        item_type="receivable",
        as_of_date=as_of_date,
        party=customer,
    )


def generate_payables_aging(as_of_date=None, supplier=None):
    return _generate_aging_report(
        item_type="payable",
        as_of_date=as_of_date,
        party=supplier,
    )
