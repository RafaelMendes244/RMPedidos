from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

# Imports para SEO
from django.contrib.sitemaps.views import sitemap
from tenants.sitemaps import StaticViewSitemap, TenantSitemap
from django.views.generic.base import TemplateView

sitemaps = {
    'static': StaticViewSitemap,
    'tenants': TenantSitemap,
}

urlpatterns = [
    path('admindegeral/', admin.site.urls),
    
    # Rota do Sitemap.xml
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    
    # Rota do Robots.txt
    path('robots.txt', TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),

    path('', include('tenants.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)