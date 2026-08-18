from django import forms
from django.forms import inlineformset_factory

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from .models import (
    Category,
    Product,
    StockAdjustment,
    StockAdjustmentItem,
    Unit,
    Warehouse,
    WarehouseTransfer,
    WarehouseTransferItem,
    WasteLoss,
    WasteLossItem,
)


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
            "reorder_quantity", "tax_rate", "maximum_discount", "track_expiration",
            "is_sellable", "image", "primary_supplier", "is_active",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from finance.models import TaxRate

        self.fields["tax_rate"].queryset = TaxRate.objects.filter(
            is_active=True
        ).order_by("code")

    def clean_maximum_discount(self):
        value = self.cleaned_data["maximum_discount"]
        if value < 0 or value > 100:
            raise ValidationError(_("Maximum discount must be between 0 and 100."))
        return value


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["code", "name", "location", "description", "is_active"]


class WasteLossForm(forms.ModelForm):
    class Meta:
        model = WasteLoss
        fields = ["document_number", "warehouse", "date", "reason", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = Warehouse.objects.filter(
            is_active=True
        ).order_by("name")


class WasteLossItemForm(forms.ModelForm):
    class Meta:
        model = WasteLossItem
        fields = ["product", "batch", "quantity", "unit_cost"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(
            is_active=True
        ).order_by("code")


class WarehouseTransferForm(forms.ModelForm):
    class Meta:
        model = WarehouseTransfer
        fields = [
            "transfer_number", "source_warehouse", "destination_warehouse",
            "date", "notes",
        ]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Retired warehouses must not appear on new documents.
        active = Warehouse.objects.filter(is_active=True).order_by("name")
        self.fields["source_warehouse"].queryset = active
        self.fields["destination_warehouse"].queryset = active

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source_warehouse")
        destination = cleaned.get("destination_warehouse")
        if source and destination and source == destination:
            raise ValidationError({
                "destination_warehouse": _(
                    "The source and destination warehouses must be different."
                )
            })
        return cleaned


class WarehouseTransferItemForm(forms.ModelForm):
    class Meta:
        model = WarehouseTransferItem
        fields = ["product", "batch", "quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(
            is_active=True
        ).order_by("code")

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity <= 0:
            raise ValidationError(_("Transfer quantity must be greater than zero."))
        return quantity


WarehouseTransferItemFormSet = inlineformset_factory(
    WarehouseTransfer,
    WarehouseTransferItem,
    form=WarehouseTransferItemForm,
    extra=1,
    can_delete=True,
)


class StockAdjustmentForm(forms.ModelForm):
    class Meta:
        model = StockAdjustment
        fields = ["adjustment_number", "warehouse", "date", "notes"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["warehouse"].queryset = Warehouse.objects.filter(
            is_active=True
        ).order_by("name")


class StockAdjustmentItemForm(forms.ModelForm):
    class Meta:
        model = StockAdjustmentItem
        # system_quantity and variance are calculated, never typed.
        fields = ["product", "batch", "counted_quantity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(
            is_active=True
        ).order_by("code")

    def clean_counted_quantity(self):
        counted = self.cleaned_data["counted_quantity"]
        if counted < 0:
            raise ValidationError(_("Counted quantity cannot be negative."))
        return counted


StockAdjustmentItemFormSet = inlineformset_factory(
    StockAdjustment,
    StockAdjustmentItem,
    form=StockAdjustmentItemForm,
    extra=1,
    can_delete=True,
)


WasteLossItemFormSet = inlineformset_factory(
    WasteLoss,
    WasteLossItem,
    form=WasteLossItemForm,
    extra=1,
    can_delete=True,
)