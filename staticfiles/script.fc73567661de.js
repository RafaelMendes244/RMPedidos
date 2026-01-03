// --- CLASSE GERADORA DE PIX ---
class PixPayload {
    constructor(chave, nome, cidade, txid, valor) {
        this.chave = chave;
        this.nome = nome.substring(0, 25).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase();
        this.cidade = cidade.substring(0, 15).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase();
        this.txid = (txid || '***').toString();
        this.valor = valor.toFixed(2);
    }
    format(id, val) { return id + val.length.toString().padStart(2, '0') + val; }
    getPayload() {
        const payload = [
            this.format('00', '01'),
            this.format('26', this.format('00', 'BR.GOV.BCB.PIX') + this.format('01', this.chave)),
            this.format('52', '0000'),
            this.format('53', '986'),
            this.format('54', this.valor),
            this.format('58', 'BR'),
            this.format('59', this.nome),
            this.format('60', this.cidade),
            this.format('62', this.format('05', this.txid)),
            '6304'
        ].join('');
        return payload + this.crc16(payload);
    }
    crc16(buffer) {
        let crc = 0xFFFF;
        for (let i = 0; i < buffer.length; i++) {
            crc ^= buffer.charCodeAt(i) << 8;
            for (let j = 0; j < 8; j++) {
                if ((crc & 0x8000) !== 0) crc = crc << 1 ^ 0x1021;
                else crc = crc << 1;
            }
        }
        return (crc & 0xFFFF).toString(16).toUpperCase().padStart(4, '0');
    }
}

// --- VARIÁVEIS GLOBAIS ---
const CART_KEY = `carrinho_${window.TENANT_SLUG || 'padrao'}`;

const HISTORY_KEY = `historico_${window.TENANT_SLUG || 'padrao'}`;

let IS_STORE_OPEN = true;

let cart = JSON.parse(localStorage.getItem(CART_KEY)) || [];
let isDelivery = true;
let valorFreteAtual = 0;

// --- FUNÇÃO DE CÁLCULO DE TAXA DE ENTREGA (CORRIGIDA) ---
window.calcularTaxaEntrega = (bairroInput) => {
    if (!bairroInput) return;

    // Normaliza string (remove acentos e põe maiusculo)
    const normalize = (str) => str.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase().trim();
    
    const bairroCliente = normalize(bairroInput);
    const taxasConfiguradas = window.STORE_CONFIG?.deliveryFees || [];
    
    // Tenta encontrar o bairro na lista configurada
    const taxaEncontrada = taxasConfiguradas.find(item => normalize(item.neighborhood) === bairroCliente);
    
    if (taxaEncontrada) {
        valorFreteAtual = parseFloat(taxaEncontrada.fee);
        Toastify({ text: `🚚 Frete para ${bairroInput}: R$ ${valorFreteAtual.toFixed(2)}`, style: { background: "#ea580c" } }).showToast();
    } else {
        // Se não achar o bairro exato, taxa a combinar
        valorFreteAtual = 0;
        Toastify({ text: `⚠️ Bairro não tabelado. Taxa a combinar.`, style: { background: "#f59e0b" } }).showToast();
    }
    
    updateCartTotal();
};

// --- INICIALIZAÇÃO ---
document.addEventListener('DOMContentLoaded', () => {
    initDarkMode();
    updateCartCounter();
    setupEventListeners();
    // Verificar status da loja
    if (typeof checkRestaurantOpen === 'function') {
        checkRestaurantOpen();
    }
});

