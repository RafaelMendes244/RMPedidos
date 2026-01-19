from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

class Tenant(models.Model):
    # NOME DA LOJA E SUBDOMINIO
    name = models.CharField(max_length=100, verbose_name="Nome da Loja")
    slug = models.SlugField(unique=True, verbose_name="Link da Loja (Slug)", help_text="Ex: brasaburguer (será usado na URL)")

    # DOMINIO PERSONALIZADO
    custom_domain = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        unique=True, 
        verbose_name="Domínio Personalizado",
        help_text="Ex: pizzariadoze.com.br (sem http:// ou www)"
    )

    # --- SISTEMA DE PLANOS PROFISSIONAL ---
    PLAN_CHOICES = [
        ('starter', 'Plano Starter'),
        ('pro', 'Plano Pro'),
    ]
    
    plan_type = models.CharField(
        max_length=20, 
        choices=PLAN_CHOICES, 
        default='starter', 
        verbose_name="Tipo de Plano"
    )

    # Data de Criação (Editável conforme você pediu)
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Data de Criação")
    
    # NOVA CAMPO: Data de Vencimento
    valid_until = models.DateField(null=True, blank=True, verbose_name="Válido Até")
    
    # Kill Switch (Cancelamento manual)
    subscription_active = models.BooleanField(default=True, verbose_name="Assinatura Ativa?")

    def __str__(self):
        return self.name

    # VINCULO DE DONO
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tenants', verbose_name="Dono da Loja", null=True, blank=True)
    
    # Customização Visual
    primary_color = models.CharField(max_length=7, default="#ea580c", verbose_name="Cor Primária", help_text="Cor Hex. Ex: #FF0000")
    background_image = models.ImageField(upload_to='tenants_bg/', blank=True, null=True, verbose_name="Imagem de Fundo")
    logo = models.ImageField(upload_to='tenants_logo/', blank=True, null=True, verbose_name="Logotipo")

    # CONFIGURAÇOES DE PIX, HORARIO E LOCALIZAÇAO
    pix_key = models.CharField(max_length=100, blank=True, null=True, verbose_name="Chave PIX")
    pix_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Nome Titular PIX")
    pix_city = models.CharField(max_length=100, blank=True, null=True, verbose_name="Cidade PIX")
    
    # CONFIGURAÇÃO DE ENDEREÇO
    address = models.TextField(blank=True, null=True, verbose_name="Endereço Completo")

    # ABERTO/FECHADO
    is_open = models.BooleanField(default=True, verbose_name="Loja Aberta?")
    
    # Override Manual - Indica que o lojista fechou a loja manualmente
    # Quando True, a loja NÃO abre automaticamente (mesmo que o horário permita)
    manual_override = models.BooleanField(default=False, verbose_name="Fechamento Manual?")
    
    # TEMPO DE ENTREGA E ESPERA
    delivery_time = models.IntegerField(
        default=45, 
        verbose_name="Tempo de Entrega (minutos)",
        help_text="Tempo estimado para entrega em minutos. Ex: 45"
    )
    pickup_time = models.IntegerField(
        default=25, 
        verbose_name="Tempo de Retirada (minutos)",
        help_text="Tempo estimado para retirada em minutos. Ex: 25"
    )
    show_delivery_time = models.BooleanField(
        default=True, 
        verbose_name="Mostrar Tempo de Entrega?",
        help_text="Exibir o tempo de entrega estimado na página do cardápio"
    )
    show_pickup_time = models.BooleanField(
        default=True, 
        verbose_name="Mostrar Tempo de Retirada?",
        help_text="Exibir o tempo de retirada estimado na página do cardápio"
    )
    
    # Contato
    phone_whatsapp = models.CharField(max_length=20, verbose_name="WhatsApp", help_text="Apenas números com DDD")

    @property
    def is_trial(self):
        """Verifica se está nos 14 dias de teste grátis"""
        if not self.created_at: return False
        return (timezone.now() - self.created_at) < timedelta(days=14)

    @property
    def remaining_trial_days(self):
        if not self.is_trial: return 0
        delta = (self.created_at + timedelta(days=14)) - timezone.now()
        return delta.days
    
    @property
    def has_active_subscription(self):
        """
        Lógica Mestra:
        1. Se subscription_active for False -> Bloqueado (Cancelado)
        2. Se tiver data de validade futura -> Ativo (Pago)
        3. Se estiver no período de trial -> Ativo (Trial)
        4. Senão -> Bloqueado (Vencido)
        """
        if not self.subscription_active:
            return False
            
        # Se tem data de vencimento definida e é futura ou hoje
        if self.valid_until and self.valid_until >= timezone.now().date():
            return True
            
        # Se não tem vencimento ou já venceu, verifica o trial
        return self.is_trial

    # --- PERMISSÕES DE RECURSOS ---

    @property
    def can_access_orders(self):
        return self.has_active_subscription and (self.is_trial or self.plan_type == 'pro')

    @property
    def can_access_reports(self):
        return self.has_active_subscription and (self.is_trial or self.plan_type == 'pro')

    @property
    def can_access_coupons(self):
        return self.has_active_subscription and (self.is_trial or self.plan_type == 'pro')
    
    class Meta:
        verbose_name = "Loja"
        verbose_name_plural = "Lojas"

    def __str__(self):
        return self.name

