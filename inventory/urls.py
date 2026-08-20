from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("products/", views.product_list_view, name="product_list"),
    path("products/create/", views.product_create_view, name="product_create"),
    path("products/<int:pk>/edit/", views.product_update_view, name="product_edit"),
    path("products/<int:pk>/stock/", views.product_stock_set_view, name="product_stock_set"),

    path("warehouses/", views.warehouse_list_view, name="warehouse_list"),
    path("warehouses/<int:pk>/edit/", views.warehouse_list_view, name="warehouse_edit"),

    path("categories/", views.category_list_view, name="category_list"),
    path("categories/<int:pk>/edit/", views.category_list_view, name="category_edit"),

    path("units/", views.unit_list_view, name="unit_list"),
    path("units/<int:pk>/edit/", views.unit_list_view, name="unit_edit"),

    path("adjustments/", views.adjustment_list_view, name="adjustment_list"),
    path("adjustments/create/", views.adjustment_create_view, name="adjustment_create"),
    path("adjustments/<int:pk>/", views.adjustment_detail_view, name="adjustment_detail"),
    path("adjustments/<int:pk>/confirm/", views.adjustment_confirm_view, name="adjustment_confirm"),

    path("batches/", views.batch_list_view, name="batch_list"),

    path("expiry/", views.expiry_watchlist_view, name="expiry_watchlist"),

    path("transfers/", views.transfer_list_view, name="transfer_list"),
    path("transfers/create/", views.transfer_create_view, name="transfer_create"),
    path("transfers/<int:pk>/", views.transfer_detail_view, name="transfer_detail"),
    path("transfers/<int:pk>/confirm/", views.transfer_confirm_view, name="transfer_confirm"),

    path("waste/", views.waste_list_view, name="waste_list"),
    path("waste/create/", views.waste_create_view, name="waste_create"),
    path("waste/<int:pk>/", views.waste_detail_view, name="waste_detail"),
    path("waste/<int:pk>/confirm/", views.waste_confirm_view, name="waste_confirm"),
]