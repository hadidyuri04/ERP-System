from django.urls import path
from . import views

app_name = "pos"

urlpatterns = [
    path("", views.pos_terminal, name="terminal"),
    path("api/search-product/", views.search_product, name="search_product"),
    path("sales/", views.sale_list, name="sale_list"),
    path("sales/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("complete/", views.complete_sale_view, name="complete_sale"),
    
    # Session Management Routes
    path("sessions/", views.session_list, name="session_list"),
    path("sessions/open/", views.open_session, name="open_session"),
    path("sessions/<int:pk>/close/", views.close_session, name="close_session"),
    path("sessions/<int:pk>/cash-transaction/", views.cash_transaction, name="cash_transaction"),
]