// --- FUNÇÃO CENTRAL: FINALIZAR PEDIDO ---
window.finalizeOrder = async () => {
    if (!IS_STORE_OPEN) {
        Toastify({ text: "⛔ A loja está fechada no momento.", style: { background: "#ef4444" } }).showToast();
        return;
    }

    const nome = document.getElementById("client-name").value.trim();
    if (!nome) { Toastify({text: "Digite seu nome", style: {background: "#ef4444"}}).showToast(); return; }

    const phone = document.getElementById("client-phone").value;
    const btnFinalize = document.getElementById("btn-finalize");
    const textoOriginal = btnFinalize.innerText;
    
    // Calcula totais
    let totalProdutos = cart.reduce((a, b) => a + (b.price * b.qtd), 0);
    const totalFinal = totalProdutos + (isDelivery ? valorFreteAtual : 0);
    
    // Dados do Pedido
    const methodEl = document.querySelector('input[name="payment-method"]:checked');
    const method = methodEl ? methodEl.value : "Dinheiro/Cartão";
    const obs = document.getElementById("order-notes").value;

    const orderData = {
        nome: nome,
        phone: phone,
        total: totalFinal,
        coupon_code: appliedCoupon ? appliedCoupon.code : null,
        method: method,
        obs: obs,
        items: cart,
        address: isDelivery ? {
            cep: document.getElementById("cep").value,
            street: document.getElementById("address").value,
            number: document.getElementById("number").value,
            neighborhood: document.getElementById("neighborhood").value
        } : {}
    };

    // --- DECISÃO DE FLUXO ---
    if (method === 'pix' && window.STORE_CONFIG.pixKey) {
        // FLUXO PIX: Mostrar Modal PRIMEIRO, Salvar DEPOIS
        const tempTxid = "PED" + Date.now().toString().slice(-6);
        
        const pix = new PixPayload(
            window.STORE_CONFIG.pixKey,
            window.STORE_CONFIG.pixName, 
            window.STORE_CONFIG.pixCity,
            tempTxid,
            totalFinal
        );
        const payload = pix.getPayload();

        // Abre o Modal
        const modalResult = await Swal.fire({
            title: '<span class="text-orange-600">Pagamento PIX</span>',
            html: `
                <p class="text-sm text-gray-600 mb-4">Escaneie o QR Code ou copie a chave:</p>
                <div class="flex justify-center mb-4 p-2 bg-white rounded-lg border border-gray-200">
                    <div id="qrcode-container"></div>
                </div>
                <div class="relative mb-4">
                    <textarea id="pix-copia-cola" readonly class="w-full h-12 bg-gray-100 p-2 text-[10px] rounded-lg border border-gray-300 resize-none outline-none">${payload}</textarea>
                    <button onclick="copiarPix()" class="absolute top-1 right-1 bg-blue-100 text-blue-600 px-2 py-1 rounded text-[10px] font-bold hover:bg-blue-200 transition">COPIAR</button>
                </div>
                <div class="text-center">
                    <p class="text-xs text-gray-400 font-bold uppercase mb-1">Valor Total</p>
                    <p class="text-2xl font-serif font-bold text-gray-800">R$ ${totalFinal.toFixed(2)}</p>
                </div>
            `,
            showCancelButton: true,
            confirmButtonText: '✅ Já Paguei / Enviar',
            cancelButtonText: '❌ Voltar / Cancelar',
            confirmButtonColor: '#10b981',
            cancelButtonColor: '#ef4444',
            allowOutsideClick: false,
            didOpen: () => {
                new QRCode(document.getElementById("qrcode-container"), {
                    text: payload, width: 140, height: 140, correctLevel: QRCode.CorrectLevel.L
                });
            }
        });

        if (modalResult.isConfirmed) {
            btnFinalize.innerText = "Enviando...";
            btnFinalize.disabled = true;
            processarSalvamento(orderData, btnFinalize, textoOriginal);
        } else {
            Toastify({ text: "Pagamento pendente. Escolha outra forma.", style: { background: "#fb923c" } }).showToast();
        }

    } else {
        // FLUXO CARTÃO/DINHEIRO: Salvar e Enviar direto
        btnFinalize.innerText = "Processando...";
        btnFinalize.disabled = true;
        processarSalvamento(orderData, btnFinalize, textoOriginal);
    }
};

// --- FUNÇÃO AUXILIAR PARA SALVAR NO DJANGO ---
async function processarSalvamento(orderData, btn, txtOriginal) {
    try {
        const response = await fetch(window.API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': window.CSRF_TOKEN },
            body: JSON.stringify(orderData)
        });

        const result = await response.json();

        if (result.status === 'success') {
            orderData.order_id = result.order_id;

            let history = JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
            if (!history.includes(result.order_id)) {
                history.push(result.order_id);
                localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
            }
            // Passa as informações do cupom aplicado para o WhatsApp
            if (appliedCoupon && appliedCoupon.discount_amount > 0) {
                // Calcula o total final com desconto
                orderData.final_total = orderData.total - appliedCoupon.discount_amount;
                orderData.appliedCoupon = {
                    code: appliedCoupon.code,
                    discount_amount: appliedCoupon.discount_amount
                };
            } else {
                orderData.final_total = orderData.total;
                orderData.appliedCoupon = null;
            }
            
            cart = [];
            saveCart();
            closeCart();
            Toastify({ text: "Pedido Enviado! 🚀", style: { background: "#10b981" } }).showToast();
            sendToWhatsApp(orderData);
        } else {
            throw new Error(result.message || "Erro no servidor");
        }
    } catch (error) {
        console.error(error);
        Toastify({ text: "Erro ao enviar: " + error.message, style: { background: "#ef4444" } }).showToast();
    } finally {
        btn.innerText = txtOriginal;
        btn.disabled = false;
    }
}

// --- RESTANTE DAS FUNÇÕES (UTITILITÁRIOS) ---

window.copiarPix = () => {
    const copyText = document.getElementById("pix-copia-cola");
    copyText.select();
    copyText.setSelectionRange(0, 99999);
    navigator.clipboard.writeText(copyText.value).then(() => {
        Toastify({ text: "Chave Copiada!", style: { background: "#3b82f6" } }).showToast();
    });
};

