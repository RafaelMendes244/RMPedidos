from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views
from django.contrib.auth import views as auth_views

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

    # ROTA DO CARDÁPIO PÚBLICO
    path('<slug:slug>/', views.cardapio_publico, name='cardapio_publico'),
    
    # ROTA DO CARDÁPIO POR MESA (QR Code)
    path('<slug:slug>/mesa/<int:table_number>/', views.cardapio_mesa, name='cardapio_mesa'),
    
    # ROTA DO PAINEL DO LOJISTA
    path('<slug:slug>/painel/', views.painel_lojista, name='painel_lojista'),

    # PWA MANIFEST
    path('<slug:slug>/manifest.json', views.pwa_manifest, name='pwa_manifest'),

    # ROTA PARA CRIAÇÃO DE PEDIDOS
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
    # API PÚBLICA para status da loja (para o cardápio do cliente)
    path('<slug:slug>/api/store/status/', views.api_public_store_status, name='api_public_store_status'),
    path('<slug:slug>/api/hours/', views.api_save_hours, name='api_save_hours'),

    # ROTAS PARA TAXAS DE ENTREGA
    path('<slug:slug>/api/delivery-fees/', views.api_delivery_fees, name='api_delivery_fees'),
    path('<slug:slug>/api/delivery-fees/<int:fee_id>/delete/', views.api_delete_delivery_fee, name='api_delete_delivery_fee'),

    # NOVAS ROTAS DE PRODUTOS
    path('<slug:slug>/api/products/', views.api_get_products, name='api_get_products'),
    path('<slug:slug>/api/products/save/', views.api_save_product, name='api_save_product'),
    path('<slug:slug>/api/products/<int:product_id>/delete/', views.api_delete_product, name='api_delete_product'),
    path('<slug:slug>/api/products/<int:product_id>/toggle/', views.api_toggle_product, name='api_toggle_product'),
    path('loja/<slug:slug>/api/product/<int:product_id>/options/', views.api_get_product_options, name='api_get_product_options'),

    # ROTAS PARA GRUPOS REUTILIZÁVEIS
    path('<slug:slug>/api/groups/', views.api_get_product_groups, name='api_get_product_groups'),
    path('<slug:slug>/api/groups/save/', views.api_save_product_group, name='api_save_product_group'),
    path('<slug:slug>/api/groups/<int:group_id>/delete/', views.api_delete_product_group, name='api_delete_product_group'),
    path('<slug:slug>/api/products/<int:product_id>/import-group/', views.api_import_product_group, name='api_import_product_group'),

    # ROTA PARA VER HISTORICO DE PEDIDOS
    path('<slug:slug>/api/my-orders/', views.api_customer_history, name='api_customer_history'),

    # ROTAS PARA CUPONS DE DESCONTO
    path('<slug:slug>/api/coupons/', views.api_coupons, name='api_coupons'),
    path('<slug:slug>/api/coupons/<int:coupon_id>/', views.api_coupon_details, name='api_coupon_details'),
    path('<slug:slug>/api/coupons/validate/', views.api_validate_coupon, name='api_validate_coupon'),
    
    # ========================
    # ROTAS DE MESAS (NOVO)
    # ========================
    path('<slug:slug>/api/tables/', views.api_tables, name='api_tables'),
    path('<slug:slug>/api/tables/<int:table_id>/', views.api_table_details, name='api_table_details'),
    path('<slug:slug>/api/tables/<int:table_id>/delete/', views.api_delete_table, name='api_delete_table'),
    path('<slug:slug>/api/tables/<int:table_id>/toggle/', views.api_toggle_table, name='api_toggle_table'),
    path('<slug:slug>/api/tables/<int:table_id>/qrcode/', views.api_generate_qrcode, name='api_generate_qrcode'),
    path('<slug:slug>/api/tables/generate-all-qrcodes/', views.api_generate_all_qrcodes, name='api_generate_all_qrcodes'),

    # ========================
    # APIs DE NOTIFICAÇÕES PUSH
    # ========================
    path('<slug:slug>/api/push/subscribe/', views.api_push_subscribe, name='api_push_subscribe'),
    path('<slug:slug>/api/push/subscriptions/count/', views.api_push_subscriptions_count, name='api_push_subscriptions_count'),
    path('<slug:slug>/api/push/send/', views.api_push_send, name='api_push_send'),
]

# Configuração para servir arquivos de mídia (imagens, logos, etc.)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
