from django.contrib import admin
from django.urls import path, include # <--- Adicione o include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('tenants.urls')), # <--- Todas as urls do app tenants
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)