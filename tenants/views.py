from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse
from django.core.files.storage import default_storage
from django.db.models import Prefetch
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.utils.text import slugify 
from django.db import IntegrityError, transaction
from django.db.models import Sum, Prefetch, Count
from django.utils import timezone
from decimal import Decimal
import logging

from django_ratelimit.decorators import ratelimit

from .models import (
    Tenant, 
    Category, 
    Product, 
    Order, 
    OrderItem, 
    OperatingDay, 
    DeliveryFee,
    ProductOption,
    OptionItem,
    Coupon,
    CouponUsage
)

# CORRIGIDO: Usar logger ao invés de print
logger = logging.getLogger(__name__)

def is_store_open_by_hours(tenant):
    """
    Verifica se a loja está aberta baseado no horário de funcionamento.
    Suporta horários de madrugada (ex: abre 18:00, fecha 02:00).
    Retorna (True, None) se estiver aberta, (False, mensagem) se fechada.
    """
    from datetime import datetime
    
    now = datetime.now()
    current_minutes = now.hour * 60 + now.minute
    current_weekday = now.weekday()  # 0=Segunda, 6=Domingo
    
    # Converter Python weekday (0=Segunda...6=Domingo) para model (0=Domingo...6=Sábado)
    # Python: 0=Seg, 1=Ter, 2=Qua, 3=Qui, 4=Sex, 5=Sab, 6=Dom
    # Model:   0=Dom, 1=Seg, 2=Ter, 3=Qua, 4=Qui, 5=Sex, 6=Sab
    # Corrigido: Agora ambos usam 0=Domingo, 1=Segunda, etc.
    # Se Python é Segunda(0), o model também é Segunda(1)
    # Se Python é Domingo(6), o model é Domingo(0)
    # Então: model_day = (python_day + 1) % 7
    # python 0(seg) -> model 1(seg) ✓
    # python 1(ter) -> model 2(ter) ✓
    # python 6(dom) -> model 0(dom) ✓
    model_today = (current_weekday + 1) % 7
    
    def check_rule(rule):
        """Verifica se uma regra de horário permite a loja aberta"""
        if not rule:
            return None
        
        if rule.is_closed:
            return None
        
        if not rule.open_time or not rule.close_time:
            return None
        
        open_min = rule.open_time.hour * 60 + rule.open_time.minute
        close_min = rule.close_time.hour * 60 + rule.close_time.minute
        
        # Se close < open, é horário de madrugada (ex: 18:00 às 02:00)
        if close_min < open_min:
            # Está aberto se já passou do open OU ainda não passou do close
            return current_minutes >= open_min or current_minutes < close_min
        else:
            # Horário normal (ex: 08:00 às 18:00)
            return open_min <= current_minutes < close_min
    
    # 1. Verificar regra de HOJE no model
    today_rule = OperatingDay.objects.filter(tenant=tenant, day=model_today).first()
    
    if today_rule:
        today_check = check_rule(today_rule)
        if today_check is True:
            return (True, None)
    
    # 2. Se hoje está fechada ou fora do horário, verificar DIA ANTERIOR
    # Para cobrir casos de madrugada (ex: abre 18:00 ter, fecha 02:00 qua)
    model_yesterday = (current_weekday) % 7  # Dia anterior no model
    yesterday_rule = OperatingDay.objects.filter(tenant=tenant, day=model_yesterday).first()
    
    if yesterday_rule:
        yesterday_check = check_rule(yesterday_rule)
        if yesterday_check is True:
            return (True, None)
    
    # 3. Loja está fechada - gerar mensagem amigável
    if today_rule and not today_rule.is_closed and today_rule.open_time:
        open_min = today_rule.open_time.hour * 60 + today_rule.open_time.minute
        if current_minutes < open_min:
            opens_at = f"{today_rule.open_time.hour:02d}:{today_rule.open_time.minute:02d}"
            return (False, f"Abre às {opens_at}")
    
    return (False, "Fora do horário de funcionamento")


# ROTA INICIAL HOME
def home(request):

    if hasattr(request, 'tenant_from_domain') and request.tenant_from_domain:

        return cardapio_publico(request, request.tenant_from_domain.slug)

    if request.user.is_authenticated:
        user_tenant = Tenant.objects.filter(owner=request.user).first()
        if user_tenant:
            return redirect('painel_lojista', slug=user_tenant.slug)

    return render(request, 'tenants/landing.html')

