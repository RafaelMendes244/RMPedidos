const CACHE_NAME = 'saaspedidos-v1';
const urlsToCache = [
  '/',
  '/static/script.js',
  '/static/dark-mode.css',
];

// Instalar o service worker e cacheá los arquivos
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return cache.addAll(urlsToCache).catch(err => {
          console.log('Erro ao cachear arquivos:', err);
        });
      })
  );
  self.skipWaiting();
});

// Ativar o service worker e limpar caches antigos
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Interceptar requisições e servir do cache
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

// Sincronização de pedidos em background
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
    console.error('Erro ao sincronizar pedidos:', error);
  }
}

// Notificações push
self.addEventListener('push', event => {
  if (event.data) {
    const options = {
      body: event.data.text(),
      icon: '/static/images/icon-192x192.png',
      badge: '/static/images/badge-72x72.png',
      tag: 'saaspedidos-notification',
      requireInteraction: true
    };

    event.waitUntil(
      self.registration.showNotification('Pedido Recebido', options)
    );
  }
});

// Clicar na notificação
self.addEventListener('notificationclick', event => {
  event.notification.close();
  
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then(clientList => {
      for (let client of clientList) {
        if (client.url === '/' && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow('/');
      }
    })
  );
});