function sendToWhatsApp(order) {
    const line = "━━━━━━━━━━━━━━━━━━━━";
    // Usa o nome da loja ou fallback para o pixName
    const storeName = window.STORE_CONFIG.storeName || window.STORE_CONFIG.pixName || 'Loja';
    let msg = `🍽️ *PEDIDO #${order.order_id}* | ${storeName}\n`;
    msg += `${line}\n\n`;
    msg += `👤 *Cliente:* ${order.nome}\n`;
    msg += `📞 *Tel:* ${order.phone}\n\n`;
    
    msg += `🛒 *ITENS:*\n`;
    order.items.forEach(i => {
        msg += `▪️ ${i.qtd}x ${i.name} - R$ ${(i.price * i.qtd).toFixed(2)}\n`;
        if(i.options && i.options.length > 0) {
            msg += `   ╰ ➕ ${i.options.map(o => o.name).join(', ')}\n`;
        }
        if(i.obs) msg += `   ╰ 📝 ${i.obs}\n`;
    });
    
    if (Object.keys(order.address).length > 0) {
        msg += `\n📍 *Entrega:* ${order.address.street}, ${order.address.number} - ${order.address.neighborhood}\n`;
        if (valorFreteAtual > 0) msg += `🚚 Frete: R$ ${valorFreteAtual.toFixed(2)}\n`;
    } else {
        msg += `\n🏪 *Retirada no Balcão*\n`;
    }
    
    // Mostrar informações do cupom se aplicado
    if (order.appliedCoupon && order.appliedCoupon.discount_amount > 0) {
        msg += `\n🎫 *Cupom:* ${order.appliedCoupon.code}\n`;
        msg += `   └ Desconto: -R$ ${order.appliedCoupon.discount_amount.toFixed(2)}\n`;
    }
    
    // Usa o total com desconto se disponível, caso contrário usa o total normal
    const totalExibir = order.final_total !== undefined ? order.final_total : order.total;
    msg += `\n💰 *TOTAL: R$ ${totalExibir.toFixed(2)}*\n`;
    msg += `💳 Pagamento: ${order.method.toUpperCase()}\n`;
    
    if (order.obs) msg += `\n⚠️ Obs: ${order.obs}`;

    const phoneStore = window.STORE_CONFIG.phone || "550000000000";
    const url = `https://api.whatsapp.com/send?phone=${phoneStore}&text=${encodeURIComponent(msg)}`;
    window.open(url, '_blank');
    
    setTimeout(() => window.location.reload(), 1500); 
}

// Modais e Carrinho
window.showProductModal = (id) => {
    const product = window.PRODUCTS_DATA[id];
    if (!product) return;
    
    // GERA O HTML DAS OPÇÕES
    let optionsHtml = '';
    
    if (product.opcoes && product.opcoes.length > 0) {
        product.opcoes.forEach((opt, idx) => {
            let itemsHtml = '';
            opt.items.forEach((item, iIdx) => {
                const inputType = opt.type === 'radio' ? 'radio' : 'checkbox';
                const inputName = opt.type === 'radio' ? `opt_${idx}` : `opt_${idx}[]`;
                
                // Formata preço (+ R$ 2,00 ou Grátis)
                const priceText = item.price > 0 ? `+ R$ ${item.price.toFixed(2)}` : '';
                
                itemsHtml += `
                    <label class="flex items-center justify-between p-3 border border-gray-100 rounded-lg mb-2 cursor-pointer hover:bg-orange-50 transition">
                        <div class="flex items-center gap-3">
                            <input type="${inputType}" name="${inputName}" value="${item.name}::${item.price}" 
                                class="w-4 h-4 accent-orange-600" 
                                ${opt.required && iIdx === 0 && opt.type === 'radio' ? 'checked' : ''}
                                onchange="calculateModalTotal()">
                            <span class="text-sm text-gray-700 font-medium">${item.name}</span>
                        </div>
                        <span class="text-xs font-bold text-orange-600">${priceText}</span>
                    </label>
                `;
            });

            optionsHtml += `
                <div class="mb-6">
                    <div class="flex justify-between items-end mb-3">
                        <h4 class="font-bold text-gray-800">${opt.title}</h4>
                        ${opt.required ? '<span class="text-[10px] bg-gray-800 text-white px-2 py-0.5 rounded">OBRIGATÓRIO</span>' : '<span class="text-[10px] text-gray-400">OPCIONAL</span>'}
                    </div>
                    ${opt.type === 'checkbox' && opt.max > 1 ? `<p class="text-xs text-gray-400 mb-2">Escolha até ${opt.max} opções</p>` : ''}
                    <div class="space-y-1">${itemsHtml}</div>
                </div>
            `;
        });
    } else { 
        optionsHtml = '<p class="text-xs text-gray-400 italic mb-4">Sem opções adicionais.</p>'; 
    }
    Swal.fire({
        title: `<span class="font-serif text-2xl text-gray-900 dark:text-white">${product.name}</span>`,
        html: `
            <div class="text-left mt-1">
                <p class="text-sm text-gray-500 mb-6 border-b border-orange-100 pb-4">${product.description || ''}</p>
                <div class="max-h-[30vh] overflow-y-auto mb-4">${optionsHtml}</div>
                <div class="mt-4">
                    <label class="text-[10px] font-bold text-gray-400 uppercase mb-2 block">Observações</label>
                    <textarea id="modal-obs" class="w-full bg-orange-50 border border-orange-200 rounded-xl p-3 text-sm resize-none" rows="2" placeholder="Ex: Sem cebola..."></textarea>
                </div>
                <div class="flex justify-between items-center mt-6 pt-4 border-t border-slate-100">
                    <span class="text-xs font-bold uppercase text-gray-400">Total</span>
                    <span class="text-2xl font-serif text-gray-900 font-medium">R$ ${product.price.toFixed(2)}</span>
                </div>
            </div>
        `,
        imageUrl: product.image, imageWidth: '100%', imageHeight: 250, imageAlt: product.name,
        showCloseButton: true, showConfirmButton: true, confirmButtonText: 'ADICIONAR À SACOLA', confirmButtonColor: '#ea580c',
        showCancelButton: true, cancelButtonText: 'Voltar',
        customClass: { popup: 'rounded-3xl overflow-hidden shadow-2xl', image: 'w-full object-cover bg-orange-100' },
        preConfirm: () => { 
            const obs = document.getElementById('modal-obs').value;
            const selectedOptions = [];
            let extraPrice = 0;
            
            // Captura todas as opções selecionadas (radio e checkbox)
            const checkedInputs = document.querySelectorAll('.swal2-popup input[type="radio"]:checked, .swal2-popup input[type="checkbox"]:checked');
            checkedInputs.forEach(input => {
                const [name, price] = input.value.split('::');
                const priceNum = parseFloat(price) || 0;
                selectedOptions.push({ name, price: priceNum });
                extraPrice += priceNum;
            });
            
            return { obs, options: selectedOptions, extraPrice }; 
        }
    }).then(r => { if (r.isConfirmed) addToCart(product, r.value.obs, r.value.options, r.value.extraPrice); });
};