def cardapio_publico(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)

    # 1. Carrega todos os horários para o JS
    db_days = OperatingDay.objects.filter(tenant=tenant).order_by('day')
    schedule_data = {}
    for d in db_days:
        schedule_data[d.day] = {
            'open': d.open_time.strftime('%H:%M') if d.open_time else '00:00',
            'close': d.close_time.strftime('%H:%M') if d.close_time else '00:00',
            'closed': d.is_closed
        }

    delivery_fees = list(tenant.delivery_fees.values('neighborhood', 'fee'))
    
    now = timezone.localtime(timezone.now())
    py_weekday = now.weekday() 
    
    today_index = 0 if py_weekday == 6 else py_weekday + 1
    
    day_today = OperatingDay.objects.filter(tenant=tenant, day=today_index).first()

    # 3. Produtos e Categorias
    produtos_ativos = Product.objects.filter(is_available=True)
    categories = Category.objects.filter(
        tenant=tenant,
        products__is_available=True 
    ).distinct().prefetch_related(
        Prefetch('products', queryset=produtos_ativos)
    ).order_by('order')

    # 4. Determinar se a loja está aberta
    # Se manual_override = True, está fechada (lojista fechou manualmente)
    # Caso contrário, usa o horário de funcionamento
    if tenant.manual_override:
        store_is_open = False
        store_closed_message = "Loja temporariamente fechada pelo lojista"
    else:
        is_open, message = is_store_open_by_hours(tenant)
        store_is_open = tenant.is_open and is_open
        store_closed_message = None if store_is_open else f"Fora do horário! {message or 'Volte mais tarde'}"

    context = {
        'tenant': tenant,
        'categories': categories,
        'schedule_json': json.dumps(schedule_data),
        'operating_days': db_days,
        'day_today': day_today,
        # Variáveis de status da loja
        'store_is_open': store_is_open,
        'store_closed_message': store_closed_message,
        'delivery_fees_json': json.dumps(delivery_fees, default=float)
    }
    
    return render(request, 'tenants/cardapio.html', context)

