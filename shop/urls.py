from django.urls import path
from . import views

app_name = 'shop'

urlpatterns = [
    path('', views.home, name='home'),
    path('shop/', views.shop, name='shop'),
    path('search-suggestions/', views.search_suggestions, name='search_suggestions'),
    path('product/<int:product_id>/', views.product_detail, name='product_detail'),
    path('repairs/', views.repairs, name='repairs'),
    path('repairs/confirmation/<str:ticket_number>/<uuid:token>/', views.repair_confirmation, name='repair_confirmation'),

    path('cart/', views.cart_detail, name='cart_detail'),
    path('cart/add/<int:product_id>/', views.cart_add, name='cart_add'),
    path('cart/update/<int:product_id>/', views.cart_update, name='cart_update'),

    path('wishlist/', views.wishlist_detail, name='wishlist_detail'),
    path('wishlist/toggle/<int:product_id>/', views.wishlist_toggle, name='wishlist_toggle'),

    path('compare/', views.compare_detail, name='compare_detail'),
    path('compare/toggle/<int:product_id>/', views.compare_toggle, name='compare_toggle'),

    path('checkout/', views.checkout, name='checkout'),
    path('order/<int:order_id>/<uuid:token>/confirmation/', views.order_confirmation, name='order_confirmation'),
    path('order/<int:order_id>/<uuid:token>/pay/', views.payment, name='payment'),
    path('order/<int:order_id>/<uuid:token>/pay/verify/', views.verify_payment, name='verify_payment'),

    # Customer accounts. Login, logout and the password-reset flow come from
    # django.contrib.auth.urls, mounted at /accounts/ in config/urls.py.
    path('account/', views.account, name='account'),
    path('account/register/', views.register, name='register'),
    path('account/orders/', views.order_history, name='order_history'),
    path('account/orders/<int:order_id>/', views.order_detail, name='order_detail'),

    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    path('track/', views.track, name='track'),
]