class Category(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='categories', verbose_name="Loja")
    name = models.CharField(max_length=100, verbose_name="Nome da Categoria")
    order = models.IntegerField(default=0, verbose_name="Ordem", help_text="Ordem de exibição no cardápio")

    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        ordering = ['order', 'name']

    def __str__(self):
        return f"{self.name} - {self.tenant.name}"

class Product(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='products', verbose_name="Loja")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products', verbose_name="Categoria")
    name = models.CharField(max_length=200, verbose_name="Nome do Produto")
    description = models.TextField(blank=True, verbose_name="Descrição")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Preço")

    original_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, verbose_name="Preço Original (De)")
    badge = models.CharField(max_length=50, blank=True, null=True, verbose_name="Etiqueta (Ex: Mais Vendido)")

    image = models.ImageField(upload_to='products/', blank=True, null=True, verbose_name="Foto do Produto")
    is_available = models.BooleanField(default=True, verbose_name="Disponível?")

    class Meta:
        verbose_name = "Produto"
        verbose_name_plural = "Produtos"

    def __str__(self):
        return self.name

# Modelo de Mesa para pedidos no local
class Table(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='tables', verbose_name="Loja")
    number = models.IntegerField(verbose_name="Número da Mesa")
    capacity = models.IntegerField(default=4, verbose_name="Capacidade")
    is_active = models.BooleanField(default=True, verbose_name="Ativa?")
    qr_code = models.ImageField(upload_to='tables_qr/', blank=True, null=True, verbose_name="QR Code")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")

    class Meta:
        unique_together = ('tenant', 'number')
        ordering = ['number']
        verbose_name = "Mesa"
        verbose_name_plural = "Mesas"

    def __str__(self):
        return f"Mesa {self.number} - {self.tenant.name}"
    
    def get_qr_code_url(self):
        """Retorna a URL do QR Code ou None"""
        if self.qr_code:
            return self.qr_code.url
        return None

