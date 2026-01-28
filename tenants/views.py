from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
import json
from django.http import JsonResponse
from django.core.files.storage import default_storage
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.utils.text import slugify 
from django.db.models import Prefetch
from django.db import IntegrityError, transaction
from django.db.models import Sum, Prefetch, Count, Q
from django.utils import timezone
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
import logging
import qrcode
import base64
from io import BytesIO

from django.conf import settings
from pywebpush import webpush

from django_ratelimit.decorators import ratelimit
from django.views.decorators.cache import never_cache

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
    ProductGroup,
    GroupItem,
    Coupon,
    CouponUsage,
    Table,
)

from .validators import validate_cep, validate_phone, validate_order_data

# CORRIGIDO: Usar logger ao invés de print
logger = logging.getLogger(__name__)

def normalizar_texto(texto):
    """
    Normaliza uma string para comparação de bairros.
    Remove acentos, converte para maiúsculas e remove espaços extras.
    Ex: 'São José' -> 'SAO JOSE'
    """
    import unicodedata
    if not texto:
        return ''
    # Normalizaunicode para remover acentos
    texto_normalizado = unicodedata.normalize('NFD', texto)
    # Remove os diacríticos (acentos)
    texto_sem_acentos = ''.join(c for c in texto_normalizado if not unicodedata.combining(c))
    # Converte para maiúsculas e remove espaços extras
    return texto_sem_acentos.upper().strip()

def send_push_notification(order, tenant, custom_title=None, custom_body=None):
    from .models import PushSubscription
    
    try:
        VAPID_PRIVATE_KEY = getattr(settings, 'VAPID_PRIVATE_KEY', None)
        VAPID_CLAIM_EMAIL = getattr(settings, 'VAPID_CLAIM_EMAIL', 'mailto:admin@admin.com')
        
        if not VAPID_PRIVATE_KEY:
            return {'success': False, 'error': 'VAPID key not configured'}

        # Lógica de Mensagem
        subscriptions = []
        
        if custom_body:
            # Notificação Manual (Promoção/Aviso) -> Envia para TODOS
            title = custom_title or tenant.name
            body = custom_body
            url = f"/{tenant.slug}/"
            subscriptions = PushSubscription.objects.filter(tenant=tenant, is_active=True)
            
        elif order:
            # Notificação de Pedido -> Envia APENAS para o CLIENTE ESPECÍFICO
            title = f"🔔 Atualização do Pedido #{order.id}"
            url = f"/{tenant.slug}/meus-pedidos/"
            
            if order.status == 'saiu_entrega':
                body = f"🏍️ Seu pedido saiu para entrega! Acompanhe."
            else:
                body = f"Status atualizado para: {order.get_status_display()}"
            
            # CORREÇÃO: Filtrar pelo telefone
            if order.customer_phone:
                # Garante que só temos números para comparar
                target_phone = ''.join(filter(str.isdigit, order.customer_phone))
                
                subscriptions = PushSubscription.objects.filter(
                    tenant=tenant, 
                    is_active=True, 
                    customer_phone=target_phone
                )
                
                if not subscriptions.exists():
                    logger.warning(f"[PUSH] Nenhuma inscrição encontrada para o telefone {target_phone}")
                    return {'success': False, 'error': 'Cliente não inscrito no push'}
            else:
                logger.warning("[PUSH] Pedido sem telefone, impossível notificar.")
                return {'success': False, 'error': 'Pedido sem telefone'}
        
        else:
            return {'success': False, 'error': 'Sem contexto'}

        # Envio
        sent_count = 0
        icon_url = tenant.logo.url if tenant.logo else '/static/img/icon-192.svg'
        
        for sub in subscriptions:
            try:
                webpush(
                    subscription_info=sub.to_json(),
                    data=json.dumps({
                        'title': title,
                        'body': body,
                        'icon': icon_url,
                        'url': url
                    }),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims={'sub': VAPID_CLAIM_EMAIL}
                )
                sent_count += 1
            except Exception as e:
                if '410' in str(e) or 'not found' in str(e).lower():
                    sub.is_active = False # Marca como inativo se falhar permanentemente
                    sub.save()
                logger.error(f"[PUSH] Erro individual: {e}")

        logger.info(f"[PUSH] Enviado para {sent_count} dispositivos.")
        return {'success': True, 'sent': sent_count}

    except Exception as e:
        logger.error(f"[PUSH] Erro crítico: {e}")
        return {'success': False, 'error': str(e)}

def is_store_open_by_hours(tenant):
    """
    Verifica se a loja está aberto baseado no horário de funcionamento.
    Retorna (True, 'ABERTO - Fecha as XX:XX') se aberto,
    (False, 'FECHADO HOJE') se fechado hoje,
    (False, 'FECHADO AGORA - ABRE AS XX:XX') se fora do horário mas abre hoje,
    (False, 'FECHADO HOJE') se não abre hoje
    """
    from datetime import datetime
    from django.utils import timezone
    
    # Usar timezone.localtime para garantir horário do Brasil
    now = timezone.localtime(timezone.now())
    current_minutes = now.hour * 60 + now.minute
    current_weekday = now.weekday()  # 0=Segunda, 6=Domingo
    
    # Converter Python weekday (0=Segunda...6=Domingo) para model (0=Domingo...6=Sábado)
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
        if today_rule.is_closed:
            # HOJE está marcado como fechado
            return (False, 'FECHADO HOJE')
        
        today_check = check_rule(today_rule)
        if today_check is True:
            # Está aberto, retorna mensagem com horário de fechamento
            close_time = today_rule.close_time.strftime('%H:%M') if today_rule.close_time else '23:59'
            return (True, f'ABERTO - Fecha as {close_time}')
        
        # HOJE tem horário definido mas está FORA do horário
        if today_rule.open_time and today_rule.close_time:
            # Verificar se ainda não chegou na hora de abrir
            open_min = today_rule.open_time.hour * 60 + today_rule.open_time.minute
            if current_minutes < open_min:
                return (False, f'FECHADO AGORA - ABRE AS {today_rule.open_time.strftime("%H:%M")}')
            else:
                # Passou do horário de fechamento
                return (False, 'FECHADO HOJE')
    
    # Se não tem configuração para hoje, procurar quando abre
    # 2. Procurar próximo dia que abre
    for i in range(1, 7):  # Verificar próximos 6 dias
        next_day = (model_today + i) % 7
        next_rule = OperatingDay.objects.filter(tenant=tenant, day=next_day).first()
        
        if next_rule and not next_rule.is_closed and next_rule.open_time:
            # Encontrou um dia que abre
            day_names = ['Domingo', 'Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado']
            # Converter Python weekday (0=Segunda...6=Domingo) para array (0=Domingo...6=Sábado)
            model_weekday = (now.weekday() + 1) % 7
            day_name = day_names[(model_weekday + i) % 7]
            return (False, f'FECHADO - ABRE {day_name} às {next_rule.open_time.strftime("%H:%M")}')
    
    # Se não encontrou nenhum dia configurado
    return (False, 'FECHADO HOJE')




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
    ).prefetch_related(
        Prefetch('products', queryset=produtos_ativos)
    ).distinct().order_by('order')

    # 4. Determinar se a loja está aberta (NOVA LÓGICA PROFISSIONAL)
    # Padrão: SEMPRE ABERTA. Só fecha se:
    # (a) Não for hora de abrir (por horário) OU
    # (b) Dono ativou fechamento manual (manual_override)
    
    store_is_open = True  # Por padrão: sempre aberta
    store_closed_message = None
    
    is_open_by_hours, status = is_store_open_by_hours(tenant)
    
    if not is_open_by_hours:
        store_is_open = False
        if status == 'CLOSED_TODAY':
            store_closed_message = "Fechado HOJE"
        elif status == 'SCHEDULE_CLOSED_TIME':
            today_rule = OperatingDay.objects.filter(tenant=tenant, day=today_index).first()
            if today_rule and today_rule.open_time:
                opens_at = f"{today_rule.open_time.hour:02d}:{today_rule.open_time.minute:02d}"
                store_closed_message = f"Fechado - Abre às {opens_at}"
            else:
                store_closed_message = "Fechado - Abre amanhã"
        else:
            store_closed_message = "Fechado - Abre amanhã"
    
    if tenant.manual_override:
        store_is_open = False
        store_closed_message = "FECHADO TEMPORARIAMENTE"

    context = {
        'tenant': tenant,
        'categories': categories,
        'schedule_json': json.dumps(schedule_data),
        'operating_days': db_days,
        'day_today': day_today,
        # Variáveis de status da loja
        'store_is_open': store_is_open,
        'store_closed_message': store_closed_message,
        'delivery_fees_json': json.dumps(delivery_fees, default=float),
        # Flag para identificar que não é pedido de mesa
        'is_table_order': False,
        'table': None,
        # VAPID Public Key para notificações push (injetada do settings.py)
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', '')
    }
    
    return render(request, 'tenants/cardapio.html', context)


