from django import forms
from django.forms import inlineformset_factory

from .models import Category, Unit, Product, Warehouse, WasteLoss, WasteLossItem


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["code", "name_en", "name_ar", "parent", "description", "is_active"]


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["name_en", "name_ar", "symbol", "is_active"]


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "code", "barcode", "name_en", "name_ar", "description", "category", "unit",
            "purchase_price", "selling_price", "minimum_stock", "maximum_stock",
            "reorder_quantity", "track_expiration", "image", "primary_supplier", "is_active",
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