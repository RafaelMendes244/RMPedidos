from django.contrib import admin
from .models import Tenant, Category, Product, Order, OrderItem, OperatingDay, DeliveryFee, Coupon, CouponUsage, ProductOption, OptionItem, Table


# --- Cadastros Básicos ---

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'custom_domain', 'phone_whatsapp', 'owner', 'is_open')
    search_fields = ('name', 'slug', 'custom_domain')
    list_filter = ('is_open', 'manual_override')
    autocomplete_fields = ['owner']

    fieldsets = (
        ('Informações Principais', {
            'fields': ('name', 'slug', 'custom_domain', 'owner')
        }),
        ('Imagens da Loja', {
            'fields': ('logo', 'background_image'),
            'classes': ('collapse',),
            'description': 'Carregue o logo e a imagem de fundo da sua loja. Essas imagens aparecerão na página do cardápio para os clientes.'
        }),
        ('Personalização Visual', {
            'fields': ('primary_color',),
            'classes': ('collapse',),
        }),
        ('Contato', {
            'fields': ('phone_whatsapp', 'address')
        }),
        ('Configurações do PIX', {
            'fields': ('pix_key', 'pix_name', 'pix_city'),
            'classes': ('collapse',)
        }),
        ('Status da Loja', {
            'fields': ('is_open', 'manual_override'),
            'description': 'Controle se a loja está aberta ou fechada. O fechamento manual impede abertura automática.'
        }),
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'order')
    list_filter = ('tenant',)
    ordering = ('tenant', 'order')


# Inline para Opções do Produto
class OptionItemInline(admin.TabularInline):
    model = OptionItem
    extra = 1


class ProductOptionInline(admin.TabularInline):
    model = ProductOption
    extra = 0
    show_change_link = True


@admin.register(ProductOption)
class ProductOptionAdmin(admin.ModelAdmin):
    list_display = ('title', 'product', 'type', 'required', 'max_quantity')
    list_filter = ('type', 'required')
    inlines = [OptionItemInline]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'original_price', 'tenant', 'is_available', 'badge')
    list_filter = ('tenant', 'category', 'is_available')
    search_fields = ('name', 'description')
    list_editable = ('price', 'is_available')
    inlines = [ProductOptionInline]

    fieldsets = (
        ('Informações Básicas', {
            'fields': ('name', 'description', 'tenant', 'category', 'is_available')
        }),
        ('Preços', {
            'fields': ('price', 'original_price', 'badge'),
            'description': 'O "Preço Original" aparece riscado. A "Badge" é uma etiqueta como "Promoção" ou "Mais Vendido".'
        }),
        ('Imagem do Produto', {
            'fields': ('image',),
            'description': 'Carregue uma imagem atrativa do produto. Isso ajuda os clientes a visualizar o pedido.'
        }),
    )


# --- Configuração dos Pedidos ---

# Isso faz os itens aparecerem DENTRO do pedido, não soltos
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0  # Não mostra linhas vazias extras
    readonly_fields = ('product_name', 'quantity', 'price', 'observation', 'options_text')  # Para não editar o histórico
    can_delete = False  # Para não apagar itens de um pedido feito


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    # Colunas que aparecem na lista
    list_display = ('id', 'tenant', 'customer_name', 'total_value', 'discount_value', 'coupon', 'status', 'is_printed', 'created_at')

    # Filtros laterais
    list_filter = ('tenant', 'status', 'created_at', 'payment_method', 'is_printed')

    # Campo de busca
    search_fields = ('customer_name', 'id', 'customer_phone')

    # Itens readonly (ninguém deve mudar o valor de um pedido passado)
    readonly_fields = ('created_at', 'total_value', 'delivery_fee', 'discount_value', 'coupon', 'payment_method', 'customer_name', 'customer_phone', 'address_cep', 'address_street', 'address_number', 'address_neighborhood', 'observation')

    # Conecta os itens aqui
    inlines = [OrderItemInline]

    # Ordena do mais recente para o mais antigo
    ordering = ('-created_at',)
    
    # Ações em lote
    actions = ['marcar_como_concluido', 'marcar_como_cancelado']
    
    @admin.action(description='Marcar selecionados como Concluído')
    def marcar_como_concluido(self, request, queryset):
        queryset.update(status='concluido')
    
    @admin.action(description='Marcar selecionados como Cancelado')
    def marcar_como_cancelado(self, request, queryset):
        queryset.update(status='cancelado')


@admin.register(OperatingDay)
class OperatingDayAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'get_day_display', 'open_time', 'close_time', 'is_closed')
    list_filter = ('tenant', 'day', 'is_closed')
    ordering = ('tenant', 'day')


@admin.register(DeliveryFee)
class DeliveryFeeAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'neighborhood', 'fee')
    list_filter = ('tenant',)
    search_fields = ('neighborhood',)
    list_editable = ('fee',)


# --- Gestão de Cupons ---


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ('code', 'tenant', 'discount_type', 'discount_value', 'usage_limit', 'used_count', 'valid_until', 'is_active')
    list_filter = ('tenant', 'discount_type', 'is_active')
    search_fields = ('code', 'description')
    ordering = ('-created_at',)
    list_editable = ('is_active',)

    fieldsets = (
        ('Informações do Cupom', {
            'fields': ('tenant', 'code', 'description')
        }),
        ('Desconto', {
            'fields': ('discount_type', 'discount_value', 'minimum_order_value')
        }),
        ('Validade e Limites', {
            'fields': ('valid_from', 'valid_until', 'usage_limit', 'used_count'),
            'description': 'Deixe as datas em branco para cupom sem prazo. Use 0 em limite para ilimitado.'
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
    )

    readonly_fields = ('used_count',)


@admin.register(CouponUsage)
class CouponUsageAdmin(admin.ModelAdmin):
    list_display = ('coupon', 'order', 'discount_applied', 'used_at')
    list_filter = ('coupon', 'used_at')
    search_fields = ('order__customer_name', 'coupon__code')
    readonly_fields = ('coupon', 'order', 'discount_applied', 'used_at')


# --- Gestão de Mesas ---

@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'number', 'capacity', 'is_active', 'created_at')
    list_filter = ('tenant', 'is_active')
    search_fields = ('tenant__name', 'number')
    list_editable = ('is_active',)
    ordering = ('tenant', 'number')