@login_required(login_url='/admin/login/') # Se não estiver logado, manda pro login do Django Admin
def painel_lojista(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # SEGURANÇA CRÍTICA: Verificar se o usuário logado é o dono da loja
    if tenant.owner != request.user:
        # Se não for o dono, verificar se ele possui alguma loja
        user_tenant = Tenant.objects.filter(owner=request.user).first()
        if user_tenant:
            # Redirecionar para a loja do usuário
            return redirect('painel_lojista', slug=user_tenant.slug)
        else:
            # Usuário sem lojas - fazer logout e mostrar erro
            logout(request)
            return render(request, 'tenants/login.html', {'error': 'Você não tem permissão para acessar esta loja.'})
    
    total_products = Product.objects.filter(tenant=tenant).count()
    
    # Carrega horários (converte para lista amigável pro JS)
    db_days = OperatingDay.objects.filter(tenant=tenant).order_by('day')
    schedule_data = {}
    for d in db_days:
        schedule_data[d.day] = {
            'open': d.open_time.strftime('%H:%M') if d.open_time else '',
            'close': d.close_time.strftime('%H:%M') if d.close_time else '',
            'closed': d.is_closed
        }

    context = {
        'tenant': tenant,
        'total_products': total_products,
        'schedule_json': json.dumps(schedule_data) # Manda como JSON string
    }
    return render(request, 'tenants/painel.html', context)

@transaction.atomic
@ratelimit(key='ip', rate='3/m', block=False)
def create_order(request, slug):

    was_limited = getattr(request, 'limited', False)
    if was_limited:
        return JsonResponse({
            'status': 'error',
            'message': 'Muitas tentativas! Aguarde um minuto para fazer outro pedido.'
        }, status=429)

    if request.method == 'POST':
        try:
            # 1. Identifica a loja
            tenant = get_object_or_404(Tenant, slug=slug)
            
            # Validações de Loja Aberta (Mantive sua lógica que é boa)
            if not tenant.is_open:
                return JsonResponse({'status': 'error', 'message': 'A loja está fechada temporariamente!'}, status=400)
            
            is_open, message = is_store_open_by_hours(tenant)
            if not is_open:
                return JsonResponse({'status': 'error', 'message': f'Fora do horário! {message}'}, status=400)
            
            data = json.loads(request.body)
            
            # --- INICIO DA BLINDAGEM DE PREÇO ---
            
            # A. Calcular total dos ITENS consultando o Banco de Dados
            cart_items = data.get('items', [])
            items_total = Decimal('0.00')
            order_items_objects = [] # Lista para salvar depois

            for item in cart_items:
                # Busca o produto original no banco para pegar o PREÇO REAL
                try:
                    product = Product.objects.get(id=item['id'], tenant=tenant)
                except Product.DoesNotExist:
                    return JsonResponse({'status': 'error', 'message': f"Produto ID {item['id']} não existe ou foi removido."}, status=400)
                
                # Valida disponibilidade
                if not product.is_available:
                    return JsonResponse({'status': 'error', 'message': f"O produto {product.name} acabou de ficar indisponível."}, status=400)

                # Preço base do produto
                item_price = product.price
                current_item_total = item_price
                
                # Opcionais: Validar e somar preços do banco
                options_list = item.get('options', [])
                valid_options_text = []
                
                for opt in options_list:
                    # O front manda {name: 'Bacon', price: 2.00}. Ignoramos o price do front.
                    # Buscamos no banco se existe essa opção para este produto
                    opt_name = opt.get('name')
                    
                    # Busca complexa: OpçãoItem -> ProductOption -> Product
                    # Garante que a opção pertence mesmo a esse produto
                    db_option_item = OptionItem.objects.filter(
                        name=opt_name, 
                        option__product=product
                    ).first()
                    
                    if db_option_item:
                        current_item_total += db_option_item.price
                        valid_options_text.append(db_option_item.name)
                    else:
                        # Se não achou no banco, pode ser um hack ou dado antigo. 
                        # Aqui decidimos se ignoramos ou barramos. Vamos ignorar o adicional hackeado.
                        pass

                quantity = int(item['qtd'])
                items_total += current_item_total * quantity
                
                # Prepara o objeto para salvar depois (Item do Pedido)
                order_items_objects.append({
                    'product_name': product.name,
                    'quantity': quantity,
                    'price': current_item_total, # Preço unitário real (base + opcionais)
                    'observation': item.get('obs', ''),
                    'options_text': ', '.join(valid_options_text)
                })

            # B. Calcular Taxa de Entrega (Consultando o Banco)
            delivery_fee = Decimal('0.00')
            neighborhood = data.get('address', {}).get('neighborhood')
            
            if neighborhood:
                # Busca exata ou 'iexact' (case insensitive)
                fee_obj = DeliveryFee.objects.filter(tenant=tenant, neighborhood__iexact=neighborhood).first()
                if fee_obj:
                    delivery_fee = fee_obj.fee
                # Se não achar, assume 0 (A combinar) ou barra. Mantive 0.

            # C. Calcular Cupom (Validar no Backend)
            discount_value = Decimal('0.00')
            coupon_code = data.get('coupon_code') # <--- O Front precisa mandar isso!
            applied_coupon = None

            if coupon_code:
                coupon = Coupon.objects.filter(tenant=tenant, code=coupon_code).first()
                if coupon:
                    is_valid, msg = coupon.is_valid()
                    if is_valid:
                        # Verifica mínimo
                        if coupon.minimum_order_value > 0 and items_total < coupon.minimum_order_value:
                            pass # Não aplica se não atingir minimo
                        else:
                            # Aplica desconto
                            final_val, discount_amt = coupon.apply_discount(items_total) # Calcula sobre produtos
                            discount_value = Decimal(str(discount_amt))
                            
                            # Incrementa uso do cupom
                            coupon.used_count += 1
                            coupon.save()
                            applied_coupon = coupon

            # D. TOTAL FINAL REAL
            final_total = items_total + delivery_fee - discount_value
            if final_total < 0: final_total = Decimal('0.00')

            # --- FIM DO CÁLCULO ---

            # Criação do Pedido
            order = Order.objects.create(
                tenant=tenant,
                customer_name=data.get('nome'),
                customer_phone=data.get('phone'),
                
                # VALORES BLINDADOS:
                total_value=final_total,
                delivery_fee=delivery_fee,
                discount_value=discount_value,
                coupon=applied_coupon,
                
                payment_method=data.get('method'),
                address_cep=data.get('address', {}).get('cep', ''),
                address_street=data.get('address', {}).get('street', ''),
                address_number=data.get('address', {}).get('number', ''),
                address_neighborhood=neighborhood,
                observation=data.get('obs', '')
            )
            
            # Cria os Itens (usando os dados validados)
            for item_obj in order_items_objects:
                OrderItem.objects.create(
                    order=order,
                    product_name=item_obj['product_name'],
                    quantity=item_obj['quantity'],
                    price=item_obj['price'],
                    observation=item_obj['observation'],
                    options_text=item_obj['options_text']
                )
                
            # Registro de uso do cupom (Tabela Link)
            if applied_coupon:
                CouponUsage.objects.create(
                    coupon=applied_coupon,
                    order=order,
                    discount_applied=discount_value
                )
            
            return JsonResponse({'status': 'success', 'order_id': order.id, 'real_total': float(final_total)})

        except Exception as e:
            # Em caso de erro, o transaction.atomic desfaz tudo
            logger.error(f"Erro ao criar pedido: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

    return JsonResponse({'status': 'error', 'message': 'Método inválido'}, status=400)

@login_required(login_url='/admin/login/')
def api_get_orders(request, slug):
    # Retorna os pedidos da loja (JSON) para o painel atualizar via AJAX
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    # Pega pedidos que NÃO estão cancelados (ou filtre como preferir)
    orders = Order.objects.filter(tenant=tenant).order_by('-created_at')[:20] # Pega os últimos 20
    
    data = []
    for order in orders:
        items = []
        for item in order.items.all():
            items.append({
                'name': item.product_name,
                'quantity': item.quantity,
                'price': float(item.price),
                'obs': item.observation,
                'options': item.options_text or ''
            })
            
        data.append({
            'id': order.id,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'total_value': float(order.total_value),
            'discount_value': float(order.discount_value) if order.discount_value else 0,
            'coupon_code': order.coupon.code if order.coupon else None,
            'status': order.status,
            'is_printed': order.is_printed,
            'payment_method': order.payment_method,
            'address': f"{order.address_street}, {order.address_number} - {order.address_neighborhood}" if order.address_street else "Retirada",
            'observation': order.observation,
            'created_at': timezone.localtime(order.created_at).strftime('%d/%m %H:%M'),
            'items': items
        })
        
    return JsonResponse({'orders': data})

@login_required(login_url='/admin/login/')
def api_update_order(request, slug, order_id):
    # Atualiza o status do pedido (Ex: Pendente -> Em Preparo)
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            new_status = data.get('status')
            
            order = Order.objects.get(id=order_id, tenant__slug=slug)
            order.status = new_status
            order.save()
            
            return JsonResponse({'status': 'success'})
        except Order.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Pedido não encontrado'}, status=404)
            
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='/admin/login/')
def api_mark_printed(request, slug, order_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            order = Order.objects.get(id=order_id, tenant__slug=slug)
            order.is_printed = True
            order.save()
            return JsonResponse({'status': 'success'})
        except Order.DoesNotExist:
            return JsonResponse({'status': 'error'}, status=404)
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='/admin/login/')
def api_update_settings(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Atualiza os campos
            tenant.pix_key = data.get('pix_key')
            tenant.pix_name = data.get('pix_name')
            tenant.pix_city = data.get('pix_city')
            tenant.address = data.get('address')
            
            # Se quiser permitir mudar cor também:
            if data.get('primary_color'):
                tenant.primary_color = data.get('primary_color')
                
            tenant.save()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            logger.error(f"Erro ao salvar configurações: {e}")
            return JsonResponse({'status': 'error', 'message': 'Erro ao salvar configurações'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

# --- APIs DE HISTORICO DO CLIENTE ---
def api_customer_history(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            order_ids = data.get('order_ids', [])
            
            # Busca pedidos que pertencem a esta loja e estão na lista de IDs
            orders = Order.objects.filter(
                tenant=tenant,
                id__in=order_ids
            ).prefetch_related('items').order_by('-created_at')
            
            history_data = []
            for order in orders:
                # Serializa itens
                items_str = []
                for item in order.items.all():
                    desc = f"{item.quantity}x {item.product_name}"
                    items_str.append(desc)
                
                history_data.append({
                    'id': order.id,
                    'status': order.get_status_display(), # Pega o texto bonito do status
                    'status_key': order.status, # Para usar cores no front
                    'total': float(order.total_value),
                    'date': timezone.localtime(order.created_at).strftime('%d/%m %H:%M'),
                    'items_summary': ', '.join(items_str),
                    'is_delivery': True if order.delivery_fee > 0 else False
                })
                
            return JsonResponse({'status': 'success', 'orders': history_data})
            
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error'}, status=400)

# --- APIs DE PRODUTOS (CRUD) ---

@login_required(login_url='/admin/login/')
def api_get_products(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    categories = Category.objects.filter(tenant=tenant).prefetch_related('products', 'products__options', 'products__options__items').order_by('order')
    
    data = []
    for cat in categories:
        products = []
        for prod in cat.products.all():
            # Serializa as opções
            options_list = []
            for opt in prod.options.all():
                items_list = []
                for item in opt.items.all():
                    items_list.append({
                        'id': item.id,
                        'name': item.name,
                        'price': float(item.price)
                    })
                options_list.append({
                    'id': opt.id,
                    'title': opt.title,
                    'type': opt.type,
                    'required': opt.required,
                    'max': opt.max_quantity,
                    'items': items_list
                })

            products.append({
                'id': prod.id,
                'name': prod.name,
                'description': prod.description,
                'price': float(prod.price),
                'original_price': float(prod.original_price) if prod.original_price else None,
                'badge': prod.badge,
                'image': prod.image.url if prod.image else '',
                'is_available': prod.is_available,
                'options': options_list # <--- ADICIONADO AQUI
            })
        
        data.append({
            'id': cat.id,
            'name': cat.name,
            'products': products
        })
        
    return JsonResponse({'categories': data})

@login_required(login_url='/admin/login/')
def api_save_product(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            # Dados básicos
            prod_id = request.POST.get('id')
            cat_input = request.POST.get('category') 
            name = request.POST.get('name')
            
            # Tratamento de preço (mantém sua lógica)
            price_str = request.POST.get('price', '0').replace('.', '').replace(',', '.') 
            if request.POST.get('price') and ',' in request.POST.get('price'):
                 price = request.POST.get('price').replace('.', '').replace(',', '.')
            else:
                 price = request.POST.get('price')

            original_price = None
            if request.POST.get('original_price'):
                original_price = request.POST.get('original_price').replace('.', '').replace(',', '.')

            badge = request.POST.get('badge', '')
            desc = request.POST.get('description', '')
            image = request.FILES.get('image')

            # Categoria
            category = None
            if cat_input and cat_input.isdigit():
                category = Category.objects.filter(id=cat_input, tenant=tenant).first()
            if not category and cat_input:
                category, created = Category.objects.get_or_create(
                    tenant=tenant, 
                    name__iexact=cat_input.strip(),
                    defaults={'name': cat_input.strip()}
                )

            # Salva/Cria Produto
            if prod_id:
                product = get_object_or_404(Product, id=prod_id, tenant=tenant)
                product.name = name
                product.price = price
                product.original_price = original_price
                product.badge = badge
                product.description = desc
                product.category = category
                
                # Tratamento de imagem
                clear_image = request.POST.get('clear_image', 'false') == 'true'
                if clear_image:
                    # Remove a imagem atual
                    if product.image:
                        product.image.delete(save=False)
                    product.image = None
                elif image:
                    # Nova imagem foi enviada, substitui a anterior
                    if product.image:
                        product.image.delete(save=False)
                    product.image = image
                product.save()
            else:
                product = Product.objects.create(
                    tenant=tenant,
                    category=category,
                    name=name,
                    price=price,
                    original_price=original_price,
                    badge=badge,
                    description=desc,
                    image=image,
                    is_available=True
                )
            
            # --- SALVAR AS OPÇÕES (ADICIONAIS) ---
            options_json = request.POST.get('options_json')
            if options_json:
                options_data = json.loads(options_json)
                
                # Estratégia simples: Apaga tudo antigo e cria novo (Sync)
                product.options.all().delete()
                
                for opt_data in options_data:
                    option = ProductOption.objects.create(
                        product=product,
                        title=opt_data['title'],
                        type=opt_data['type'],
                        required=opt_data['required'],
                        max_quantity=int(opt_data['max'] or 10)
                    )
                    for item_data in opt_data['items']:
                        OptionItem.objects.create(
                            option=option,
                            name=item_data['name'],
                            price=item_data['price']
                        )

            _limpar_categorias_vazias(tenant)
                
            return JsonResponse({'status': 'success'})
        except Exception as e:
            logger.error(f"Erro ao salvar produto: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='/admin/login/')
def api_delete_product(request, slug, product_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            product = get_object_or_404(Product, id=product_id, tenant=tenant)
            
            product.delete()
            
            _limpar_categorias_vazias(tenant)
            
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir produto'}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='/admin/login/')
def api_toggle_product(request, slug, product_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            product = get_object_or_404(Product, id=product_id, tenant__slug=slug)
            product.is_available = not product.is_available
            product.save()
            return JsonResponse({'status': 'success', 'new_state': product.is_available})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao alternar disponibilidade'}, status=400)
    return JsonResponse({'status': 'error'}, status=400)

def _limpar_categorias_vazias(tenant):
    """Remove categorias que não possuem produtos vinculados"""
    Category.objects.filter(tenant=tenant, products__isnull=True).delete()

# ROTAS DE LOGIN E LOGOUT
@ratelimit(key='ip', rate='5/m', block=False)
def custom_login(request):

    was_limited = getattr(request, 'limited', False)
    if was_limited:
        return render(request, 'login.html', {
            'error': 'Muitas tentativas de login. Aguarde 1 minuto.'
        })

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()
        passw = request.POST.get('password', '')
        
        # CORRIGIDO: Removido prints de debug - usando logger em vez disso
        logger.info(f"Tentativa de login para: {username}")
        
        user = authenticate(request, username=username, password=passw)
        
        if user is not None:
            login(request, user)
            logger.info(f"Login sucesso para usuário ID: {user.id}")
            
            # Busca TODAS as lojas do usuário e usa a mais recente
            user_tenants = Tenant.objects.filter(owner=user).order_by('-id')
            
            # Verificar quantas lojas o usuário tem
            tenant_count = user_tenants.count()
            
            if tenant_count == 0:
                # Usuário logado mas sem loja - criar uma ou direcionar para criação
                return redirect('signup')
            elif tenant_count == 1:
                # Apenas uma loja - direcionar diretamente
                return redirect('painel_lojista', slug=user_tenants.first().slug)
            else:
                # Múltiplas lojas - usar a mais recente
                return redirect('painel_lojista', slug=user_tenants.first().slug)
        else:
            logger.warning(f"Login falhou para: {username}")
            return render(request, 'tenants/login.html', {'error': 'Usuário ou senha incorretos'})

    return render(request, 'tenants/login.html')

def custom_logout(request):
    logout(request)
    return redirect('custom_login')

# ROTA PARA CRIAR EMPRESA
def signup(request):
    if request.method == 'POST':
        store_name = request.POST.get('store_name', '').strip()
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        
        # Validações Básicas
        if not store_name or len(store_name) < 3:
            return render(request, 'tenants/signup.html', {'error': 'Nome da loja deve ter pelo menos 3 caracteres.'})
            
        if not email or '@' not in email:
            return render(request, 'tenants/signup.html', {'error': 'Digite um email válido.'})
        
        # CORRIGIDO: Agora exige 8 caracteres (consistente com o frontend)
        if not password or len(password) < 8:
            return render(request, 'tenants/signup.html', {'error': 'A senha deve ter pelo menos 8 caracteres.'})
            
        # 1. Gerar Slug da Loja
        slug = slugify(store_name)
        
        if Tenant.objects.filter(slug=slug).exists():
            return render(request, 'tenants/signup.html', {'error': 'Essa loja já existe. Tente outro nome.'})
            
        if User.objects.filter(username=email).exists():
            return render(request, 'tenants/signup.html', {'error': 'Este email já está cadastrado.'})

        try:
            # 2. Criar Usuário
            user = User.objects.create_user(username=email, email=email, password=password)
            logger.info(f"Novo usuário criado: ID {user.id}")
            
            # 3. Criar a Loja (Tenant) vinculada
            tenant = Tenant.objects.create(
                owner=user,
                name=store_name,
                slug=slug,
                primary_color='#ea580c' # Cor laranja padrão
            )
            logger.info(f"Nova loja criada: {tenant.name} (slug: {tenant.slug})")
            
            # 4. Criar Categorias de Exemplo
            Category.objects.create(tenant=tenant, name="Lanches", order=1)
            Category.objects.create(tenant=tenant, name="Bebidas", order=2)

            # 5. Logar e Redirecionar
            login(request, user)
            return redirect('painel_lojista', slug=tenant.slug)

        except Exception as e:
            logger.error(f"Erro ao criar conta: {e}")
            return render(request, 'tenants/signup.html', {'error': 'Erro ao criar conta. Tente novamente.'})

    return render(request, 'tenants/signup.html')

# --- API FINANCEIRO E HISTÓRICO ---
@login_required(login_url='/admin/login/')
def api_get_financials(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    today = timezone.now().date()
    
    # 1. Resumo do Dia
    sales_today = Order.objects.filter(
        tenant=tenant, 
        status='concluido', 
        created_at__date=today
    ).aggregate(total=Sum('total_value'))['total'] or 0.00
    
    count_today = Order.objects.filter(
        tenant=tenant, 
        status='concluido', 
        created_at__date=today
    ).count()

    # 2. Histórico (Últimos 50 pedidos concluídos ou cancelados)
    history_orders = Order.objects.filter(
        tenant=tenant,
        status__in=['concluido', 'cancelado']
    ).order_by('-created_at')[:50]
    
    history_data = []
    for order in history_orders:
        history_data.append({
            'id': order.id,
            'customer': order.customer_name,
            'total': float(order.total_value),
            'status': order.status,
            'date': order.created_at.strftime('%Y-%m-%d'),
            'date_display': order.created_at.strftime('%d/%m %H:%M'),
            'payment': order.payment_method or ''
        })

    return JsonResponse({
        'sales_today': float(sales_today),
        'count_today': count_today,
        'history': history_data
    })

# --- API ABRIR/FECHAR LOJA ---
@login_required(login_url='/admin/login/')
def api_toggle_store_open(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            is_open = data.get('is_open')
            
            tenant.is_open = is_open
            
            if not is_open:
                tenant.manual_override = True
            else:
                tenant.manual_override = False
            
            tenant.save()
            return JsonResponse({'status': 'success', 'is_open': tenant.is_open})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao atualizar status da loja'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

# --- API SINCRONIZAR STATUS COM HORÁRIOS ---
@login_required(login_url='/admin/login/')
def api_sync_store_status(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            if tenant.manual_override:
                return JsonResponse({
                    'status': 'success', 
                    'is_open': False,
                    'reason': 'fechamento_manual'
                })
            
            # Se manual_override = False, permite atualização automática baseada no horário
            is_open, message = is_store_open_by_hours(tenant)
            
            # Atualiza o status se necessário
            if is_open != tenant.is_open:
                tenant.is_open = is_open
                tenant.save()
            
            return JsonResponse({
                'status': 'success',
                'is_open': is_open,
                'reason': message or 'horario_funcionamento'
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao sincronizar status'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

# ROTA PARA HORARIO DE FUNCIONAMENTO/FOLGAS
@login_required(login_url='/admin/login/')
def api_save_hours(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body) # Recebe lista: [{day:0, open:'18:00', close:'23:00', closed:false}, ...]
            
            for item in data:
                OperatingDay.objects.update_or_create(
                    tenant=tenant,
                    day=int(item['day']),
                    defaults={
                        'open_time': item['open'] if item['open'] else None,
                        'close_time': item['close'] if item['close'] else None,
                        'is_closed': item['closed']
                    }
                )
            
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao salvar horários'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

# ROTA PARA TAXAS DE ENTREGAS
@login_required(login_url='/admin/login/')
def api_delivery_fees(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    # GET: Retorna lista de taxas
    if request.method == 'GET':
        fees = list(tenant.delivery_fees.values('id', 'neighborhood', 'fee'))
        return JsonResponse({'fees': fees})

    # POST: Salva uma nova taxa
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            neighborhood = data.get('neighborhood', '').strip()
            fee = data.get('fee')

            if not neighborhood or fee is None:
                return JsonResponse({'status': 'error', 'message': 'Dados inválidos'}, status=400)

            # Usa update_or_create para evitar duplicatas (atualiza se já existir)
            DeliveryFee.objects.update_or_create(
                tenant=tenant,
                neighborhood__iexact=neighborhood, # Busca ignorando maiuscula/minuscula
                defaults={'neighborhood': neighborhood, 'fee': fee}
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao salvar taxa de entrega'}, status=500)

    return JsonResponse({'status': 'error'}, status=400)

@login_required(login_url='/admin/login/')
def api_delete_delivery_fee(request, slug, fee_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            DeliveryFee.objects.filter(id=fee_id, tenant=tenant).delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir taxa'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)


# ========================
# API DE CUPONS DE DESCONTO
# ========================

@login_required(login_url='/admin/login/')
def api_coupons(request, slug):
    """
    GET: Lista todos os cupons da loja
    POST: Cria um novo cupom
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    # GET: Lista cupons
    if request.method == 'GET':
        coupons = tenant.coupons.annotate(
            usage_count=Count('usages')
        ).values(
            'id', 'code', 'description', 'discount_type', 'discount_value',
            'minimum_order_value', 'usage_limit', 'used_count',
            'valid_from', 'valid_until', 'is_active'
        )
        return JsonResponse({'coupons': list(coupons)})
    
    # POST: Cria novo cupom
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validação básica
            code = data.get('code', '').strip().upper()
            if not code:
                return JsonResponse({'status': 'error', 'message': 'Código do cupom é obrigatório'}, status=400)
            
            discount_type = data.get('discount_type', 'percentage')
            try:
                discount_value = float(data.get('discount_value', 0))
                if discount_value <= 0:
                    return JsonResponse({'status': 'error', 'message': 'Valor do desconto deve ser maior que 0'}, status=400)
            except:
                return JsonResponse({'status': 'error', 'message': 'Valor do desconto inválido'}, status=400)
            
            minimum_order = float(data.get('minimum_order_value', 0))
            usage_limit = int(data.get('usage_limit', 0))
            
            # Validações específicas por tipo
            if discount_type == 'percentage' and discount_value > 100:
                return JsonResponse({'status': 'error', 'message': 'Porcentagem não pode ser maior que 100%'}, status=400)
            
            # Cria o cupom
            coupon = Coupon.objects.create(
                tenant=tenant,
                code=code,
                description=data.get('description', ''),
                discount_type=discount_type,
                discount_value=discount_value,
                minimum_order_value=minimum_order,
                usage_limit=usage_limit,
                valid_from=data.get('valid_from'),
                valid_until=data.get('valid_until'),
                is_active=data.get('is_active', True)
            )
            
            return JsonResponse({
                'status': 'success',
                'coupon': {
                    'id': coupon.id,
                    'code': coupon.code,
                    'description': coupon.description,
                    'discount_type': coupon.discount_type,
                    'discount_value': float(coupon.discount_value),
                    'minimum_order_value': float(coupon.minimum_order_value),
                    'usage_limit': coupon.usage_limit,
                    'used_count': coupon.used_count,
                    'valid_from': coupon.valid_from,
                    'valid_until': coupon.valid_until,
                    'is_active': coupon.is_active
                }
            })
        except IntegrityError:
            return JsonResponse({'status': 'error', 'message': 'Já existe um cupom com este código'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


@login_required(login_url='/admin/login/')
def api_coupon_details(request, slug, coupon_id):
    """
    GET: Retorna detalhes de um cupom específico
    PUT: Atualiza um cupom
    DELETE: Exclui um cupom
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    coupon = get_object_or_404(Coupon, id=coupon_id, tenant=tenant)
    
    # GET: Retorna detalhes do cupom
    if request.method == 'GET':
        usages = list(coupon.usages.select_related('order').values(
            'id', 'order__id', 'discount_applied', 'used_at'
        ))
        return JsonResponse({
            'coupon': {
                'id': coupon.id,
                'code': coupon.code,
                'description': coupon.description,
                'discount_type': coupon.discount_type,
                'discount_value': float(coupon.discount_value),
                'minimum_order_value': float(coupon.minimum_order_value),
                'usage_limit': coupon.usage_limit,
                'used_count': coupon.used_count,
                'valid_from': coupon.valid_from,
                'valid_until': coupon.valid_until,
                'is_active': coupon.is_active,
                'created_at': coupon.created_at
            },
            'usages': usages
        })
    
    # PUT: Atualiza o cupom
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            
            coupon.code = data.get('code', coupon.code).strip().upper()
            coupon.description = data.get('description', coupon.description)
            coupon.discount_type = data.get('discount_type', coupon.discount_type)
            
            if 'discount_value' in data:
                discount_value = float(data['discount_value'])
                if coupon.discount_type == 'percentage' and discount_value > 100:
                    return JsonResponse({'status': 'error', 'message': 'Porcentagem não pode ser maior que 100%'}, status=400)
                coupon.discount_value = discount_value
            
            coupon.minimum_order_value = data.get('minimum_order_value', coupon.minimum_order_value)
            coupon.usage_limit = data.get('usage_limit', coupon.usage_limit)
            coupon.valid_from = data.get('valid_from', coupon.valid_from)
            coupon.valid_until = data.get('valid_until', coupon.valid_until)
            coupon.is_active = data.get('is_active', coupon.is_active)
            
            coupon.save()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    # DELETE: Exclui o cupom
    if request.method == 'DELETE':
        try:
            coupon.delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir cupom'}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


def api_validate_coupon(request, slug):
    """
    Valida um cupom durante o checkout (público)
    POST: Recebe o código do cupom e o valor do pedido
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            code = data.get('code', '').strip().upper()
            order_value = float(data.get('order_value', 0))
            
            if not code:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'Código do cupom é obrigatório'
                }, status=400)
            
            # Busca o cupom
            coupon = Coupon.objects.filter(
                tenant=tenant,
                code=code
            ).first()
            
            if not coupon:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Cupom não encontrado'
                })
            
            # Valida o cupom
            is_valid, message = coupon.is_valid()
            if not is_valid:
                return JsonResponse({
                    'status': 'error',
                    'message': message
                })
            
            # Verifica valor mínimo
            if coupon.minimum_order_value > 0 and order_value < float(coupon.minimum_order_value):
                return JsonResponse({
                    'status': 'error',
                    'message': f'Valor mínimo do pedido é R$ {float(coupon.minimum_order_value):.2f}'
                })
            
            # Calcula o desconto
            final_value, discount = coupon.apply_discount(order_value)
            
            return JsonResponse({
                'status': 'success',
                'coupon': {
                    'code': coupon.code,
                    'description': coupon.description,
                    'discount_type': coupon.discount_type,
                    'discount_value': float(coupon.discount_value),
                    'discount_amount': discount,
                    'final_value': final_value
                }
            })
            
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': 'Erro ao validar cupom'
            }, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


# --- PÁGINAS LEGAIS (Termos de Uso e Política de Privacidade) ---

def termos_de_uso(request):
    """
    Página de Termos de Uso
    """
    context = {
        'tenant': {
            'name': 'RM Pedidos',
            'phone_whatsapp': '(83) 98855-3366',
        }
    }
    return render(request, 'tenants/termos.html', context)


def politica_privacidade(request):
    """
    Página de Política de Privacidade
    """
    context = {
        'tenant': {
            'name': 'RM Pedidos',
            'phone_whatsapp': '(83) 98855-3366',
            'address': '',
        }
    }
    return render(request, 'tenants/privacidade.html', context)