# ========================
# NOVA ROTA: CARDÁPIO POR MESA (QR CODE)
# ========================
def cardapio_mesa(request, slug, table_number):
    """
    Cardápio específico para pedido na mesa.
    O cliente escaneia o QR code da mesa e vai direto para esta página.
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Validar mesa com mensagem customizada
    table = Table.objects.filter(
        tenant=tenant, 
        number=table_number,
        is_active=True
    ).first()
    
    if not table:
        # Retornar página customizada com erro
        context = {
            'tenant': tenant,
            'error': f'Mesa {table_number} não encontrada ou inativa',
            'table_number': table_number
        }
        return render(request, 'tenants/error_table.html', context, status=404)
    
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
    ).prefetch_related(
        Prefetch('products', queryset=produtos_ativos)
    ).distinct().order_by('order')

    # 4. Determinar se a loja está aberta (NOVA LÓGICA PROFISSIONAL)
    # Padrão: SEMPRE ABERTA. Só fecha se:
    # (a) Não for hora de abrir (por horário) OU
    # (b) Dono ativou fechamento manual (manual_override)
    
    store_is_open = True  # Por padrão: sempre aberta
    store_closed_message = None
    
    is_open_by_hours, status = is_store_open_by_hours(tenant)
    
    if not is_open_by_hours:
        store_is_open = False
        if status == 'CLOSED_TODAY':
            store_closed_message = "Fechado HOJE"
        elif status == 'SCHEDULE_CLOSED_TIME':
            today_rule = OperatingDay.objects.filter(tenant=tenant, day=today_index).first()
            if today_rule and today_rule.open_time:
                opens_at = f"{today_rule.open_time.hour:02d}:{today_rule.open_time.minute:02d}"
                store_closed_message = f"Fechado - Abre às {opens_at}"
            else:
                store_closed_message = "Fechado - Abre amanhã"
        else:
            store_closed_message = "Fechado - Abre amanhã"
    
    if tenant.manual_override:
        store_is_open = False
        store_closed_message = "FECHADO TEMPORARIAMENTE"

    context = {
        'tenant': tenant,
        'categories': categories,
        'schedule_json': json.dumps(schedule_data),
        'operating_days': db_days,
        'day_today': day_today,
        # Variáveis de status da loja
        'store_is_open': store_is_open,
        'store_closed_message': store_closed_message,
        'delivery_fees_json': json.dumps(delivery_fees, default=float),
        # Flag para identificar que é pedido de mesa
        'is_table_order': True,
        'table': table,
        # VAPID Public Key para notificações push (injetada do settings.py)
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', '')
    }
    
    return render(request, 'tenants/cardapio.html', context)


@never_cache
@login_required
def painel_lojista(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # SEGURANÇA CRÍTICA: Verificar se o usuário logado é o dono da loja
    if tenant.owner != request.user and not request.user.is_superuser:
        # Se não for o dono, verificar se ele possui alguma loja
        user_tenant = Tenant.objects.filter(owner=request.user).first()
        if user_tenant:
            # Redirecionar para a loja do usuário
            return redirect('painel_lojista', slug=user_tenant.slug)
        else:
            # Usuário sem lojas - fazer logout e mostrar erro
            logout(request)
            return render(request, 'tenants/login.html', {'error': 'Você não tem permissão para acessar esta loja.'})

    if not tenant.has_active_subscription:
        return render(request, 'tenants/plan_expired.html', {'tenant': tenant})
    
    total_products = Product.objects.filter(tenant=tenant).count()
    total_tables = Table.objects.filter(tenant=tenant).count()
    
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
        'total_tables': total_tables,
        'schedule_json': json.dumps(schedule_data),

        'allow_scheduling': tenant.allow_scheduling,

        # FLAGS PARA O FRONTEND
        'is_trial': tenant.is_trial,
        'trial_days': tenant.remaining_trial_days,
        'can_access_orders': tenant.can_access_orders,
        'can_access_reports': tenant.can_access_reports,
        'can_access_coupons': tenant.can_access_coupons,
        'can_access_push': tenant.can_access_push,
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
            
            # Validações de Loja Aberta (Mantida sua lógica que é boa)
            if not tenant.is_open:
                return JsonResponse({'status': 'error', 'message': 'A loja está fechada temporariamente!'}, status=400)
            
            is_open, message = is_store_open_by_hours(tenant)
            if not is_open:
                return JsonResponse({'status': 'error', 'message': f'Fora do horário! {message}'}, status=400)
            
            data = json.loads(request.body)

            # --- AGENDAMENTO DE PEDIDO ---
            is_scheduled = data.get('is_scheduled', False)
            scheduled_date = data.get('scheduled_date')
            scheduled_time = data.get('scheduled_time')

            if not tenant.allow_scheduling or data.get('table_number'):
                is_scheduled = False

            if is_scheduled:
                # 1. Validação de Preenchimento
                if not scheduled_date or not scheduled_time:
                    return JsonResponse({'status': 'error', 'message': 'Data e hora são obrigatórias para agendamento.'}, status=400)
                
                # 2. Validação de Data Futura
                try:
                    from datetime import datetime
                    # Combina data e hora strings em um objeto datetime
                    agendamento_dt = datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M")
                    agendamento_dt = timezone.make_aware(agendamento_dt) # Torna consciente do fuso horário
                    
                    if agendamento_dt < timezone.now():
                         return JsonResponse({'status': 'error', 'message': 'O agendamento não pode ser no passado.'}, status=400)
                         
                except ValueError:
                    return JsonResponse({'status': 'error', 'message': 'Formato de data/hora inválido.'}, status=400)
            else:
                # Se não for agendado, garante que os campos fiquem vazios no banco
                scheduled_date = None
                scheduled_time = None
            
            # Validação básica de dados
            if not data:
                return JsonResponse({'status': 'error', 'message': 'Dados vazios'}, status=400)
            # DETECTAR TIPO DE PEDIDO (NOVO)
            # ========================
            table_number = data.get('table_number')
            order_type = data.get('order_type', 'delivery')
            
            # Se table_number foi informado, força o tipo como mesa
            if table_number:
                order_type = 'table'
            
            # Buscar mesa se for pedido de mesa
            table = None
            if order_type == 'table' and table_number:
                table = Table.objects.filter(
                    tenant=tenant,
                    number=table_number,
                    is_active=True
                ).first()
                
                if not table:
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Mesa não encontrada ou inativa. Peça atendimento.'
                    }, status=400)
            
            # --- VALIDAÇÃO DE DADOS (NOVO) ---
            # Validar dados do cliente
            nome = data.get('nome', '').strip()
            phone = data.get('phone', '').strip()
            
            # SANITIZAÇÃO: remover caracteres perigosos do nome
            nome = ''.join(c for c in nome if c.isalnum() or c.isspace() or c in '-,.\'')
            
            if not nome or len(nome) < 2:
                return JsonResponse({'status': 'error', 'message': 'Nome deve ter no mínimo 2 caracteres.'}, status=400)
            
            try:
                phone_clean = validate_phone(phone)
            except ValidationError as e:
                return JsonResponse({'status': 'error', 'message': f'Telefone: {e.message}'}, status=400)
            
            # Validar endereço para entregas
            if order_type == 'delivery':
                address_data = data.get('address', {})
                
                # Validar CEP
                cep = address_data.get('cep', '').strip()
                try:
                    cep_clean = validate_cep(cep)
                except ValidationError as e:
                    return JsonResponse({'status': 'error', 'message': f'CEP: {e.message}'}, status=400)
                
                # Validar rua
                street = address_data.get('street', '').strip()
                street = ''.join(c for c in street if c.isalnum() or c.isspace() or c in '-,./°ª')
                if not street or len(street) < 3:
                    return JsonResponse({'status': 'error', 'message': 'Rua deve ter no mínimo 3 caracteres.'}, status=400)
                
                # Validar número
                number = address_data.get('number', '').strip()
                number = ''.join(c for c in number if c.isalnum() or c in '/-')
                if not number:
                    return JsonResponse({'status': 'error', 'message': 'Número é obrigatório.'}, status=400)
                
                # Validar bairro
                neighborhood = address_data.get('neighborhood', '').strip()
                neighborhood = ''.join(c for c in neighborhood if c.isalnum() or c.isspace() or c in '-.')
                if not neighborhood or len(neighborhood) < 2:
                    return JsonResponse({'status': 'error', 'message': 'Bairro deve ter no mínimo 2 caracteres.'}, status=400)
            
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
                
                # Validar disponibilidade
                if not product.is_available:
                    return JsonResponse({'status': 'error', 'message': f"O produto {product.name} acabou de ficar indisponível."}, status=400)
                
                # Validar quantidade
                try:
                    qty = int(item.get('qtd', 0))
                    if qty <= 0 or qty > 999:
                        return JsonResponse({'status': 'error', 'message': 'Quantidade inválida no carrinho.'}, status=400)
                except (ValueError, TypeError):
                    return JsonResponse({'status': 'error', 'message': 'Dados de quantidade inválidos.'}, status=400)

                # Preço base do produto
                item_price = product.price
                current_item_total = item_price
                
                # Opcionais: Validar e somar preços do banco
                options_list = item.get('options', [])
                valid_options_text = []
                
                for opt in options_list:
                    # O front manda {name: 'Bacon', price: 2.00} ou {name: 'Bacon (3x)', price: 6.00}
                    # Usamos o nome do front para preservar a quantidade se existir
                    opt_name = opt.get('name')
                    
                    # Busca complexa: OpçãoItem -> ProductOption -> Product
                    # Primeiro tenta encontrar o item exato (para validar preço)
                    clean_name = opt_name.split(' (')[0].strip()

                    # 2. Busca EXATA (iexact): Garante que "Bacon" só pegue "Bacon", e não "Bacon Extra"
                    db_option_item = OptionItem.objects.filter(
                        name__iexact=clean_name, 
                        option__product=product
                    ).first()
                    
                    if db_option_item:
                        current_item_total += db_option_item.price
                        # Usa o nome do front (que pode ter "(3x)") em vez do nome do banco
                        valid_options_text.append(opt_name)
                    else:
                        # Se não encontrar, ainda assim salva o nome do front
                        valid_options_text.append(opt_name)

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
            
            # Para pedidos de mesa, não cobra taxa de entrega
            if order_type == 'table':
                delivery_fee = Decimal('0.00')
            elif neighborhood:
                # Normaliza o bairro do cliente para busca
                neighborhood_normalized = normalizar_texto(neighborhood)
                
                # Busca todas as taxas e compara com versão normalizada
                fees = DeliveryFee.objects.filter(tenant=tenant)
                fee_obj = None
                for fee in fees:
                    if normalizar_texto(fee.neighborhood) == neighborhood_normalized:
                        fee_obj = fee
                        break
                
                if fee_obj:
                    delivery_fee = fee_obj.fee

            # C. Calcular Cupom (Validar no Backend)
            discount_value = Decimal('0.00')
            coupon_code = data.get('coupon_code')
            applied_coupon = None

            if coupon_code:
                coupon = Coupon.objects.filter(tenant=tenant, code=coupon_code).first()
                if coupon:
                    is_valid, msg = coupon.is_valid()
                    if is_valid:
                        if coupon.minimum_order_value > 0 and items_total < coupon.minimum_order_value:
                            pass
                        else:
                            final_val, discount_amt = coupon.apply_discount(items_total)
                            discount_value = Decimal(str(discount_amt))
                            
                            coupon.used_count += 1
                            coupon.save()
                            applied_coupon = coupon

            # D. TOTAL FINAL REAL
            final_total = items_total + delivery_fee - discount_value
            if final_total < 0: final_total = Decimal('0.00')

            status_inicial = 'pendente'
            if tenant.plan_type == 'starter':
                status_inicial = 'concluido'

            # Criação do Pedido
            obs = data.get('obs', '').strip()[:500]  # Limitar a 500 caracteres
            
            order = Order.objects.create(
                tenant=tenant,
                customer_name=nome,
                status=status_inicial,
                customer_phone=phone_clean,
                
                # VALORES BLINDADOS:
                total_value=final_total,
                delivery_fee=delivery_fee,
                discount_value=discount_value,
                coupon=applied_coupon,
                
                payment_method=data.get('method'),
                address_cep=cep_clean if order_type == 'delivery' else '',
                address_street=street if order_type == 'delivery' else '',
                address_number=number if order_type == 'delivery' else '',
                address_neighborhood=neighborhood if order_type == 'delivery' else '',
                observation=obs,
                
                # NOVOS CAMPOS (NOVO)
                order_type=order_type,
                table=table,

                # NOVOS CAMPOS PARA AGENDAMENTO
                is_scheduled=is_scheduled,
                scheduled_date=scheduled_date if is_scheduled else None,
                scheduled_time=scheduled_time if is_scheduled else None
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

            try:
                # 1. Recupera o endpoint salvo no cookie do navegador
                device_endpoint = request.COOKIES.get('push_endpoint')
                
                if device_endpoint:
                    from .models import PushSubscription
                    # 2. Busca a inscrição por esse endpoint e atualiza o telefone com o do pedido
                    PushSubscription.objects.filter(
                        tenant=tenant, 
                        endpoint=device_endpoint
                    ).update(customer_phone=phone_clean)
                    
                    logger.info(f"[PUSH] Telefone {phone_clean} vinculado ao device via Cookie com sucesso.")
            except Exception as e:
                logger.warning(f"[PUSH] Erro ao vincular telefone no pedido: {e}")
            
            return JsonResponse({
                'status': 'success', 
                'order_id': order.id, 
                'real_total': float(final_total),
                'order_type': order_type
            })

        except ValidationError as e:
            # Erros de validação (CEP, telefone, etc)
            logger.warning(f"Erro de validação ao criar pedido: {e}")
            return JsonResponse({'status': 'error', 'message': str(e.message) if hasattr(e, 'message') else str(e)}, status=400)
        
        except Product.DoesNotExist as e:
            logger.warning(f"Produto não encontrado ao criar pedido: {e}")
            return JsonResponse({'status': 'error', 'message': 'Um ou mais produtos não foi encontrado ou foi removido.'}, status=400)
        
        except json.JSONDecodeError:
            logger.error("Erro ao decodificar JSON na criação de pedido")
            return JsonResponse({'status': 'error', 'message': 'Dados inválidos enviados. Tente novamente.'}, status=400)
        
        except Exception as e:
            # Em caso de erro genérico, o transaction.atomic desfaz tudo
            logger.error(f"Erro inesperado ao criar pedido: {type(e).__name__} - {str(e)}", exc_info=True)
            return JsonResponse({
                'status': 'error', 
                'message': 'Erro ao processar seu pedido. Por favor, tente novamente mais tarde.'
            }, status=500)

    return JsonResponse({'status': 'error', 'message': 'Método inválido'}, status=400)

@login_required
def api_get_orders(request, slug):
    # Retorna os pedidos da loja (JSON) para o painel atualizar via AJAX
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'error': 'Acesso negado'}, status=403)

    # --- PROTEÇÃO DO PLANO ---
    if not tenant.can_access_orders:
        return JsonResponse({'orders': [], 'plan_block': True, 'message': 'Faça upgrade para ver pedidos em tempo real.'})
    
    # Filtro por tipo de pedido (NOVO)
    filter_type = request.GET.get('type', 'all')  # all, delivery, table
    
    orders = Order.objects.filter(tenant=tenant)
    
    if filter_type == 'table':
        orders = orders.filter(order_type='table')
    elif filter_type == 'delivery':
        orders = orders.filter(order_type='delivery')
    elif filter_type == 'pickup':
        orders = orders.filter(order_type='pickup')
    # 'all' não aplica filtro
    
    orders = orders.order_by('-created_at')[:20]
    
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
            
        # Identificar informações da mesa (NOVO)
        table_info = None
        if order.table:
            table_info = {
                'id': order.table.id,
                'number': order.table.number
            }
        
        # Formata o endereço ou mesa
        if order.order_type == 'table':
            address_display = f"Mesa {order.table.number}"
        elif order.address_street:
            address_display = f"{order.address_street}, {order.address_number} - {order.address_neighborhood}"
        else:
            address_display = "Retirada"
            
        data.append({
            'id': order.id,
            'customer_name': order.customer_name,
            'customer_phone': order.customer_phone,
            'phone': order.customer_phone,
            'total_value': float(order.total_value),
            'delivery_fee': float(order.delivery_fee) if order.delivery_fee else 0,
            'discount_amount': float(order.discount_value) if order.discount_value else 0,
            'discount_value': float(order.discount_value) if order.discount_value else 0,
            'coupon_code': order.coupon.code if order.coupon else None,
            'status': order.status,
            'is_printed': order.is_printed,
            'payment_method': order.payment_method,
            'address': address_display,
            'observation': order.observation,
            'created_at': timezone.localtime(order.created_at).strftime('%d/%m %H:%M'),
            'items': items,
            'order_type': order.order_type,
            'table': table_info,
            'table_number': order.table.number if order.table else None,
            # ADICIONE ESTES CAMPOS:
            'is_scheduled': order.is_scheduled,
            'scheduled_date': order.scheduled_date.strftime('%d/%m/%Y') if order.scheduled_date else None,
            'scheduled_time': order.scheduled_time.strftime('%H:%M') if order.scheduled_time else None,
        })
        
    return JsonResponse({'orders': data})

@login_required
def api_update_order(request, slug, order_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            new_status = data.get('status')
            
            order = Order.objects.get(id=order_id, tenant__slug=slug)
            
            # Log para debug
            logger.info(f"Atualizando pedido #{order.id} para status: {new_status}")
            
            order.status = new_status
            order.save()
            
            # CORREÇÃO: Usar 'saiu_entrega' (snake_case) ao invés de 'saiu para entrega'
            if new_status == 'saiu_entrega':
                logger.info(f"Disparando Push para pedido #{order.id}")
                send_push_notification(order, tenant)
            
            return JsonResponse({'status': 'success'})
        except Order.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Pedido não encontrado'}, status=404)
            
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def api_mark_printed(request, slug, order_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user and not request.user.is_superuser:
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

@login_required
def api_update_settings(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            # Agora usamos request.POST e request.FILES (FormData)
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                # Se for FormData (veio com imagens)
                data = request.POST

            files = request.FILES
            
            # Configurações de Tempo de Entrega
            if 'delivery_time' in data:
                tenant.delivery_time = int(data.get('delivery_time'))
            if 'pickup_time' in data:
                tenant.pickup_time = int(data.get('pickup_time'))
            
            # Checkbox vem como string 'true'/'false' ou 'on' no FormData
            if 'show_delivery_time' in data:
                val = data.get('show_delivery_time')
                tenant.show_delivery_time = val == 'true' or val == 'on' or val == True
                
            if 'show_pickup_time' in data:
                val = data.get('show_pickup_time')
                tenant.show_pickup_time = val == 'true' or val == 'on' or val == True
            
            # Configurações de PIX
            if 'pix_key' in data: tenant.pix_key = data.get('pix_key')
            if 'pix_name' in data: tenant.pix_name = data.get('pix_name')
            if 'pix_city' in data: tenant.pix_city = data.get('pix_city')
            
            # Endereço e Contato
            if 'address' in data: tenant.address = data.get('address')
            if 'phone_whatsapp' in data: tenant.phone_whatsapp = data.get('phone_whatsapp')
            
            # Personalização Visual (Texto)
            if 'primary_color' in data:
                tenant.primary_color = data.get('primary_color')
            
            if 'store_name' in data:
                tenant.name = data.get('store_name')

            # === SALVAMENTO DAS IMAGENS ===
            
            # Logo
            if 'logo' in files:
                # Se já tiver logo, o django substitui, mas é boa prática deletar a antiga se quiser economizar espaço
                # mas o comportamento padrão funciona bem.
                tenant.logo = files['logo']
            
            # Capa / Background
            if 'background_image' in files:
                tenant.background_image = files['background_image']

            if 'allow_scheduling' in data:
                val = data.get('allow_scheduling')
                # Aceita 'true', 'on', True ou 1 como verdadeiro
                tenant.allow_scheduling = val in ['true', 'on', True, 'True', 1, '1']
                
            tenant.save()
            return JsonResponse({'status': 'success', 'message': 'Configurações salvas com sucesso'})
            
        except Exception as e:
            logger.error(f"Erro ao salvar configurações: {e}")
            return JsonResponse({'status': 'error', 'message': f'Erro ao salvar configurações: {str(e)}'}, status=500)
            
    return JsonResponse({'status': 'error'}, status=400)

# --- APIs DE HISTORICO DO CLIENTE ---
def api_customer_history(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            order_ids = data.get('order_ids', [])
            
            orders = Order.objects.filter(
                tenant=tenant,
                id__in=order_ids
            ).prefetch_related('items').order_by('-created_at')
            
            history_data = []
            for order in orders:
                items_str = []
                for item in order.items.all():
                    desc = f"{item.quantity}x {item.product_name}"
                    items_str.append(desc)
                
                # Determinar tipo de pedido para exibir (NOVO)
                is_delivery = order.order_type == 'delivery'
                is_table = order.order_type == 'table'
                
                history_data.append({
                    'id': order.id,
                    'status': order.get_status_display(),
                    'status_key': order.status,
                    'total': float(order.total_value),
                    'date': timezone.localtime(order.created_at).strftime('%d/%m %H:%M'),
                    'items_summary': ', '.join(items_str),
                    'is_delivery': is_delivery,
                    'is_table': is_table,
                    'table_number': order.table.number if order.table else None
                })
                
            return JsonResponse({'status': 'success', 'orders': history_data})
            
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
            
    return JsonResponse({'status': 'error'}, status=400)

# --- APIs DE PRODUTOS (CRUD) ---

@login_required
def api_get_products(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    categories = Category.objects.filter(tenant=tenant).prefetch_related('products', 'products__options', 'products__options__items').order_by('order')
    
    data = []
    for cat in categories:
        products = []
        for prod in cat.products.all():
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
                    'group_id': opt.group_id,  # ADICIONADO: ID do grupo importado
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
                'options': options_list
            })
        
        data.append({
            'id': cat.id,
            'name': cat.name,
            'products': products
        })
        
    return JsonResponse({'categories': data})

@login_required
def api_save_product(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verificar se o usuário é o dono da loja
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            prod_id = request.POST.get('id')
            cat_input = request.POST.get('category') 
            name = request.POST.get('name')
            
            # Lógica de tratamento de preço (Mantida a sua lógica original)
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
                
                clear_image = request.POST.get('clear_image', 'false') == 'true'
                if clear_image:
                    if product.image:
                        product.image.delete(save=False)
                    product.image = None
                elif image:
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
            
            options_json = request.POST.get('options_json')
            if options_json:
                options_data = json.loads(options_json)
                
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

            # === AQUI ESTÁ A CORREÇÃO ===
            # Passamos o ID da categoria atual para ela ser protegida da exclusão
            current_cat_id = category.id if category else None
            _limpar_categorias_vazias(tenant, category_id_to_protect=current_cat_id)
                
            return JsonResponse({'status': 'success'})
        except Exception as e:
            logger.error(f"Erro ao salvar produto: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def api_get_product_options(request, slug, product_id):
    """
    Retorna os grupos de adicionais de um produto específico para importação.
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Verifica permissão
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)

    try:
        source_product = Product.objects.get(id=product_id, tenant=tenant)
        
        options_data = []
        for opt in source_product.options.all():
            items = []
            for item in opt.items.all():
                items.append({
                    'name': item.name,
                    'price': float(item.price)
                })
            
            options_data.append({
                'title': opt.title,
                'type': opt.type,
                'required': opt.required,
                'max': opt.max_quantity,
                'items': items
            })
            
        return JsonResponse({'status': 'success', 'options': options_data})
    except Product.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Produto não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@login_required
