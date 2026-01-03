from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from .models import Tenant

class StaticViewSitemap(Sitemap):
    priority = 0.6
    changefreq = 'weekly'

    def items(self):
        # Nomes das rotas estáticas que você quer indexar
        return ['home', 'signup', 'custom_login', 'termos', 'privacidade']

    def location(self, item):
        return reverse(item)

class TenantSitemap(Sitemap):
    priority = 0.9
    changefreq = 'daily'

    def items(self):
        # Apenas lojas ABERTAS devem aparecer no Google
        return Tenant.objects.filter(is_open=True)

    def location(self, obj):
        # Gera o link /slug-da-loja/
        return reverse('cardapio_publico', args=[obj.slug])