# Estrutura de Pedidos
class Order(models.Model):
    STATUS_CHOICES = [
        ('pendente', 'Pendente'),
        ('em_preparo', 'Em Preparo'),
        ('saiu_entrega', 'Saiu para Entrega'),
        ('concluido', 'Concluído'),
        ('cancelado', 'Cancelado'),
    ]

    ORDER_TYPE_CHOICES = [
        ('delivery', 'Delivery'),
        ('pickup', 'Retirada'),
        ('table', 'Mesa'),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='orders')
    customer_name = models.CharField(max_length=100, verbose_name="Nome do Cliente")
    customer_phone = models.CharField(max_length=20, verbose_name="Telefone")
    
    # Tipo de pedido (delivery, pickup, ou mesa)
    order_type = models.CharField(
        max_length=20, 
        choices=ORDER_TYPE_CHOICES, 
        default='delivery',
        verbose_name="Tipo de Pedido"
    )
    
    # Mesa vinculada (para pedidos no local)
    table = models.ForeignKey(
        Table, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='orders',
        verbose_name="Mesa"
    )
    
    # Endereço (pode ser null se for retirada ou mesa)
    address_cep = models.CharField(max_length=10, blank=True, null=True)
    address_street = models.CharField(max_length=200, blank=True, null=True)
    address_number = models.CharField(max_length=20, blank=True, null=True)
    address_neighborhood = models.CharField(max_length=100, blank=True, null=True)
    
    payment_method = models.CharField(max_length=50, verbose_name="Forma de Pagamento")
    total_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor Total")
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Taxa Entrega")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Desconto")
    coupon = models.ForeignKey('Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders', verbose_name="Cupom Usado")
    is_printed = models.BooleanField(default=False, verbose_name="Impresso?")
    observation = models.TextField(blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pendente')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data do Pedido")

    class Meta:
        verbose_name = "Pedido"
        verbose_name_plural = "Pedidos"
        ordering = ['-created_at'] # Mais recentes primeiro

    def __str__(self):
        table_info = f" - Mesa {self.table.number}" if self.table and self.order_type == 'table' else ""
        return f"Pedido #{self.id} - {self.customer_name}{table_info}"

class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product_name = models.CharField(max_length=200) # Salvamos o nome caso o produto seja deletado depois
    quantity = models.IntegerField(default=1)
    price = models.DecimalField(max_digits=10, decimal_places=2) # Preço no momento da compra
    observation = models.CharField(max_length=200, blank=True, null=True)
    options_text = models.TextField(blank=True, null=True, verbose_name="Opcionais Selecionados") # Ex: "Bacon, Queijo Extra"

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"
    
# CONFIGURAÇAO DE HORARIOS DE FUNCIONAMENTO
class OperatingDay(models.Model):
    DAYS = [
        (0, 'Domingo'), (1, 'Segunda'), (2, 'Terça'), (3, 'Quarta'),
        (4, 'Quinta'), (5, 'Sexta'), (6, 'Sábado')
    ]
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='operating_days')
    day = models.IntegerField(choices=DAYS)
    open_time = models.TimeField(null=True, blank=True, verbose_name="Abertura")
    close_time = models.TimeField(null=True, blank=True, verbose_name="Fechamento")
    is_closed = models.BooleanField(default=False, verbose_name="Fechado neste dia?")

    class Meta:
        unique_together = ('tenant', 'day') # Garante que só tenha 1 regra por dia
        ordering = ['day']

    def __str__(self):
        return f"{self.tenant.name} - {self.get_day_display()}"
    

# CONFIGURAÇAO DE TAXAS DE ENTREGA
class DeliveryFee(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='delivery_fees')
    neighborhood = models.CharField(max_length=100, verbose_name="Bairro")
    fee = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Taxa de Entrega")

    def __str__(self):
        return f"{self.neighborhood} - R$ {self.fee}"

    class Meta:
        # Garante que não haja bairros duplicados na mesma loja
        unique_together = ('tenant', 'neighborhood')

# GRUPOS REUTILIZÁVEIS DE ADICIONAIS
class ProductGroup(models.Model):
    """
    Agrupa um conjunto de opções (adicionais) que podem ser reutilizados em múltiplos produtos.
    Ex: Grupo "Adicionais de Carne" pode ser usado em Hambúrguer, Espetinho, Sanduíche, etc.
    """
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='product_groups')
    name = models.CharField(max_length=100, verbose_name="Nome do Grupo") # Ex: Adicionais de Carne
    type = models.CharField(
        max_length=20,
        choices=[
            ('radio', 'Escolha Única (Ex: Ponto da carne)'),
            ('checkbox', 'Múltipla Escolha (Ex: Adicionais)'),
        ],
        default='checkbox',
        verbose_name="Tipo de Seleção"
    )
    required = models.BooleanField(default=False, verbose_name="Obrigatório?")
    max_quantity = models.IntegerField(default=10, verbose_name="Máximo de itens", help_text="Para múltipla escolha")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('tenant', 'name')
        verbose_name = "Grupo de Produtos"
        verbose_name_plural = "Grupos de Produtos"
    
    def __str__(self):
        return f"{self.name} ({self.tenant.name})"

class GroupItem(models.Model):
    """Item individual dentro de um grupo reutilizável"""
    group = models.ForeignKey(ProductGroup, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=100, verbose_name="Nome da Opção") # Ex: Bacon
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Preço Adicional")
    
    class Meta:
        unique_together = ('group', 'name')
    
    def __str__(self):
        return f"{self.name} (+R${self.price})"

# CONFIGURAÇAO DE ADICIONAIS
class ProductOption(models.Model):
    TYPE_CHOICES = [
        ('radio', 'Escolha Única (Ex: Ponto da carne)'),
        ('checkbox', 'Múltipla Escolha (Ex: Adicionais)'),
    ]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='options')
    group = models.ForeignKey(ProductGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name='product_options', verbose_name="Grupo Origem")
    title = models.CharField(max_length=100, verbose_name="Título do Grupo") # Ex: Adicionais
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='checkbox')
    required = models.BooleanField(default=False, verbose_name="Obrigatório?")
    max_quantity = models.IntegerField(default=1, verbose_name="Máximo de itens", help_text="Para múltipla escolha")

    def __str__(self):
        return f"{self.product.name} - {self.title}"

