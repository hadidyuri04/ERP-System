from django.urls import path

from . import views

app_name = "inventory"

urlpatterns = [
    path("products/", views.product_list_view, name="product_list"),
    path("products/create/", views.product_create_view, name="product_create"),
    path("products/<int:pk>/edit/", views.product_update_view, name="product_edit"),

    path("warehouses/", views.warehouse_list_view, name="warehouse_list"),

    path("waste/", views.waste_list_view, name="waste_list"),
    path("waste/create/", views.waste_create_view, name="waste_create"),
    path("waste/<int:pk>/", views.waste_detail_view, name="waste_detail"),
    path("waste/<int:pk>/confirm/", views.waste_confirm_view, name="waste_confirm"),
]