function addToCart(product, obs, options, extraPrice) {
    const finalPrice = product.price + (extraPrice || 0);
    const optionsKey = options.map(o => o.name).sort().join(',');
    const existing = cart.find(i => i.id === product.id && i.obs === obs && i.optionsKey === optionsKey);
    
    if (existing) {
        existing.qtd++;
    } else {
        cart.push({ 
            id: product.id, 
            name: product.name, 
            price: finalPrice, 
            basePrice: product.price,
            image: product.image, 
            obs, 
            options: options || [],
            optionsKey,
            qtd: 1 
        });
    }
    saveCart();
    Toastify({ text: "🍽️ Adicionado à sacola", style: { background: "#ea580c" } }).showToast();
}

function saveCart() { 
    localStorage.setItem(CART_KEY, JSON.stringify(cart)); 
    updateCartCounter(); 
}

function updateCartCounter() {
    const count = cart.reduce((a, b) => a + b.qtd, 0);
    const badge = document.getElementById("cart-count");
    const footer = document.getElementById("footer-cart");
    if (badge) { badge.innerText = count; badge.classList.toggle('scale-0', count === 0); }
    if (footer) footer.classList.toggle("hidden", count === 0);
    updateCartTotal();
}

window.renderCartItems = () => {
    const container = document.getElementById("cart-items");
    const emptyMsg = document.getElementById("empty-cart-msg");
    const btnNext = document.getElementById("btn-next-step");
    const cartHeader = document.getElementById("cart-header");
    container.innerHTML = "";
    if (cart.length === 0) { container.classList.add("hidden"); emptyMsg.classList.remove("hidden"); if(btnNext) btnNext.classList.add("hidden"); if(cartHeader) cartHeader.classList.add("hidden"); return; }
    container.classList.remove("hidden"); emptyMsg.classList.add("hidden"); if(btnNext) btnNext.classList.remove("hidden"); if(cartHeader) cartHeader.classList.remove("hidden");

    cart.forEach((item, idx) => {
        // Renderiza opcionais selecionados
        let optionsHtml = '';
        if (item.options && item.options.length > 0) {
            optionsHtml = `<p class="text-[10px] text-orange-600 truncate">+ ${item.options.map(o => o.name).join(', ')}</p>`;
        }
        
        const hasRealImage = item.image && !item.image.startsWith('data:image');
        
        const imageHtml = hasRealImage 
            ? `<img src="${item.image}" class="w-full h-full object-cover">`
            : `<div class="w-full h-full flex items-center justify-center bg-orange-50">
                    <i class="fas fa-utensils text-orange-300 text-xl"></i>
                </div>`;
        
        container.innerHTML += `
        <div class="flex gap-4 p-3 bg-white dark:bg-gray-800 rounded-2xl border border-orange-100 dark:border-gray-700 relative mb-3 shadow-sm">
            <div class="w-16 h-16 rounded-xl overflow-hidden bg-orange-100 shrink-0">${imageHtml}</div>
            <div class="flex-1 min-w-0 flex flex-col justify-between py-1">
                <div>
                    <h4 class="font-medium text-sm text-gray-900 dark:text-white truncate">${item.name}</h4>
                    ${optionsHtml}
                    ${item.obs ? `<p class="text-[10px] text-gray-400 italic truncate">"${item.obs}"</p>` : ''}
                </div>
                <div class="flex justify-between items-end">
                    <div class="flex items-center bg-gray-100 dark:bg-gray-700 rounded-lg h-6 px-1">
                        <button onclick="changeQtd(${idx}, -1)" class="w-6 h-full font-bold text-gray-500 hover:text-orange-600">-</button>
                        <span class="text-xs font-bold w-6 text-center text-gray-900 dark:text-white">${item.qtd}</span>
                        <button onclick="changeQtd(${idx}, 1)" class="w-6 h-full font-bold text-gray-500 hover:text-orange-600">+</button>
                    </div>
                    <span class="font-bold text-gray-900 dark:text-white text-sm">R$ ${(item.price * item.qtd).toFixed(2)}</span>
                </div>
            </div>
            <button onclick="removeItem(${idx})" class="absolute top-2 right-2 w-6 h-6 flex items-center justify-center rounded-full text-gray-300 hover:text-red-500 hover:bg-red-50 transition"><i class="fas fa-times text-xs"></i></button>
        </div>`;
    });
};

