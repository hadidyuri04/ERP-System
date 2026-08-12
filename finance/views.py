from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from core.permissions import accountant_required
from .reports import generate_general_ledger, generate_trial_balance
from .models import Account, JournalEntry
from .services import post_journal_entry

@login_required
@accountant_required
def general_ledger_view(request):
    """
    General Ledger report. Accessible strictly by Accountants and Admins.
    """
    account_id = request.GET.get('account_id')
    context = {'accounts': Account.objects.filter(is_active=True).order_by('code')}
    
    if account_id:
        try:
            ledger_data = generate_general_ledger(account_id=account_id)
            context.update(ledger_data)
        except ValueError as e:
            messages.error(request, str(e))
            
    return render(request, 'finance/general_ledger.html', context)

@login_required
@accountant_required
def trial_balance_view(request):
    """
    Trial Balance report verifying debits equal credits.
    """
    tb_data = generate_trial_balance()
    return render(request, 'finance/trial_balance.html', {'tb_data': tb_data})

@login_required
@accountant_required
def post_voucher_view(request, pk):
    """
    Posts a draft journal entry/voucher to the ledger.
    """
    entry = get_object_or_404(JournalEntry, pk=pk)
    if request.method == 'POST':
        try:
            post_journal_entry(entry.id)
            messages.success(request, _("Voucher posted successfully."))
        except Exception as e:
            messages.error(request, str(e))
    return redirect('finance:ledger')