from django.urls import path

from . import views

app_name = "purchasing"

urlpatterns = [
    path("", views.purchase_list_view, name="list"),
    path("create/", views.purchase_create_view, name="create"),
    path("<int:pk>/", views.purchase_detail_view, name="detail"),
    path("<int:pk>/edit/", views.purchase_update_view, name="edit"),
    path("<int:pk>/confirm/", views.purchase_confirm_view, name="confirm"),
]