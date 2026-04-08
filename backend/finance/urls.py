from django.urls import path

from . import views

app_name = "finance"

urlpatterns = [
    path("", views.research_home, name="research_home"),
    path("refresh/", views.refresh_finance, name="refresh_finance"),
    path("state/", views.finance_state, name="finance_state"),
    path("search/", views.ticker_search, name="ticker_search"),
    path("research/", views.ticker_research, name="ticker_research"),
    path("schwab/connect/", views.schwab_connect, name="schwab_connect"),
    path("schwab/callback/", views.schwab_callback, name="schwab_callback"),
    path("schwab/market/connect/", views.schwab_market_connect, name="schwab_market_connect"),
    path("schwab/market/callback/", views.schwab_market_callback, name="schwab_market_callback"),
]
