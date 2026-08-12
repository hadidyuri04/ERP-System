from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from .models import Quotation
from .forms import QuotationForm, QuotationItemFormSet
from .services import convert_quotation_to_pos_sale

@login_required
def quotation_list(request):
    quotations = Quotation.objects.select_related('customer', 'created_by').order_by('-created_at')
    return render(request, 'quotations/quotation_list.html', {'quotations': quotations})

@login_required
def quotation_detail(request, pk):
    quotation = get_object_or_404(Quotation.objects.prefetch_related('items__product'), pk=pk)
    return render(request, 'quotations/quotation_detail.html', {'quotation': quotation})

@login_required
@transaction.atomic
def quotation_create(request):
    if request.method == 'POST':
        form = QuotationForm(request.POST)
        formset = QuotationItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            quotation = form.save(commit=False)
            quotation.created_by = request.user
            quotation.quotation_number = f"QT-{timezone.now().strftime('%Y%m%d%H%M%S')}"
            quotation.save()
            
            formset.instance = quotation
            formset.save()
            
            messages.success(request, _("Quotation created successfully."))
            return redirect('quotation_detail', pk=quotation.pk)
    else:
        form = QuotationForm()
        formset = QuotationItemFormSet()

    return render(request, 'quotations/quotation_form.html', {'form': form, 'formset': formset})

@login_required
def convert_to_sale_view(request, pk):
    quotation = get_object_or_404(Quotation, pk=pk)
    if request.method == 'POST':
        warehouse_id = request.POST.get('warehouse')
        payment_method = request.POST.get('payment_method', 'CASH')
        
        # Construct single payment block from POST
        payments_data = [{
            'payment_method': payment_method,
            'amount': quotation.total,
            'reference_number': request.POST.get('reference_number', '')
        }]

        try:
            from inventory.models import Warehouse
            warehouse = Warehouse.objects.get(pk=warehouse_id)
            sale = convert_quotation_to_pos_sale(
                quotation_id=quotation.id,
                warehouse=warehouse,
                cashier=request.user,
                payments_data=payments_data
            )
            messages.success(request, _("Quotation successfully converted to POS Sale %(sale_num)s") % {'sale_num': sale.sale_number})
            return redirect('pos_sale_detail', pk=sale.pk)
        except Exception as e:
            messages.error(request, str(e))
            return redirect('quotation_detail', pk=pk)

    return redirect('quotation_detail', pk=pk)