window.changeQtd = (i, d) => { if (d === -1 && cart[i].qtd === 1) { removeItem(i); return; } cart[i].qtd += d; saveCart(); window.renderCartItems(); };

window.removeItem = (i) => {
    const item = cart[i];
    
    // Usa o SweetAlert2 para criar um modal bonito e nativo
    Swal.fire({
        title: 'Remover item?',
        text: `Deseja retirar "${item.name}" da sacola?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        cancelButtonColor: '#3085d6',
        confirmButtonText: 'Sim, remover',
        cancelButtonText: 'Cancelar',
        width: 300 // Tamanho ideal para mobile
    }).then((result) => {
        if (result.isConfirmed) {
            // Animação de saída antes de remover (opcional, mas profissional)
            cart.splice(i, 1);
            saveCart();
            
            // Se o carrinho ficar vazio, fecha o modal ou mostra mensagem
            if (cart.length === 0) {
                // Opcional: fechar modal se esvaziar
                // window.closeCart(); 
            }
            
            window.renderCartItems();
            Toastify({ 
                text: "Item removido", 
                style: { background: "#ef4444", boxShadow: "none" },
                duration: 2000 
            }).showToast();
        }
    });
};

window.openCart = () => { document.getElementById("cart-modal").classList.remove("hidden"); document.body.classList.add("overflow-hidden"); window.renderCartItems(); document.getElementById("step-1-cart").classList.remove("hidden"); document.getElementById("step-2-address").classList.add("hidden"); document.getElementById("btn-finalize").classList.add("hidden"); document.getElementById("btn-next-step").classList.remove("hidden"); };
window.closeCart = () => { document.getElementById("cart-modal").classList.add("hidden"); document.body.classList.remove("overflow-hidden"); };

window.goToAddress = () => { 
    if (!IS_STORE_OPEN) {
        Toastify({ text: "⛔ A loja está fechada no momento.", style: { background: "#ef4444" } }).showToast();
        return;
    } 
    if (cart.length === 0) 
        return; 
    
    document.getElementById("step-1-cart").classList.add("hidden"); 
    document.getElementById("step-2-address").classList.remove("hidden"); 
    document.getElementById("btn-next-step").classList.add("hidden"); 
    document.getElementById("btn-finalize").classList.remove("hidden"); 
};

window.backToCart = () => { document.getElementById("step-2-address").classList.add("hidden"); document.getElementById("step-1-cart").classList.remove("hidden"); document.getElementById("btn-finalize").classList.add("hidden"); document.getElementById("btn-next-step").classList.remove("hidden"); };

window.toggleDelivery = (val) => {
    isDelivery = val;
    const btnDel = document.getElementById("btn-delivery"); const btnPick = document.getElementById("btn-pickup"); const addrCont = document.getElementById("address-container");
    if (val) { btnDel.classList.replace("text-gray-500", "text-orange-600"); btnDel.classList.add("shadow"); btnPick.classList.replace("text-orange-600", "text-gray-500"); btnPick.classList.remove("shadow"); addrCont.classList.remove("hidden"); } 
    else { btnPick.classList.replace("text-gray-500", "text-orange-600"); btnPick.classList.add("shadow"); btnDel.classList.replace("text-orange-600", "text-gray-500"); btnDel.classList.remove("shadow"); addrCont.classList.add("hidden"); valorFreteAtual = 0; }
    updateCartTotal();
};

function updateCartTotal() {
    let total = cart.reduce((a, b) => a + (b.price * b.qtd), 0);
    if (isDelivery) total += valorFreteAtual;
    const elFinal = document.getElementById("cart-total-final");
    const elPreview = document.getElementById("cart-total-preview");
    if (elFinal) elFinal.innerText = `R$ ${total.toFixed(2)}`;
    if (elPreview) elPreview.innerText = `R$ ${total.toFixed(2)}`;
}

window.toggleFavorite = (id) => Toastify({ text: "❤️ Favoritado", style: { background: "#ea580c" } }).showToast();
window.openStoreInfo = () => {
    document.getElementById("store-info-modal").classList.remove("hidden");
    document.body.classList.add("overflow-hidden");
};

window.closeStoreInfo = () => {
    document.getElementById("store-info-modal").classList.add("hidden");
    document.body.classList.remove("overflow-hidden");
};

// CONFIGURAÇAO PARA HISTORICO DO CLIENTE
window.openHistory = async () => {
    const modal = document.getElementById("history-modal");
    const content = document.getElementById("history-content");
    
    modal.classList.remove("hidden");
    document.body.classList.add("overflow-hidden");
    
    const localIds = JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
    
    if (localIds.length === 0) {
        content.innerHTML = `
            <div class="flex flex-col items-center justify-center h-full text-gray-400">
                <div class="w-16 h-16 bg-gray-200 rounded-full flex items-center justify-center mb-4">
                    <i class="fas fa-receipt text-2xl text-gray-400"></i>
                </div>
                <p class="font-medium">Nenhum pedido recente</p>
                <p class="text-xs mt-1">Seus pedidos aparecerão aqui.</p>
            </div>`;
        return;
    }

    content.innerHTML = `<div class="flex justify-center py-10"><i class="fas fa-spinner fa-spin text-orange-500 text-2xl"></i></div>`;

    try {
        const response = await fetch(`/${window.TENANT_SLUG}/api/my-orders/`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': window.CSRF_TOKEN
            },
            body: JSON.stringify({ order_ids: localIds })
        });
        
        const data = await response.json();
        
        if (data.status === 'success' && data.orders.length > 0) {
            content.innerHTML = ''; // Limpa loading
            
            data.orders.forEach(order => {
                // CORES IDÊNTICAS AO PRINT (Bolinhas e Fundos)
                let statusClass = "bg-gray-100 text-gray-600";
                let iconClass = "fa-clock";
                let statusText = order.status;

                // Mapeamento de Cores para ficar igual ao estilo "App"
                if(order.status_key === 'pendente') {
                    statusClass = "bg-orange-100 text-orange-600";
                    iconClass = "fa-circle-notch fa-spin";
                }
                else if(order.status_key === 'em_preparo') {
                    // AZULZINHO (Igual da foto)
                    statusClass = "bg-blue-100 text-blue-600";
                    iconClass = "fa-fire"; 
                }
                else if(order.status_key === 'saiu_entrega') {
                    statusClass = "bg-yellow-100 text-yellow-700";
                    iconClass = "fa-motorcycle";
                }
                else if(order.status_key === 'concluido') {
                    statusClass = "bg-green-100 text-green-600";
                    iconClass = "fa-check-circle";
                }
                else if(order.status_key === 'cancelado') {
                    statusClass = "bg-red-100 text-red-600";
                    iconClass = "fa-times-circle";
                }

                // Ícone de Tipo de Entrega (Igual da foto: cinza com ícone escuro)
                const typeIcon = order.is_delivery 
                    ? `<div class="bg-gray-100 px-3 py-1.5 rounded-lg flex items-center gap-2 text-xs font-bold text-gray-600">
                        <i class="fas fa-motorcycle text-gray-800"></i> Entrega
                        </div>`
                    : `<div class="bg-gray-100 px-3 py-1.5 rounded-lg flex items-center gap-2 text-xs font-bold text-gray-600">
                        <i class="fas fa-store text-gray-800"></i> Retirada
                        </div>`;

                // TEMPLATE DO CARD (VISUAL CLEAN)
                content.innerHTML += `
                    <div class="bg-white dark:bg-gray-800 rounded-2xl p-5 shadow-[0_2px_15px_-3px_rgba(0,0,0,0.07)] border border-gray-100 dark:border-gray-700 relative overflow-hidden">
                        
                        <div class="flex justify-between items-start mb-4">
                            <div>
                                <span class="text-xs font-bold text-gray-400 tracking-wider">#${order.id}</span>
                                <p class="text-[11px] text-gray-400 font-medium mt-0.5">${order.date}</p>
                            </div>
                            
                            <div class="px-3 py-1.5 rounded-full text-[10px] font-bold uppercase tracking-wide flex items-center gap-1.5 ${statusClass}">
                                <i class="fas ${iconClass}"></i> ${statusText}
                            </div>
                        </div>

                        <div class="mb-5">
                            <p class="text-sm font-medium text-gray-800 dark:text-gray-200 leading-relaxed">
                                ${formatItemsList(order.items_summary)}
                            </p>
                        </div>

                        <div class="flex justify-between items-center pt-2 border-t border-gray-50 dark:border-gray-700 mt-2">
                            ${typeIcon}
                            <span class="font-serif text-lg font-bold text-gray-900 dark:text-white">R$ ${order.total.toFixed(2)}</span>
                        </div>
                    </div>
                `;
            });
        } else {
            content.innerHTML = `<div class="text-center py-10 text-gray-500">Nenhum pedido encontrado.</div>`;
        }
    } catch (e) {
        console.error(e);
        content.innerHTML = `<div class="text-center py-10 text-red-400">Erro de conexão.</div>`;
    }
};

function formatItemsList(summary) {
    return summary.replace(/(\d+x)/g, '<span class="font-bold text-gray-900 dark:text-white">$1</span>');
}

window.closeHistory = () => {
    document.getElementById("history-modal").classList.add("hidden");
    document.body.classList.remove("overflow-hidden");
};

window.buscarCep = () => {
    const cep = document.getElementById("cep").value.replace(/\D/g, "");
    if (cep.length !== 8) return;
    fetch(`https://viacep.com.br/ws/${cep}/json/`).then(r => r.json()).then(d => {
        if(!d.erro) {
            document.getElementById("address").value = d.logradouro;
            document.getElementById("neighborhood").value = d.bairro;
            document.getElementById("number").focus();
            window.calcularTaxaEntrega(d.bairro);
        }
    });
};

