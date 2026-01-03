from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    # ROTA DE LOGIN E LOGOUT ADMIN
    path('login/', views.custom_login, name='custom_login'),
    path('logout/', views.custom_logout, name='custom_logout'),

    # ROTA DE CRIAÇAO DE LOJAS
    path('criar-loja/', views.signup, name='signup'),

    # Páginas Legais
    path('termos/', views.termos_de_uso, name='termos'),
    path('privacidade/', views.politica_privacidade, name='privacidade'),

    # ROTA HOME(INICIO DE TUDO)
    path('', views.home, name='home'),

    path('<slug:slug>/', views.cardapio_publico, name='cardapio_publico'),
    path('<slug:slug>/painel/', views.painel_lojista, name='painel_lojista'),

    path('<slug:slug>/api/create_order/', views.create_order, name='api_create_order'),

    # NOVAS ROTAS PARA O PAINEL
    path('<slug:slug>/api/orders/', views.api_get_orders, name='api_get_orders'),
    path('<slug:slug>/api/orders/<int:order_id>/update/', views.api_update_order, name='api_update_order'),
    path('<slug:slug>/api/orders/<int:order_id>/printed/', views.api_mark_printed, name='api_mark_printed'),

    # ROTA PARA CONFIGURAÇAO
    path('<slug:slug>/api/settings/', views.api_update_settings, name='api_update_settings'),

    # ROTA DO FINANCEIRO E LOJA ABERTA/FECHADA
    path('<slug:slug>/api/financials/', views.api_get_financials, name='api_get_financials'),
    path('<slug:slug>/api/store/toggle/', views.api_toggle_store_open, name='api_toggle_store_open'),
    path('<slug:slug>/api/store/sync/', views.api_sync_store_status, name='api_sync_store_status'),
    path('<slug:slug>/api/hours/', views.api_save_hours, name='api_save_hours'),

    # ROTAS PARA TAXAS DE ENTREGA
    path('<slug:slug>/api/delivery-fees/', views.api_delivery_fees, name='api_delivery_fees'),
    path('<slug:slug>/api/delivery-fees/<int:fee_id>/delete/', views.api_delete_delivery_fee, name='api_delete_delivery_fee'),

    # NOVAS ROTAS DE PRODUTOS
    path('<slug:slug>/api/products/', views.api_get_products, name='api_get_products'),
    path('<slug:slug>/api/products/save/', views.api_save_product, name='api_save_product'),
    path('<slug:slug>/api/products/<int:product_id>/delete/', views.api_delete_product, name='api_delete_product'),
    path('<slug:slug>/api/products/<int:product_id>/toggle/', views.api_toggle_product, name='api_toggle_product'),

    # ROTA PARA VER HISTORICO DE PEDIDOS
    path('<slug:slug>/api/my-orders/', views.api_customer_history, name='api_customer_history'),

    # ROTAS PARA CUPONS DE DESCONTO
    path('<slug:slug>/api/coupons/', views.api_coupons, name='api_coupons'),
    path('<slug:slug>/api/coupons/<int:coupon_id>/', views.api_coupon_details, name='api_coupon_details'),
    path('<slug:slug>/api/coupons/validate/', views.api_validate_coupon, name='api_validate_coupon'),
]

# Configuração para servir arquivos de mídia (imagens, logos, etc.)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)