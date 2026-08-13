from django.urls import path

from . import views

app_name = "suppliers"

urlpatterns = [
    path("", views.supplier_list_view, name="list"),
    path("create/", views.supplier_create_view, name="create"),
    path("<int:pk>/edit/", views.supplier_update_view, name="edit"),
]