def api_delete_product(request, slug, product_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
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

@login_required
def api_toggle_product(request, slug, product_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
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

# ========================
# GERENCIAMENTO DE GRUPOS REUTILIZÁVEIS
# ========================

@login_required
def api_get_product_groups(request, slug):
    """Retorna todos os grupos de adicionais reutilizáveis da loja"""
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    from .models import ProductGroup
    
    groups = ProductGroup.objects.filter(tenant=tenant).values('id', 'name', 'type', 'required', 'max_quantity')
    
    groups_data = []
    for group in groups:
        items = list(GroupItem.objects.filter(group_id=group['id']).values('id', 'name', 'price'))
        group['items'] = items
        groups_data.append(group)
    
    return JsonResponse({'status': 'success', 'groups': groups_data})

@login_required
def api_save_product_group(request, slug):
    """Cria ou atualiza um grupo de adicionais reutilizável"""
    from .models import ProductGroup, GroupItem
    
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            group_id = request.POST.get('id')
            name = request.POST.get('name')
            group_type = request.POST.get('type')
            required = request.POST.get('required', 'false') == 'true'
            max_qty = int(request.POST.get('max_quantity', 10))
            items_json = request.POST.get('items_json')
            
            if not name:
                return JsonResponse({'status': 'error', 'message': 'Nome do grupo é obrigatório'}, status=400)
            
            if group_id:
                group = ProductGroup.objects.get(id=group_id, tenant=tenant)
                group.name = name
                group.type = group_type
                group.required = required
                group.max_quantity = max_qty
                group.save()
            else:
                group, created = ProductGroup.objects.get_or_create(
                    tenant=tenant,
                    name=name,
                    defaults={
                        'type': group_type,
                        'required': required,
                        'max_quantity': max_qty
                    }
                )
                if not created:
                    return JsonResponse({'status': 'error', 'message': 'Este grupo já existe'}, status=400)
            
            # Salvar itens
            if items_json:
                items_data = json.loads(items_json)
                group.items.all().delete()
                
                for item_data in items_data:
                    GroupItem.objects.create(
                        group=group,
                        name=item_data['name'],
                        price=item_data.get('price', 0)
                    )
            
            return JsonResponse({'status': 'success', 'id': group.id})
        except ProductGroup.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Grupo não encontrado'}, status=404)
        except Exception as e:
            logger.error(f"Erro ao salvar grupo: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def api_delete_product_group(request, slug, group_id):
    """Deleta um grupo de adicionais reutilizável"""
    from .models import ProductGroup
    
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            group = ProductGroup.objects.get(id=group_id, tenant=tenant)
            group.delete()
            return JsonResponse({'status': 'success'})
        except ProductGroup.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Grupo não encontrado'}, status=404)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def api_import_product_group(request, slug, product_id):
    """Importa um grupo reutilizável para um produto específico"""
    from .models import ProductOption, ProductGroup
    
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            product = Product.objects.get(id=product_id, tenant=tenant)
            group_id = request.POST.get('group_id')
            
            if not group_id:
                return JsonResponse({'status': 'error', 'message': 'ID do grupo é obrigatório'}, status=400)
            
            group = ProductGroup.objects.get(id=group_id, tenant=tenant)
            
            # Criar uma ProductOption baseada no ProductGroup
            option = ProductOption.objects.create(
                product=product,
                group=group,
                title=group.name,
                type=group.type,
                required=group.required,
                max_quantity=group.max_quantity
            )
            
            # Copiar os itens do grupo para a opção do produto
            for group_item in group.items.all():
                OptionItem.objects.create(
                    option=option,
                    name=group_item.name,
                    price=group_item.price
                )
            
            return JsonResponse({'status': 'success', 'option_id': option.id})
        except Product.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Produto não encontrado'}, status=404)
        except ProductGroup.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': 'Grupo não encontrado'}, status=404)
        except Exception as e:
            logger.error(f"Erro ao importar grupo: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)

def _limpar_categorias_vazias(tenant, category_id_to_protect=None):
    # Pega todas as categorias vazias dessa loja
    cats_to_delete = Category.objects.filter(tenant=tenant, products__isnull=True)
    
    # SE tivermos um ID para proteger (a categoria que acabamos de usar), excluímos ela da lista de deleção
    if category_id_to_protect:
        cats_to_delete = cats_to_delete.exclude(id=category_id_to_protect)
        
    cats_to_delete.delete()

# ROTAS DE LOGIN E LOGOUT
@ratelimit(key='ip', rate='5/m', block=False)
def custom_login(request):
    # (Opcional) Lógica de rate limit
    was_limited = getattr(request, 'limited', False)
    if was_limited:
        return render(request, 'tenants/login.html', {
            'error': 'Muitas tentativas de login. Aguarde 1 minuto.'
        })

    if request.method == 'POST':
        username = request.POST.get('username', '').strip().lower()
        passw = request.POST.get('password', '')
        remember_me = request.POST.get('remember_me') # Captura o checkbox
        
        user = authenticate(request, username=username, password=passw)
        
        if user is not None:
            login(request, user)
            
            if remember_me:
                request.session.set_expiry(1209600) # 2 semanas
            else:
                request.session.set_expiry(0) # Fecha ao fechar o navegador
            
            # Redirecionamento
            user_tenants = Tenant.objects.filter(owner=user).order_by('-id')
            if user_tenants.exists():
                return redirect('painel_lojista', slug=user_tenants.first().slug)
            else:
                return redirect('signup') # Ou criar loja
        else:
            return render(request, 'tenants/login.html', {'error': 'Usuário ou senha incorretos'})

    return render(request, 'tenants/login.html')

def custom_logout(request):
    logout(request)
    return redirect('custom_login')

# ROTA PARA CRIAR EMPRESA

def signup(request):
    if request.method == 'POST':
        store_name = request.POST.get('store_name', '').strip()
        # Novo campo para o slug (opcional no form, mas tratado aqui)
        slug_input = request.POST.get('slug', '').strip()
        
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        
        if not store_name or len(store_name) < 3:
            return render(request, 'tenants/signup.html', {'error': 'Nome da loja deve ter pelo menos 3 caracteres.'})
            
        if not email or '@' not in email:
            return render(request, 'tenants/signup.html', {'error': 'Digite um email válido.'})
        
        if not password or len(password) < 8:
            return render(request, 'tenants/signup.html', {'error': 'A senha deve ter pelo menos 8 caracteres.'})
            
        # Lógica do Slug: Se o usuário digitou, usa o dele. Se não, usa o nome da loja.
        # O slugify garante que "Rafa Burguer" vire "rafa-burguer"
        if slug_input:
            final_slug = slugify(slug_input)
        else:
            final_slug = slugify(store_name)
            
        # Validação extra: Slug vazio após slugify (ex: usuário digitou só simbolos)
        if not final_slug:
            return render(request, 'tenants/signup.html', {'error': 'Link da loja inválido.'})
        
        # Verifica duplicidade
        if Tenant.objects.filter(slug=final_slug).exists():
            return render(request, 'tenants/signup.html', {'error': f'O link "{final_slug}" já está em uso. Escolha outro.'})
            
        if User.objects.filter(username=email).exists():
            return render(request, 'tenants/signup.html', {'error': 'Este email já está cadastrado.'})

        try:
            with transaction.atomic(): # Garante que cria tudo ou nada
                user = User.objects.create_user(username=email, email=email, password=password)
                logger.info(f"Novo usuário criado: ID {user.id}")

                chosen_color = request.POST.get('primary_color', '#ea580c')
                
                tenant = Tenant.objects.create(
                    owner=user,
                    name=store_name, # Ex: Hamburguer do Rafael
                    slug=final_slug, # Ex: rafaburguer
                    primary_color=chosen_color
                )
                
                # Categorias Padrão
                Category.objects.create(tenant=tenant, name="Destaques", order=1)
                Category.objects.create(tenant=tenant, name="Bebidas", order=2)

                # ===========================================
                # ENVIAR EMAIL DE BOAS-VINDAS
                # ===========================================
                try:
                    from django.conf import settings
                    from django.core.mail import EmailMultiAlternatives
                    
                    subject = f'🎉 Bem-vindo ao RM Pedidos, {store_name}!'
                    
                    # Versão texto simples
                    text_content = f'''
Olá!

Parabéns! Sua loja "{store_name}" está pronta para vender online! 🚀

PRIMEIROS PASSOS:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Acesse seu painel: {request.build_absolute_uri("/").rstrip("/")}/{final_slug}/painel/
2. Configure o horário de funcionamento
3. Cadastre seus produtos
4. Compartilhe seu cardápio digital

O QUE VOCÊ PODE FAZER:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓ Gerenciar produtos e categorias
✓ Receber pedidos em tempo real
✓ Criar cupons de desconto
✓ Gerar QR Codes para mesas
✓ Acompanhar vendas e relatórios
✓ Personalizar cores e logo

PRECISA DE AJUDA?
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📱 WhatsApp: (83) 92000-6113
📧 Email: suporte@rmpedidos.com.br

Estamos aqui para te ajudar a crescer!

Equipe RM Pedidos
Tecnologia que vende mais 🚀
'''

                    # Versão HTML (mais profissional)
                    html_content = f'''
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f5f5;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #f5f5f5; padding: 20px;">
        <tr>
            <td align="center">
                <!-- Container Principal -->
                <table width="600" cellpadding="0" cellspacing="0" style="background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
                    
                    <!-- Header com gradiente -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #ea580c 0%, #dc2626 100%); padding: 40px 30px; text-align: center;">
                            <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: 700;">
                                🎉 Bem-vindo ao RM Pedidos!
                            </h1>
                            <p style="color: #fee2e2; margin: 10px 0 0 0; font-size: 16px;">
                                Sua loja digital está pronta para vender
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Conteúdo -->
                    <tr>
                        <td style="padding: 40px 30px;">
                            
                            <!-- Mensagem de Boas-vindas -->
                            <p style="color: #374151; font-size: 16px; line-height: 1.6; margin: 0 0 20px 0;">
                                Olá! 👋
                            </p>
                            <p style="color: #374151; font-size: 16px; line-height: 1.6; margin: 0 0 30px 0;">
                                Parabéns! Sua loja <strong style="color: #ea580c;">"{store_name}"</strong> foi criada com sucesso e já está pronta para receber pedidos online! 🚀
                            </p>
                            
                            <!-- CTA Principal -->
                            <table width="100%" cellpadding="0" cellspacing="0" style="margin: 30px 0;">
                                <tr>
                                    <td align="center">
                                        <a href="{request.build_absolute_uri("/").rstrip("/")}/{final_slug}/painel/" 
                                            style="display: inline-block; background: linear-gradient(135deg, #ea580c 0%, #dc2626 100%); color: #ffffff; text-decoration: none; padding: 16px 40px; border-radius: 8px; font-weight: 700; font-size: 16px; box-shadow: 0 4px 6px rgba(234, 88, 12, 0.3);">
                                            🎯 Acessar Meu Painel
                                        </a>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Primeiros Passos -->
                            <div style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 20px; border-radius: 6px; margin: 30px 0;">
                                <h3 style="color: #92400e; margin: 0 0 15px 0; font-size: 18px;">
                                    ⚡ Comece Agora em 4 Passos:
                                </h3>
                                <ol style="color: #78350f; margin: 0; padding-left: 20px; line-height: 1.8;">
                                    <li>Configure o horário de funcionamento</li>
                                    <li>Cadastre seus produtos</li>
                                    <li>Personalize cores e logo da sua loja</li>
                                    <li>Compartilhe seu cardápio digital</li>
                                </ol>
                            </div>
                            
                            <!-- Recursos -->
                            <h3 style="color: #1f2937; font-size: 20px; margin: 30px 0 20px 0;">
                                💎 O Que Você Pode Fazer:
                            </h3>
                            
                            <table width="100%" cellpadding="0" cellspacing="0">
                                <tr>
                                    <td width="50%" style="padding: 10px;">
                                        <div style="background-color: #f9fafb; padding: 15px; border-radius: 8px; border: 1px solid #e5e7eb;">
                                            <span style="font-size: 24px;">📱</span>
                                            <p style="color: #374151; margin: 8px 0 0 0; font-size: 14px; font-weight: 600;">
                                                Pedidos em Tempo Real
                                            </p>
                                        </div>
                                    </td>
                                    <td width="50%" style="padding: 10px;">
                                        <div style="background-color: #f9fafb; padding: 15px; border-radius: 8px; border: 1px solid #e5e7eb;">
                                            <span style="font-size: 24px;">🎨</span>
                                            <p style="color: #374151; margin: 8px 0 0 0; font-size: 14px; font-weight: 600;">
                                                Personalização Total
                                            </p>
                                        </div>
                                    </td>
                                </tr>
                                <tr>
                                    <td width="50%" style="padding: 10px;">
                                        <div style="background-color: #f9fafb; padding: 15px; border-radius: 8px; border: 1px solid #e5e7eb;">
                                            <span style="font-size: 24px;">🎫</span>
                                            <p style="color: #374151; margin: 8px 0 0 0; font-size: 14px; font-weight: 600;">
                                                Cupons de Desconto
                                            </p>
                                        </div>
                                    </td>
                                    <td width="50%" style="padding: 10px;">
                                        <div style="background-color: #f9fafb; padding: 15px; border-radius: 8px; border: 1px solid #e5e7eb;">
                                            <span style="font-size: 24px;">📊</span>
                                            <p style="color: #374151; margin: 8px 0 0 0; font-size: 14px; font-weight: 600;">
                                                Relatórios de Vendas
                                            </p>
                                        </div>
                                    </td>
                                </tr>
                            </table>
                            
                            <!-- Suporte -->
                            <div style="background-color: #eff6ff; border: 2px solid #3b82f6; padding: 20px; border-radius: 8px; margin: 30px 0;">
                                <h3 style="color: #1e40af; margin: 0 0 15px 0; font-size: 18px;">
                                    💬 Precisa de Ajuda?
                                </h3>
                                <p style="color: #1e3a8a; margin: 0 0 10px 0; line-height: 1.6;">
                                    <strong>📱 WhatsApp:</strong> (83) 92000-6113<br>
                                    <strong>📧 Email:</strong> suporte@rmpedidos.com.br
                                </p>
                                <p style="color: #1e3a8a; margin: 10px 0 0 0; font-size: 14px;">
                                    Estamos aqui para te ajudar a vender mais! 🚀
                                </p>
                            </div>
                            
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f9fafb; padding: 30px; text-align: center; border-top: 1px solid #e5e7eb;">
                            <p style="color: #6b7280; font-size: 14px; margin: 0 0 10px 0;">
                                Obrigado por escolher o <strong style="color: #ea580c;">RM Pedidos</strong>
                            </p>
                            <p style="color: #9ca3af; font-size: 12px; margin: 0;">
                                Tecnologia que vende mais 🚀
                            </p>
                        </td>
                    </tr>
                    
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
'''
                    
                    # Criar email com versão HTML e texto
                    email = EmailMultiAlternatives(
                        subject=subject,
                        body=text_content,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[email]
                    )
                    email.attach_alternative(html_content, "text/html")
                    email.send(fail_silently=True)
                    logger.info(f'[EMAIL] Email de boas-vindas enviado para {email}')
                except Exception as email_error:
                    # Log do erro mas não impede o cadastro
                    logger.warning(f'[EMAIL] Erro ao enviar email de boas-vindas: {email_error}')

                login(request, user)
                return redirect('painel_lojista', slug=tenant.slug)

        except Exception as e:
            logger.error(f"Erro ao criar conta: {e}")
            return render(request, 'tenants/signup.html', {'error': 'Erro ao criar conta. Tente novamente.'})

    return render(request, 'tenants/signup.html')

# --- API FINANCEIRO E HISTÓRICO ---
@login_required
def api_get_financials(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'error': 'Acesso negado'}, status=403)

    # --- PROTEÇÃO DO PLANO ---
    if not tenant.can_access_reports:
        return JsonResponse({'orders': [], 'plan_block': True, 'message': 'Faça upgrade para ver pedidos em tempo real.'})
    
    # Usa localtime para garantir que o "hoje" seja o hoje do Brasil, não o do UTC
    today = timezone.localtime(timezone.now()).date()
    
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

    history_orders = Order.objects.filter(
        tenant=tenant,
        status__in=['concluido', 'cancelado']
    ).order_by('-created_at')[:50]
    
    history_data = []
    for order in history_orders:
        # CORREÇÃO AQUI: Converter para o horário local antes de formatar
        local_dt = timezone.localtime(order.created_at)
        
        history_data.append({
            'id': order.id,
            'customer': order.customer_name,
            'total': float(order.total_value),
            'status': order.status,
            # Mantemos o date puro para filtros, mas corrigido
            'date': local_dt.strftime('%Y-%m-%d'),
            # O date_display é o que aparece na tabela (estava com +3h antes)
            'date_display': local_dt.strftime('%d/%m %H:%M'),
            'payment': order.payment_method or ''
        })

    return JsonResponse({
        'sales_today': float(sales_today),
        'count_today': count_today,
        'history': history_data
    })

# --- API ABRIR/FECHAR LOJA ---
@login_required
def api_toggle_store_open(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Permitir owner da loja OU superuser
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
        
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            is_open = data.get('is_open', False)
            
            # Lógica Nova (baseada em manual_override):
            # is_open=True (checkbox marcado) → Loja em modo AUTOMÁTICO (sem bloqueio manual)
            # is_open=False (checkbox desmarcado) → Loja em modo MANUAL FECHADO (com bloqueio)
            
            if is_open:
                # QUER ABRIR: Removemos o bloqueio manual
                tenant.manual_override = False
            else:
                # QUER FECHAR: Ativamos o bloqueio manual
                tenant.manual_override = True
            
            tenant.save()
            
            return JsonResponse({
                'status': 'success', 
                'is_open': not tenant.manual_override,
                'manual_override': tenant.manual_override
            })
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Dados inválidos'}, status=400)
        
    return JsonResponse({'status': 'error'}, status=400)

# --- API SINCRONIZAR STATUS COM HORÁRIOS ---
@login_required
def api_sync_store_status(request, slug):
    """Sincroniza o estado da loja com os horários.
    
    NOVA LÓGICA PROFISSIONAL:
    - Se manual_override está ativado, mantém fechado (dono quer fechar manualmente)
    - Se manual_override está desativado, sincroniza com os horários
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            # Se manual_override está ativado, RESPEITA (dono quer fechar manualmente)
            if tenant.manual_override:
                return JsonResponse({
                    'status': 'success',
                    'is_open': False,
                    'reason': 'fechamento_manual'
                })
            
            # Caso contrário, sincroniza com os horários
            is_open_by_hours, status = is_store_open_by_hours(tenant)
            
            return JsonResponse({
                'status': 'success',
                'is_open': is_open_by_hours,
                'reason': 'horario_funcionamento'
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return JsonResponse({'status': 'error'}, status=400)

# --- API PÚBLICA PARA STATUS DA LOJA (ACESSO PELO CLIENTE) ---
def api_public_store_status(request, slug):
    """
    API pública para verificar o status da loja.
    Usada pelo cardápio do cliente para exibir a mensagem correta.
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if request.method == 'GET':
        try:
            # PRIORIDADE 1: Fechamento manual pelo dono
            if tenant.manual_override:
                return JsonResponse({
                    'status': 'success',
                    'is_open': False,
                    'reason': 'fechamento_manual',
                    'message': 'FECHADO TEMPORARIAMENTE'
                })
            
            # PRIORIDADE 2: Verificar horário de funcionamento
            is_open, message = is_store_open_by_hours(tenant)
            
            return JsonResponse({
                'status': 'success',
                'is_open': is_open,
                'reason': 'horario_funcionamento',
                'message': message
            })
                
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)

# ROTA PARA HORARIO DE FUNCIONAMENTO/FOLGAS
@login_required
def api_save_hours(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
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

# --- API TAXAS DE ENTREGA ---
@login_required
def api_delivery_fees(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'GET':
        fees = list(tenant.delivery_fees.values('id', 'neighborhood', 'fee'))
        return JsonResponse({'fees': fees})

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            neighborhood = data.get('neighborhood', '').strip()
            fee = data.get('fee')

            if not neighborhood or fee is None:
                return JsonResponse({'status': 'error', 'message': 'Dados inválidos'}, status=400)

            # Normaliza o bairro para manter consistência
            neighborhood_normalized = normalizar_texto(neighborhood)

            DeliveryFee.objects.update_or_create(
                tenant=tenant,
                neighborhood__iexact=neighborhood,
                defaults={'neighborhood': neighborhood_normalized, 'fee': fee}
            )
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao salvar taxa de entrega'}, status=500)

    return JsonResponse({'status': 'error'}, status=400)

# ========================
# APIs DE NOTIFICAÇÕES PUSH
# ========================

def api_push_subscribe(request, slug):
    from .models import PushSubscription
    
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            subscription_data = data.get('subscription')
            
            # Captura e limpa o telefone recebido
            raw_phone = data.get('customer_phone', '')
            customer_phone = ''.join(filter(str.isdigit, raw_phone)) if raw_phone else None
            
            if not subscription_data:
                return JsonResponse({'status': 'error', 'message': 'Subscription inválida'}, status=400)
            
            endpoint = subscription_data.get('endpoint')
            keys = subscription_data.get('keys', {})
            p256dh = keys.get('p256dh', '')
            auth = keys.get('auth', '')
            
            if not endpoint:
                return JsonResponse({'status': 'error', 'message': 'Endpoint obrigatório'}, status=400)
            
            # LÓGICA BLINDADA: Verificar se já existe antes de salvar
            subscription = PushSubscription.objects.filter(tenant=tenant, endpoint=endpoint).first()
            
            if subscription:
                # Se já existe, atualiza as chaves (caso tenham mudado)
                subscription.p256dh = p256dh
                subscription.auth = auth
                subscription.is_active = True
                
                # O PULO DO GATO: Só atualiza o telefone se o request trouxer um número válido.
                # Se o request vier sem telefone (verificação automática), MANTÉM o que já está no banco.
                if customer_phone:
                    subscription.customer_phone = customer_phone
                
                subscription.save()
            else:
                # Se não existe, cria do zero
                subscription = PushSubscription.objects.create(
                    tenant=tenant,
                    endpoint=endpoint,
                    p256dh=p256dh,
                    auth=auth,
                    is_active=True,
                    customer_phone=customer_phone
                )
            
            # Log para confirmar o que ficou salvo no final
            logger.info(f'[PUSH] Subscription salva/atualizada. Telefone no banco: {subscription.customer_phone}')
            
            response = JsonResponse({'status': 'success', 'message': 'Subscription salva'})
            
            if endpoint:
                response.set_cookie(
                    key='push_endpoint', 
                    value=endpoint, 
                    max_age=31536000, # 1 ano
                    httponly=True,
                    samesite='Lax'
                )
            
            return response

        except Exception as e:
            logger.error(f'[PUSH] Erro ao salvar subscription: {e}', exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)

@login_required
def api_push_subscriptions_count(request, slug):
    """
    Retorna a quantidade de assinantes de push notifications ativos de uma loja.
    Usado para exibir no painel do lojista antes de enviar notificações.
    """
    from .models import PushSubscription
    
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'GET':
        try:
            count = PushSubscription.objects.filter(tenant=tenant, is_active=True).count()
            return JsonResponse({'status': 'success', 'count': count})
        except Exception as e:
            logger.error(f'[PUSH] Erro ao contar subscriptions: {e}', exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)

def api_push_send(request, slug):
    """
    Envia uma notificação push para todos os subscribers de uma loja.
    Usado para notificar promoções, cupons, status da loja, etc.
    Requer autenticação do dono da loja.
    """
    from .models import PushSubscription
    from django.conf import settings
    
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Tipo de notificação
            notification_type = data.get('type', 'custom')
            
            # Templates de notificação
            if notification_type == 'promotion':
                title = f"🔥 {tenant.name}"
                body = data.get('message', ' Temos uma promoção especial para você!')
            elif notification_type == 'coupon':
                title = f"🎁 {tenant.name}"
                coupon_code = data.get('coupon_code', '')
                body = f"Use o cupom {coupon_code} e ganhe desconto!"
            elif notification_type == 'store_open':
                title = f"🟢 {tenant.name}"
                body = data.get('message', 'Agora estamos abertos! Faça seu pedido.')
            elif notification_type == 'new_product':
                title = f"✨ {tenant.name}"
                body = data.get('message', 'Novo produto disponível! Venha conferir.')
            else:
                # Custom message - usa title e body enviados pelo frontend
                title = data.get('title', f'📢 {tenant.name}')
                body = data.get('body', data.get('message', 'Você tem uma nova notificação!'))
            
            url = data.get('url', f'/{slug}/')
            
            # Configurar web-push com VAPID do settings
            VAPID_PRIVATE_KEY = getattr(settings, 'VAPID_PRIVATE_KEY', None)
            VAPID_CLAIM_EMAIL = getattr(settings, 'VAPID_CLAIM_EMAIL', 'mailto: <gabriel.mito07@gmail.com>')
            
            if not VAPID_PRIVATE_KEY:
                return JsonResponse({
                    'status': 'error', 
                    'message': 'VAPID private key não configurada no settings.py'
                }, status=500)
            
            # Nota: A nova API do pywebpush não usa set_vapid_details()
            # Os dados VAPID são passados diretamente para webpush()
            
            subscriptions = PushSubscription.objects.filter(tenant=tenant, is_active=True)
            sent_count = 0
            failed_count = 0
            
            icon_url = tenant.logo.url if tenant.logo else '/static/img/icon-192.svg'
            
            logger.info(f'[PUSH MANUAL] Enviando "{notification_type}" para {subscriptions.count()} subscribers')
            
            for sub in subscriptions:
                try:
                    subscription = sub.to_json()
                    webpush(
                        subscription_info=subscription,
                        data=json.dumps({
                            'title': title,
                            'body': body,
                            'icon': icon_url,
                            'badge': '/static/img/badge-72.png',
                            'url': url,
                            'tag': f'push-manual-{notification_type}-{int(timezone.now().timestamp())}',
                            'extra': {
                                'type': notification_type,
                                'tenant_slug': slug
                            }
                        }),
                        vapid_private_key=VAPID_PRIVATE_KEY,
                        vapid_claims={
                            'sub': VAPID_CLAIM_EMAIL
                        }
                    )
                    sent_count += 1
                except Exception as e:
                    error_str = str(e)
                    # Se o subscription expirou, marcar como inativo
                    if '410' in error_str or 'unsubscribed' in error_str.lower() or 'not found' in error_str.lower():
                        sub.is_active = False
                        sub.save()
                        logger.info(f'[PUSH MANUAL] Subscription {sub.id} marcada como inativa')
                    failed_count += 1
                    logger.warning(f'[PUSH MANUAL] Falha para sub {sub.id}: {error_str}')
            
            logger.info(f'[PUSH MANUAL] Concluído: {sent_count} enviados, {failed_count} falharam')
            
            return JsonResponse({
                'status': 'success', 
                'sent': sent_count,
                'failed': failed_count,
                'message': f'Notificação enviada para {sent_count} clientes'
            })
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'JSON inválido'}, status=400)
        except Exception as e:
            logger.error(f'[PUSH MANUAL] Erro: {e}', exc_info=True)
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error', 'message': 'Método não permitido'}, status=400)

@login_required
def api_delete_delivery_fee(request, slug, fee_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            DeliveryFee.objects.filter(id=fee_id, tenant=tenant).delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir taxa'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)


# ========================
# APIs DE MESAS (NOVO)
# ========================

@login_required
def api_tables(request, slug):
    """
    GET: Lista todas as mesas da loja
    POST: Cria uma nova mesa
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    # GET: Lista mesas
    if request.method == 'GET':

        tables_qs = tenant.tables.annotate(
            order_count=Count('orders', filter=Q(orders__status__in=['pendente', 'em_preparo']))
        ).order_by('number')
        
        tables_data = []
        for table in tables_qs:
            tables_data.append({
                'id': table.id,
                'number': table.number,
                'capacity': table.capacity,
                'is_active': table.is_active,
                'qr_code': table.get_qr_code_url(), # Agora chama a função correta do model!
                'created_at': table.created_at.strftime('%Y-%m-%d'),
                'order_count': table.order_count
            })

        return JsonResponse({'tables': tables_data})
    
    # POST: Cria nova mesa
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            number = int(data.get('number'))
            capacity = int(data.get('capacity', 4))
            
            if not number:
                return JsonResponse({'status': 'error', 'message': 'Número da mesa é obrigatório'}, status=400)
            
            # Verifica se já existe mesa com esse número
            if Table.objects.filter(tenant=tenant, number=number).exists():
                return JsonResponse({'status': 'error', 'message': 'Já existe uma mesa com este número'}, status=400)
            
            table = Table.objects.create(
                tenant=tenant,
                number=number,
                capacity=capacity,
                is_active=True
            )
            
            return JsonResponse({
                'status': 'success',
                'table': {
                    'id': table.id,
                    'number': table.number,
                    'capacity': table.capacity,
                    'is_active': table.is_active,
                    'qr_code': table.get_qr_code_url()
                }
            })
        except Exception as e:
            logger.error(f"Erro ao criar mesa: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def api_table_details(request, slug, table_id):
    """
    GET: Retorna detalhes de uma mesa específica
    PUT: Atualiza uma mesa
    DELETE: Exclui uma mesa
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    table = get_object_or_404(Table, id=table_id, tenant=tenant)
    
    # GET: Retorna detalhes da mesa
    if request.method == 'GET':
        recent_orders = table.orders.order_by('-created_at')[:5]
        recent_orders_data = []
        for order in recent_orders:
            recent_orders_data.append({
                'id': order.id,
                'customer_name': order.customer_name,
                'total_value': float(order.total_value),
                'status': order.status,
                'created_at': timezone.localtime(order.created_at).strftime('%d/%m %H:%M')
            })
        
        return JsonResponse({
            'table': {
                'id': table.id,
                'number': table.number,
                'capacity': table.capacity,
                'is_active': table.is_active,
                'qr_code': table.get_qr_code_url(),
                'created_at': table.created_at.strftime('%d/%m/%Y'),
                'recent_orders': recent_orders_data
            }
        })
    
    # PUT: Atualiza a mesa
    if request.method == 'PUT':
        try:
            data = json.loads(request.body)
            
            # Verifica se o novo número já existe em outra mesa
            new_number = int(data.get('number', table.number))
            if new_number != table.number:
                if Table.objects.filter(tenant=tenant, number=new_number).exists():
                    return JsonResponse({'status': 'error', 'message': 'Já existe outra mesa com este número'}, status=400)
            
            table.number = new_number
            table.capacity = int(data.get('capacity', table.capacity))
            table.is_active = bool(data.get('is_active', table.is_active))
            table.save()
            
            return JsonResponse({
                'status': 'success',
                'table': {
                    'id': table.id,
                    'number': table.number,
                    'capacity': table.capacity,
                    'is_active': table.is_active,
                    'qr_code': table.get_qr_code_url()
                }
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    # DELETE: Exclui a mesa
    if request.method == 'DELETE':
        try:
            table.delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir mesa'}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def api_delete_table(request, slug, table_id):
    """Exclui uma mesa"""
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            table = get_object_or_404(Table, id=table_id, tenant=tenant)
            
            # Remove QR Code antigo se existir
            if table.qr_code:
                table.qr_code.delete(save=False)
            
            table.delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir mesa'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def api_toggle_table(request, slug, table_id):
    """Ativa ou desativa uma mesa"""
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            table = get_object_or_404(Table, id=table_id, tenant=tenant)
            table.is_active = not table.is_active
            table.save()
            return JsonResponse({
                'status': 'success', 
                'is_active': table.is_active,
                'message': f'Mesa {"ativada" if table.is_active else "desativada"} com sucesso'
            })
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao alterar status da mesa'}, status=500)
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def api_generate_qrcode(request, slug, table_id):
    """
    Gera QR Code para uma mesa específica.
    O QR Code leva para a URL: /{slug}/mesa/{number}/
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        
        # Se não for dono e nem admin, verifica se tem loja própria
        user_tenant = Tenant.objects.filter(owner=request.user).first()
        if user_tenant:
            return redirect('painel_lojista', slug=user_tenant.slug)
        else:
            logout(request)
            return render(request, 'tenants/login.html', {'error': 'Você não tem permissão para acessar esta loja.'})
    
    if request.method == 'POST':
        try:
            table = get_object_or_404(Table, id=table_id, tenant=tenant)
            
            # Remove QR Code antigo se existir
            if table.qr_code:
                table.qr_code.delete(save=False)
            
            # Gera a URL que o QR Code vai指向
            base_url = request.build_absolute_uri('/').rstrip('/')
            table_url = f"{base_url}/{slug}/mesa/{table.number}/"
            
            # Gera o QR Code
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=2,
            )
            qr.add_data(table_url)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Salva a imagem
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)
            
            filename = f"table_{table.id}_{table.number}.png"
            table.qr_code.save(filename, buffer, save=True)
            
            return JsonResponse({
                'status': 'success',
                'qr_code': table.get_qr_code_url(),
                'table_url': table_url
            })
        except Exception as e:
            logger.error(f"Erro ao gerar QR Code: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


@login_required
def api_generate_all_qrcodes(request, slug):
    """
    Gera QR Codes para todas as mesas ativas da loja.
    """
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            tables = Table.objects.filter(tenant=tenant, is_active=True)
            base_url = request.build_absolute_uri('/').rstrip('/')
            
            results = []
            for table in tables:
                # Remove QR Code antigo se existir
                if table.qr_code:
                    table.qr_code.delete(save=False)
                
                # Gera a URL
                table_url = f"{base_url}/{slug}/mesa/{table.number}/"
                
                # Gera o QR Code
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=2,
                )
                qr.add_data(table_url)
                qr.make(fit=True)
                
                img = qr.make_image(fill_color="black", back_color="white")
                
                # Salva a imagem
                buffer = BytesIO()
                img.save(buffer, format="PNG")
                buffer.seek(0)
                
                filename = f"table_{table.id}_{table.number}.png"
                table.qr_code.save(filename, buffer, save=True)
                
                results.append({
                    'table_id': table.id,
                    'table_number': table.number,
                    'qr_code': table.get_qr_code_url()
                })
            
            return JsonResponse({
                'status': 'success',
                'message': f'{len(results)} QR Codes gerados com sucesso',
                'tables': results
            })
        except Exception as e:
            logger.error(f"Erro ao gerar QR Codes em massa: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


# ========================
# API DE CUPONS DE DESCONTO
# ========================

@login_required
def api_coupons(request, slug):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    # --- PROTEÇÃO DO PLANO ---
    if not tenant.can_access_coupons:
        return JsonResponse({'orders': [], 'plan_block': True, 'message': 'Faça upgrade para ver pedidos em tempo real.'})
    
    if request.method == 'GET':
        coupons = tenant.coupons.annotate(
            usage_count=Count('usages')
        ).values(
            'id', 'code', 'description', 'discount_type', 'discount_value',
            'minimum_order_value', 'usage_limit', 'used_count',
            'valid_from', 'valid_until', 'is_active'
        )
        return JsonResponse({'coupons': list(coupons)})
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
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
            
            if discount_type == 'percentage' and discount_value > 100:
                return JsonResponse({'status': 'error', 'message': 'Porcentagem não pode ser maior que 100%'}, status=400)
            
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


@login_required
def api_coupon_details(request, slug, coupon_id):
    tenant = get_object_or_404(Tenant, slug=slug)
    
    if tenant.owner != request.user and not request.user.is_superuser:
        return JsonResponse({'status': 'error', 'message': 'Acesso negado'}, status=403)
    
    coupon = get_object_or_404(Coupon, id=coupon_id, tenant=tenant)
    
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
    
    if request.method == 'DELETE':
        try:
            coupon.delete()
            return JsonResponse({'status': 'success'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': 'Erro ao excluir cupom'}, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


def api_validate_coupon(request, slug):
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
            
            coupon = Coupon.objects.filter(
                tenant=tenant,
                code=code
            ).first()
            
            if not coupon:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Cupom não encontrado'
                })
            
            is_valid, message = coupon.is_valid()
            if not is_valid:
                return JsonResponse({
                    'status': 'error',
                    'message': message
                })
            
            if coupon.minimum_order_value > 0 and order_value < float(coupon.minimum_order_value):
                return JsonResponse({
                    'status': 'error',
                    'message': f'Valor mínimo do pedido é R$ {float(coupon.minimum_order_value):.2f}'
                })
            
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
            
        except json.JSONDecodeError:
            logger.error("Erro ao decodificar JSON na validação de cupom")
            return JsonResponse({
                'status': 'error',
                'message': 'Dados inválidos'
            }, status=400)
        
        except ValueError as e:
            logger.warning(f"Erro de valor ao validar cupom: {e}")
            return JsonResponse({
                'status': 'error',
                'message': 'Valores inválidos'
            }, status=400)
        
        except Exception as e:
            logger.error(f"Erro inesperado ao validar cupom: {type(e).__name__} - {str(e)}", exc_info=True)
            return JsonResponse({
                'status': 'error',
                'message': 'Erro ao validar cupom. Tente novamente.'
            }, status=500)
    
    return JsonResponse({'status': 'error'}, status=400)


# --- PÁGINAS LEGAIS (Termos de Uso e Política de Privacidade) ---

def termos_de_uso(request):
    context = {
        'tenant': {
            'name': 'RM Pedidos',
            'phone_whatsapp': '(83) 92000-6113',
        }
    }
    return render(request, 'tenants/termos.html', context)


def politica_privacidade(request):
    context = {
        'tenant': {
            'name': 'RM Pedidos',
            'phone_whatsapp': '(83) 92000-6113',
            'address': '',
        }
    }
    return render(request, 'tenants/privacidade.html', context)


# ========================
# PWA - MANIFEST.JSON DINÂMICO
# ========================
def pwa_manifest(request, slug):
    """Retorna manifest.json customizado para cada loja (PWA) com ícones dinâmicos"""
    tenant = get_object_or_404(Tenant, slug=slug)
    
    # Determinar a cor primária
    primary_color = tenant.primary_color if tenant.primary_color else '#ea580c'
    color_hex = primary_color.replace('#', '')
    
    # Lógica de ícone: Logo da empresa OU UI Avatar baseado no nome
    if tenant.logo:
        logo_url = tenant.logo.url
    else:
        # Gera um ícone com a inicial da loja e a cor primária dela
        inicial = tenant.name[0].upper() if tenant.name else "C"
        logo_url = f"https://ui-avatars.com/api/?name={inicial}&background={color_hex}&color=fff&size=512&font-size=0.5"
    
    manifest = {
        'name': tenant.name,
        'short_name': tenant.name[:12],
        'description': f'Cardápio Digital - {tenant.name}',
        'start_url': f'/{slug}/',
        'scope': f'/{slug}/',
        'display': 'standalone',
        'orientation': 'portrait-primary',
        'background_color': '#ffffff',
        'theme_color': primary_color,
        'icons': [
            {
                'src': logo_url,
                'sizes': '192x192',
                'type': 'image/png',
                'purpose': 'any maskable' # Permite que o Android ajuste o formato
            },
            {
                'src': logo_url,
                'sizes': '512x512',
                'type': 'image/png',
                'purpose': 'any'
            }
        ],
        'screenshots': [
            {
                'src': logo_url,
                'sizes': '540x720',
                'type': 'image/png',
                'form_factor': 'narrow'
            }
        ],
        'categories': ['shopping', 'food'],
        'shortcuts': [
            {
                'name': 'Ver Cardápio',
                'short_name': 'Cardápio',
                'description': 'Abrir o cardápio da loja',
                'url': f'/{slug}/',
                'icons': [{'src': logo_url, 'sizes': '96x96', 'type': 'image/png'}]
            }
        ]
    }
    
    return JsonResponse(manifest)