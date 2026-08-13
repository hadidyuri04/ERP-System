from django.urls import path

from . import views

app_name = "quotations"

urlpatterns = [
    path("", views.quotation_list, name="list"),
    path("create/", views.quotation_create, name="create"),
    path("<int:pk>/", views.quotation_detail, name="detail"),
    path("<int:pk>/convert/", views.convert_to_sale_view, name="convert"),
]
