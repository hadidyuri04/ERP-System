from django import forms
from django.forms import inlineformset_factory

from .models import Product, Warehouse, WasteLoss, WasteLossItem


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "code", "barcode", "name", "description", "category", "unit",
            "purchase_price", "selling_price", "minimum_stock",
            "track_expiration", "is_active",
        ]


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["code", "name", "location", "description", "is_active"]


class WasteLossForm(forms.ModelForm):
    class Meta:
        model = WasteLoss
        fields = ["document_number", "warehouse", "date", "reason", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class WasteLossItemForm(forms.ModelForm):
    class Meta:
        model = WasteLossItem
        fields = ["product", "batch", "quantity", "unit_cost"]


WasteLossItemFormSet = inlineformset_factory(
    WasteLoss,
    WasteLossItem,
    form=WasteLossItemForm,
    extra=1,
    can_delete=True,
)