class OptionItem(models.Model):
    option = models.ForeignKey(ProductOption, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=100, verbose_name="Nome da Opção") # Ex: Bacon
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Preço Adicional")


    def __str__(self):
        return f"{self.name} (+R${self.price})"


# CONFIGURAÇÃO DE CUPONS DE DESCONTO
class Coupon(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('percentage', 'Porcentagem (%)'),
        ('fixed', 'Valor Fixo (R$)'),
    ]

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='coupons', verbose_name="Loja")
    code = models.CharField(max_length=20, verbose_name="Código do Cupom", help_text="Ex: PRIMEIRACOMPRA10")
    description = models.CharField(max_length=200, blank=True, null=True, verbose_name="Descrição", help_text="Ex: 10% off na primeira compra")
    
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='percentage', verbose_name="Tipo de Desconto")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor do Desconto")
    
    minimum_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Valor Mínimo do Pedido", help_text="Pedidos abaixo deste valor não podem usar este cupom")
    
    usage_limit = models.IntegerField(default=0, verbose_name="Limite de Uso", help_text="0 = ilimitado")
    used_count = models.IntegerField(default=0, verbose_name="Vezes Usado")
    
    valid_from = models.DateTimeField(null=True, blank=True, verbose_name="Válido a partir de")
    valid_until = models.DateTimeField(null=True, blank=True, verbose_name="Válido até")
    
    is_active = models.BooleanField(default=True, verbose_name="Ativo?")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")

    class Meta:
        unique_together = ('tenant', 'code')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} - {self.tenant.name}"

    def is_valid(self):
        """Verifica se o cupom é válido para uso"""
        from django.utils import timezone
        now = timezone.now()
        
        # Verifica se está ativo
        if not self.is_active:
            return False, "Cupom desativado"
        
        # Verifica limite de uso
        if self.usage_limit > 0 and self.used_count >= self.usage_limit:
            return False, "Cupom atingiu limite de uso"
        
        # Verifica data de início
        if self.valid_from and now < self.valid_from:
            return False, "Cupom ainda não está válido"
        
        # Verifica data de expiração
        if self.valid_until and now > self.valid_until:
            return False, "Cupom expirado"
        
        return True, "Cupom válido"

    def apply_discount(self, order_value):
        """Aplica o desconto ao valor do pedido e retorna o valor final"""
        from decimal import Decimal
        
        # Converte order_value para Decimal para evitar erros de tipo
        order_value = Decimal(str(order_value))
        
        if self.discount_type == 'percentage':
            discount = order_value * (self.discount_value / Decimal('100'))
        else:
            discount = self.discount_value
        
        # Garante que o desconto não ultrapasse o valor do pedido
        discount = min(discount, order_value)
        final_value = order_value - discount
        
        return float(final_value), float(discount)


# Registro de uso de cupom
class CouponUsage(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name='usages', verbose_name="Cupom")
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='coupon_usages', verbose_name="Pedido")
    used_at = models.DateTimeField(auto_now_add=True, verbose_name="Usado em")
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Desconto Aplicado")

    class Meta:
        verbose_name = "Uso de Cupom"
        verbose_name_plural = "Usos de Cupom"

    def __str__(self):
        return f"{self.coupon.code} usado em Pedido #{self.order.id}"


# ========================
# NOTIFICAÇÕES PUSH
# ========================

class PushSubscription(models.Model):
    """
    Armazena as subscriptions de notificações push dos clientes.
    Cada subscription contém um endpoint único para envio de push notifications.
    """
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='push_subscriptions', verbose_name="Loja")
    endpoint = models.TextField(verbose_name="Endpoint")
    p256dh = models.CharField(max_length=200, verbose_name="Chave P256dh")
    auth = models.CharField(max_length=200, verbose_name="Chave Auth")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atualizado em", null=True, blank=True)
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    
    class Meta:
        verbose_name = "Subscription Push"
        verbose_name_plural = "Subscriptions Push"
        unique_together = ['tenant', 'endpoint']
    
    def __str__(self):
        return f"Subscription de {self.tenant.name} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"
    
    def to_json(self):
        """Converte para formato JSON do browser push manager"""
        return {
            'endpoint': self.endpoint,
            'keys': {
                'p256dh': self.p256dh,
                'auth': self.auth
            }
        }