window.habilitarEnderecoManual = () => {
    document.getElementById("address").removeAttribute("readonly");
    document.getElementById("neighborhood").removeAttribute("readonly");
    document.getElementById("address").focus();
    
    // Adiciona listener para calcular taxa quando digitar o bairro manualmente
    const neighborhoodInput = document.getElementById("neighborhood");
    neighborhoodInput.addEventListener('blur', () => {
        if (neighborhoodInput.value) {
            window.calcularTaxaEntrega(neighborhoodInput.value);
        }
    });
};

function initDarkMode() { if (localStorage.getItem('theme') === 'dark') document.body.classList.add('dark-mode'); }
function setupEventListeners() {
    const phone = document.getElementById("client-phone");
    if (phone) phone.addEventListener("input", (e) => {
        let v = e.target.value.replace(/\D/g,"");
        v = v.replace(/^(\d{2})(\d)/g,"($1) $2");
        v = v.replace(/(\d)(\d{4})$/,"$1-$2");
        e.target.value = v.substring(0, 15);
    });
}

// Verifica se a loja está aberta AGORA
// Verifica se a loja está aberta AGORA
function checkRestaurantOpen() {
    const statusEl = document.getElementById("status-text");
    const iconEl = document.getElementById("status-icon");
    const container = document.getElementById("status-loja-container");

    if (!window.STORE_CONFIG?.manualOpen) {
        setStatusClosed("FECHADO TEMPORARIAMENTE");
        updateCheckoutButtons(false);
        return false;
    }

    const schedule = window.STORE_CONFIG?.schedule;
    if (!schedule || Object.keys(schedule).length === 0) {
        setStatusOpen("ABERTO (Sem horário definido)");
        updateCheckoutButtons(true);
        return true;
    }

    const now = new Date();
    const diaHoje = now.getDay(); 
    const diaOntem = diaHoje === 0 ? 6 : diaHoje - 1;
    const horaAtualMin = now.getHours() * 60 + now.getMinutes();

    const getMinutes = (str) => {
        if(!str) return 0;
        const [h, m] = str.split(':').map(Number);
        return h * 60 + m;
    }

    function verificarRegra(regra) {
        if (!regra || regra.closed) return false;
        
        const openMin = getMinutes(regra.open);
        const closeMin = getMinutes(regra.close);
        
        if (closeMin < openMin) {
            return horaAtualMin >= openMin || horaAtualMin < closeMin;
        } else {
            return horaAtualMin >= openMin && horaAtualMin < closeMin;
        }
    }

    const regraOntem = schedule[diaOntem];
    if (regraOntem && !regraOntem.closed) {
        const ontemOpen = getMinutes(regraOntem.open);
        const ontemClose = getMinutes(regraOntem.close);
        
        if (ontemClose < ontemOpen) {
            if (verificarRegra(regraOntem)) {
                let closeTime = regraOntem.close;
                if (getMinutes(regraOntem.close) === 0) closeTime = "00:00";
                setStatusOpen(`ABERTO • Fecha às ${closeTime}`);
                updateCheckoutButtons(true);
                return true;
            }
        }
    }

    const regraHoje = schedule[diaHoje];
    if (verificarRegra(regraHoje)) {
        let closeTime = regraHoje.close;
        if (getMinutes(regraHoje.close) === 0) closeTime = "00:00";
        setStatusOpen(`ABERTO • Fecha às ${closeTime}`);
        updateCheckoutButtons(true);
        return true;
    }

    let msg = "FECHADO AGORA";
    if (regraHoje && !regraHoje.closed && regraHoje.open) {
        const openMin = getMinutes(regraHoje.open);
        if (horaAtualMin < openMin) {
            msg = `FECHADO AGORA - ABRE ÀS ${regraHoje.open}`;
        }
    }
    setStatusClosed(msg);
    updateCheckoutButtons(false);
    return false;
}

