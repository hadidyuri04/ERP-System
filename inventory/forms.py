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


class BatchSelect(forms.Select):
    """
    Batch dropdown that carries the warehouse on each option.

    The batch list is global, so a transfer out of the Main warehouse would
    offer batches sitting in the Branch store. The service refuses those, but
    the form should not offer them in the first place. The warehouse is only
    known once the user picks it, so the filtering happens in the browser using
    this attribute.
    """

    def create_option(self, name, value, label, selected, index,
                      subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-warehouse"] = instance.warehouse_id
        return option


class StockedProductSelect(forms.Select):
    """
    Product dropdown that lists which warehouses hold each product.

    A transfer out of Main was offering products that only exist in the Branch
    store. The service refuses them, but the user should not be able to pick
    one. A product can sit in several warehouses, so the option carries a
    comma-separated list rather than a single id.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._stock_map = None

    def stock_map(self):
        from .models import StockBalance

        if self._stock_map is None:
            self._stock_map = {}
            rows = StockBalance.objects.filter(quantity__gt=0).values_list(
                "product_id", "warehouse_id"
            )
            for product_id, warehouse_id in rows:
                self._stock_map.setdefault(product_id, set()).add(warehouse_id)
        return self._stock_map

    def create_option(self, name, value, label, selected, index,
                      subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        instance = getattr(value, "instance", None)
        if instance is not None:
            warehouses = self.stock_map().get(instance.pk, set())
            option["attrs"]["data-warehouses"] = ",".join(
                str(w) for w in sorted(warehouses)
            )
        return option


class BatchChoiceField(forms.ModelChoiceField):
    """
    Batch dropdown label that actually says what the batch is.

    The default label was "Batch 3 - Ice Tea (18.000 left)", which gave no
    warehouse and no expiry date. Two different batches in two warehouses
    looked identical, and an expired batch looked perfectly fine.
    """

    widget = BatchSelect

    def label_from_instance(self, batch):
        from django.utils import timezone

        parts = [batch.batch_number, batch.product.name, batch.warehouse.name]

        if batch.expiration_date:
            if batch.expiration_date < timezone.now().date():
                parts.append(
                    _("expired %(date)s") % {"date": batch.expiration_date}
                )
            else:
                parts.append(
                    _("expires %(date)s") % {"date": batch.expiration_date}
                )

        parts.append(
            _("%(qty)s left") % {"qty": f"{batch.quantity_remaining:.3f}"}
        )
        return " · ".join(str(p) for p in parts)


def sellable_batches(include_expired=True):
    """
    Batches that still hold stock, nearest expiry first.

    `include_expired` is False for transfers: moving expired stock between
    warehouses is not a thing. Waste and stock counts need them, because
    writing them off and counting them are exactly what they are for.
    """
    from django.db.models import Q
    from django.utils import timezone

    from .models import StockBatch

    queryset = (
        StockBatch.objects
        .select_related("warehouse", "product")
        .filter(quantity_remaining__gt=0)
        .exclude(status=StockBatch.BatchStatus.DEPLETED)
        .order_by("expiration_date", "batch_number")
    )

    if not include_expired:
        today = timezone.now().date()
        queryset = queryset.filter(
            Q(expiration_date__isnull=True) | Q(expiration_date__gte=today)
        )

    return queryset


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
        self.fields["product"].widget = StockedProductSelect(
            choices=self.fields["product"].widget.choices
        )
        # Expired batches belong here: writing them off is the point.
        self.fields["batch"] = BatchChoiceField(
            queryset=sellable_batches(include_expired=True),
            required=self.fields["batch"].required,
            label=self.fields["batch"].label,
        )


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
        self.fields["product"].widget = StockedProductSelect(
            choices=self.fields["product"].widget.choices
        )
        # Moving expired stock between warehouses is not a valid operation.
        self.fields["batch"] = BatchChoiceField(
            queryset=sellable_batches(include_expired=False),
            required=self.fields["batch"].required,
            label=self.fields["batch"].label,
        )

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
        self.fields["product"].widget = StockedProductSelect(
            choices=self.fields["product"].widget.choices
        )
        # Expired batches belong here: writing them off is the point.
        self.fields["batch"] = BatchChoiceField(
            queryset=sellable_batches(include_expired=True),
            required=self.fields["batch"].required,
            label=self.fields["batch"].label,
        )

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