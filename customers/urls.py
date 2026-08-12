from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.customer_list_view, name="list"),
    path("create/", views.customer_create_view, name="create"),
    path("<int:pk>/edit/", views.customer_update_view, name="edit"),
]