// ============================================
// SERVICE WORKER UNIFICADO - CARDÁPIO DIGITAL
// Cache PWA + Push Notifications
// ============================================

const CACHE_NAME = 'saaspedidos-v1';
const urlsToCache = [
  '/',
  '/static/script.js',
  '/static/dark-mode.css',
];

// ============================================
// INSTALAÇÃO E CACHE
// ============================================
self.addEventListener('install', event => {
  console.log('[SW] Instalando Service Worker...');
  
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('[SW] Cacheando arquivos...');
        return cache.addAll(urlsToCache).catch(err => {
          console.log('[SW] Erro ao cachear arquivos:', err);
        });
      })
  );
  
  self.skipWaiting();
  console.log('[SW] Instalado e pronto para controlar clientes');
});

// ============================================
// ATIVAÇÃO E LIMPEZA
// ============================================
self.addEventListener('activate', event => {
  console.log('[SW] Ativando Service Worker...');
  
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            console.log('[SW] Deletando cache antigo:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  
  self.clients.claim();
  console.log('[SW] Ativado e controlling todos os clientes');
});

// ============================================
// INTERCEPTAR REQUISIÇÕES (CACHE)
// ============================================
self.addEventListener('fetch', event => {
  // Não fazer cache de requisições POST ou APIs
  if (event.request.method !== 'GET') {
    return;
  }

  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Se estiver em cache, retornar do cache
        if (response) {
          return response;
        }

        // Caso contrário, fazer requisição de rede
        return fetch(event.request)
          .then(response => {
            // Se a resposta for válida, adicionar ao cache
            if (!response || response.status !== 200) {
              return response;
            }

            const responseToCache = response.clone();
            caches.open(CACHE_NAME)
              .then(cache => {
                cache.put(event.request, responseToCache);
              });

            return response;
          })
          .catch(() => {
            // Se falhar, tentar servir do cache
            return caches.match(event.request);
          });
      })
  );
});

// ============================================
// PUSH NOTIFICATIONS
// ============================================
self.addEventListener('push', event => {
  console.log('[SW] Push recebido');
  
  // Dados padrão
  let pushData = {
    title: '',
    body: 'Você tem uma nova notificação!',
    icon: '/static/img/menu-icon.png',
    badge: '/static/img/badge-72.png',
    url: '/',
    tag: 'saaspedidos-notification'
  };
  
  if (event.data) {
    try {
      // O pywebpush já envia o payload como objeto JSON
      const data = event.data.json();
      console.log('[SW] Payload type:', typeof data);
      
      if (data && typeof data === 'object') {
        pushData.title = data.title || pushData.title;
        pushData.body = data.body || pushData.body;
        pushData.icon = data.icon || pushData.icon;
        pushData.badge = data.badge || pushData.badge;
        pushData.url = data.url || pushData.url;
        pushData.tag = data.tag || pushData.tag;
      }
      
      console.log('[SW] Título:', pushData.title);
      console.log('[SW] Body:', pushData.body);
      
    } catch (e) {
      console.log('[SW] Erro no parse JSON:', e);
      try {
        // Fallback para texto direto
        pushData.body = event.data.text();
      } catch (e2) {
        console.log('[SW] Erro ao obter texto:', e2);
      }
    }
  }
  
  const options = {
    body: pushData.body,
    icon: pushData.icon,
    badge: pushData.badge,
    tag: pushData.tag,
    requireInteraction: true,
    data: {
      url: pushData.url
    },
    actions: [
      { action: 'open', title: 'Conferir' },
      { action: 'close', title: 'Fechar' }
    ],
    vibrate: [100, 50, 100]
  };

  event.waitUntil(
    self.registration.showNotification(pushData.title, options)
  );
});

// ============================================
// CLICAR NA NOTIFICAÇÃO
// ============================================
self.addEventListener('notificationclick', event => {
  console.log('[SW] Notificação clicada:', event.action);
  
  event.notification.close();
  
  if (event.action === 'close') {
    return;
  }
  
  const urlToOpen = event.notification.data?.url || '/';
  
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(clientList => {
        // Verificar se já há uma janela aberta com a mesma URL
        for (let client of clientList) {
          if (client.url === urlToOpen && 'focus' in client) {
            return client.focus();
          }
        }
        // Abrir nova janela se não houver nenhuma
        if (clients.openWindow) {
          return clients.openWindow(urlToOpen);
        }
      })
  );
});

// ============================================
// FECHAR NOTIFICAÇÃO
// ============================================
self.addEventListener('notificationclose', event => {
  console.log('[SW] Notificação fechada:', event.notification.tag);
});

// ============================================
// MENSAGENS DO CLIENTE
// ============================================
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// ============================================
// SYNC DE PEDIDOS (BACKGROUND)
// ============================================
self.addEventListener('sync', event => {
  if (event.tag === 'sync-orders') {
    event.waitUntil(syncOrders());
  }
});

async function syncOrders() {
  try {
    const db = await idb.openDB('saaspedidos');
    const orders = await db.getAll('orders');
    
    for (let order of orders) {
      if (!order.synced) {
        const response = await fetch(order.url, {
          method: 'POST',
          body: JSON.stringify(order.data)
        });
        
        if (response.ok) {
          order.synced = true;
          await db.put('orders', order);
        }
      }
    }
  } catch (error) {
    console.error('[SW] Erro ao sincronizar pedidos:', error);
  }
}