function setStatusClosed(msg) {
    const statusEl = document.getElementById("status-text");
    const iconEl = document.getElementById("status-icon");
    const container = document.getElementById("status-loja-container");
    if(statusEl) statusEl.innerText = msg;
    if(iconEl) iconEl.className = "fas fa-circle text-[8px] text-red-500";
    if(container) container.className = "inline-flex items-center gap-3 px-5 py-2.5 rounded-full bg-black/40 backdrop-blur-md border border-red-500/30 text-xs font-bold shadow-lg mb-8 transition hover:bg-black/50";
}

function setStatusOpen(msg) {
    const statusEl = document.getElementById("status-text");
    const iconEl = document.getElementById("status-icon");
    const container = document.getElementById("status-loja-container");
    if(statusEl) statusEl.innerText = msg;
    if(iconEl) iconEl.className = "fas fa-circle text-[8px] animate-pulse text-green-400";
    if(container) container.className = "inline-flex items-center gap-3 px-5 py-2.5 rounded-full bg-black/40 backdrop-blur-md border border-green-500/30 text-xs font-bold shadow-lg mb-8 transition hover:bg-black/50";
}

// Atualiza a cada minuto
setInterval(checkRestaurantOpen, 60000);

// ========================
// FUNÇÕES DE CUPOM DE DESCONTO
// ========================

let appliedCoupon = null;

