from decimal import Decimal
from django.db.models import Sum
from django.utils.translation import gettext_lazy as _
from .models import Account, JournalEntry, JournalEntryLine

def get_account_balance(account, start_date=None, end_date=None):
    """
    Calculates running balance for a specific account within an optional date range.
    """
    lines = JournalEntryLine.objects.filter(
        account=account,
        journal_entry__status=JournalEntry.Status.POSTED
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
        journal_entry__status=JournalEntry.Status.POSTED
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
            journal_entry__status=JournalEntry.Status.POSTED
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
