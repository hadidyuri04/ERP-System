from django.urls import path
from . import views

app_name = "pos"

urlpatterns = [
    path("", views.pos_terminal, name="terminal"),
    path("api/search-product/", views.search_product, name="search_product"), # New API route
    path("sales/", views.sale_list, name="sale_list"),
    path("sales/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("complete/", views.complete_sale_view, name="complete_sale"),
]