window.applyCoupon = async () => {
    const codeInput = document.getElementById("coupon-code");
    const messageEl = document.getElementById("coupon-message");
    const appliedEl = document.getElementById("coupon-applied");
    const codeDisplay = document.getElementById("coupon-applied-code");
    
    const code = codeInput.value.trim().toUpperCase();
    if (!code) {
        showCouponMessage("Digite um código de cupom", "error");
        return;
    }
    
    // Calcula o valor atual dos produtos (sem frete)
    let subtotal = cart.reduce((a, b) => a + (b.price * b.qtd), 0);
    
    try {
        const response = await fetch(`/${window.TENANT_SLUG}/api/coupons/validate/`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'X-CSRFToken': window.CSRF_TOKEN
            },
            body: JSON.stringify({
                code: code,
                order_value: subtotal
            })
        });
        
        const result = await response.json();
        
        if (result.status === 'success') {
            // Cupom válido!
            appliedCoupon = {
                code: result.coupon.code,
                discount_amount: result.coupon.discount_amount,
                final_value: result.coupon.final_value
            };
            
            // Atualiza UI
            codeInput.value = '';
            appliedEl.classList.remove('hidden');
            codeDisplay.innerText = result.coupon.code;
            
            showCouponMessage(`${result.coupon.description || 'Cupom aplicado!'} (-R$ ${result.coupon.discount_amount.toFixed(2)})`, "success");
            
            // Atualiza o total
            updateCartTotal();
        } else {
            // Cupom inválido
            appliedCoupon = null;
            appliedEl.classList.add('hidden');
            showCouponMessage(result.message, "error");
            updateCartTotal();
        }
    } catch (error) {
        console.error("Erro ao validar cupom:", error);
        showCouponMessage("Erro ao validar cupom. Tente novamente.", "error");
    }
};

window.removeCoupon = () => {
    appliedCoupon = null;
    document.getElementById("coupon-applied").classList.add("hidden");
    document.getElementById("coupon-code").value = '';
    updateCartTotal();
};

function showCouponMessage(msg, type) {
    const el = document.getElementById("coupon-message");
    el.innerText = msg;
    el.className = `text-xs mt-2 ${type === 'success' ? 'text-green-600 font-bold' : 'text-red-500'}`;
    el.classList.remove('hidden');
    
    if (type === 'success') {
        setTimeout(() => el.classList.add('hidden'), 3000);
    }
}

// Sobrescreve updateCartTotal para considerar desconto do cupom
const originalUpdateCartTotal = updateCartTotal;
updateCartTotal = function() {
    let total = cart.reduce((a, b) => a + (b.price * b.qtd), 0);
    if (isDelivery) total += valorFreteAtual;
    
    const elFinal = document.getElementById("cart-total-final");
    const elPreview = document.getElementById("cart-total-preview");
    const discountEl = document.getElementById("discount-display");
    const discountAmountEl = document.getElementById("discount-amount");
    
    if (appliedCoupon) {
        const finalWithDiscount = total - appliedCoupon.discount_amount;
        
        if (elFinal) elFinal.innerText = `R$ ${finalWithDiscount.toFixed(2)}`;
        if (elPreview) elPreview.innerText = `R$ ${finalWithDiscount.toFixed(2)}`;
        
        if (discountEl) {
            discountEl.classList.remove('hidden');
            discountAmountEl.innerText = `-R$ ${appliedCoupon.discount_amount.toFixed(2)}`;
        }
    } else {
        if (elFinal) elFinal.innerText = `R$ ${total.toFixed(2)}`;
        if (elPreview) elPreview.innerText = `R$ ${total.toFixed(2)}`;
        if (discountEl) discountEl.classList.add('hidden');
    }
};

function updateCheckoutButtons(isOpen) {
    IS_STORE_OPEN = isOpen; // Atualiza o estado global
    
    const btnNext = document.getElementById("btn-next-step");
    const btnFinalize = document.getElementById("btn-finalize");
    
    // Lista de botões para aplicar a lógica
    const buttons = [btnNext, btnFinalize];

    buttons.forEach(btn => {
        if (!btn) return;

        if (isOpen) {
            // Lógica para quando a loja está ABERTA
            btn.disabled = false;
            btn.classList.remove("bg-gray-400", "cursor-not-allowed");
            
            // Restaura as cores originais baseadas no ID do botão
            if (btn.id === "btn-next-step") {
                btn.classList.add("bg-orange-600", "hover:bg-orange-700");
                btn.innerText = "Continuar";
            } else {
                btn.classList.add("bg-green-600", "hover:bg-green-700");
                // Mantém o texto original do finalizar se não estivermos alterando
                if(btn.innerText === "Loja Fechada") btn.innerText = "Finalizar Pedido no WhatsApp";
            }
        } else {
            // Lógica para quando a loja está FECHADA
            btn.disabled = true;
            btn.classList.remove("bg-orange-600", "hover:bg-orange-700", "bg-green-600", "hover:bg-green-700");
            btn.classList.add("bg-gray-400", "cursor-not-allowed");
            btn.innerText = "Loja Fechada";
        }
    });
}

window.clearCart = () => {
    if (cart.length === 0) return;
    
    Swal.fire({
        title: 'Esvaziar sacola?',
        text: "Todos os itens serão removidos.",
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        cancelButtonColor: '#3085d6',
        confirmButtonText: 'Sim, limpar',
        cancelButtonText: 'Cancelar'
    }).then((result) => {
        if (result.isConfirmed) {
            cart = [];
            saveCart();
            window.renderCartItems();
            window.closeCart(); // Opcional
            Toastify({ text: "Sacola limpa!", style: { background: "#ef4444" } }).showToast();
        }
    });
}