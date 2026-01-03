from django.shortcuts import get_object_or_404
from django.http import Http404
from .models import Tenant # Ajuste o import conforme o nome do seu app

class DomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Pega o domínio limpo (sem porta, ex: pizzariadoze.com)
        host = request.get_host().split(':')[0].lower()
        
        # Lista dos SEUS domínios oficiais (que devem mostrar a Landing Page)
        my_domains = ['rmpedidos.online', 'www.rmpedidos.online', 'localhost', '127.0.0.1']

        request.tenant_from_domain = None

        if host not in my_domains:
            # Se não é o seu domínio principal, TENTA achar uma loja
            try:
                # Remove o 'www.' se tiver, para evitar duplicidade
                clean_host = host.replace('www.', '')
                
                # Busca a loja pelo domínio personalizado
                tenant = Tenant.objects.get(custom_domain=clean_host)
                
                # Salva a loja na requisição para usarmos na View
                request.tenant_from_domain = tenant
                
            except Tenant.DoesNotExist:
                # Se o domínio aponta pro seu IP mas não tem loja cadastrada
                pass 

        return self.get_response(request)