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
const DELIVERY_KEY = `entrega_${window.TENANT_SLUG || 'padrao'}`;

let IS_STORE_OPEN = true;

let cart = JSON.parse(localStorage.getItem(CART_KEY)) || [];
let isDelivery = JSON.parse(localStorage.getItem(DELIVERY_KEY)) !== false; // true por padrão
let valorFreteAtual = 0;
let ultimoValorFreteValido = 0; // Armazena o último valor válido do frete

// Variável global para número da mesa (definida no cardapio.html)
window.TABLE_NUMBER = null;

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
        ultimoValorFreteValido = valorFreteAtual; 
        Toastify({ text: `Frete para ${bairroInput}: R$ ${valorFreteAtual.toFixed(2)}`, style: { background: getPrimaryColor() } }).showToast();
    } else {
        // Se não achar o bairro, zera o frete
        valorFreteAtual = 0;
        // Não atualizamos o ultimoValorFreteValido aqui para não travar lógicas futuras, 
        // apenas zeramos o atual para mostrar "A combinar" ou sumir.
        Toastify({ text: `Bairro não tabelado. Taxa a combinar.`, style: { background: getPrimaryColor() } }).showToast();
    }
    
    // Atualizar display da taxa de entrega
    updateDeliveryFeeDisplay();
    updateCartTotal();
};

// Função para atualizar o display da taxa de entrega
function updateDeliveryFeeDisplay() {
    const deliveryFeeRow = document.getElementById("delivery-fee-row");
    const deliveryFeeEl = document.getElementById("cart-delivery-fee");
    
    if (!deliveryFeeRow || !deliveryFeeEl) return;
    
    if (isDelivery && valorFreteAtual > 0) {
        deliveryFeeRow.classList.remove("hidden");
        deliveryFeeEl.innerText = `R$ ${valorFreteAtual.toFixed(2)}`;
    } else if (isDelivery && valorFreteAtual === 0) {
        // Bairro não tabelado ou ainda não calculado
        deliveryFeeRow.classList.remove("hidden");
        deliveryFeeEl.innerText = "A combinar";
        deliveryFeeEl.classList.add("text-orange-500");
    } else {
        deliveryFeeRow.classList.add("hidden");
    }
}

// --- INICIALIZAÇÃO ---
document.addEventListener('DOMContentLoaded', () => {
    updateCartCounter();
    setupEventListeners();
    
    // Inicializa estado dos botoes de entrega/retirada
    const btnDel = document.getElementById("btn-delivery");
    const btnPick = document.getElementById("btn-pickup");
    if (isDelivery && btnDel && btnPick) {
        btnDel.classList.add("bg-white", "text-orange-600");
        btnDel.classList.remove("text-gray-500");
        btnPick.classList.remove("bg-white", "text-orange-600");
        btnPick.classList.add("text-gray-500");
    } else if (!isDelivery && btnDel && btnPick) {
        btnPick.classList.add("bg-white", "text-orange-600");
        btnPick.classList.remove("text-gray-500");
        btnDel.classList.remove("bg-white", "text-orange-600");
        btnDel.classList.add("text-gray-500");
    }
    
    // Verificar status da loja
    if (typeof checkRestaurantOpen === 'function') {
        checkRestaurantOpen();
    }
});

// --- FUNÇÃO CENTRAL: FINALIZAR PEDIDO ---
window.finalizeOrder = async () => {
    if (!IS_STORE_OPEN) {
        Toastify({ text: "A loja esta fechada no momento.", style: { background: "#ef4444" } }).showToast();
        return;
    }
    
    if (cart.length === 0) {
        Toastify({ text: "Sua sacola está vazia!", style: { background: "#ef4444" } }).showToast();
        return;
    }

    const nome = document.getElementById("client-name").value.trim();
    if (!nome || nome.length < 2) { Toastify({text: "Digite seu nome completo", style: {background: "#ef4444"}}).showToast(); return; }

    const phone = document.getElementById("client-phone").value.trim();
    if (!phone || phone.replace(/\D/g, "").length < 10) { Toastify({text: "Telefone inválido", style: {background: "#ef4444"}}).showToast(); return; }
    const btnFinalize = document.getElementById("btn-finalize");
    const textoOriginal = btnFinalize.innerText;
    
    // Calcula totais
    let totalProdutos = cart.reduce((a, b) => a + (b.price * b.qtd), 0);
    let totalComFrete = totalProdutos + (isDelivery ? valorFreteAtual : 0);
    
    // Aplica desconto do cupom se existir
    let discountAmount = 0;
    let totalFinal = totalComFrete;
    
    if (appliedCoupon && appliedCoupon.discount_amount) {
        discountAmount = appliedCoupon.discount_amount;
        totalFinal = totalComFrete - discountAmount;
        
        // Garante que nao fique negativo
        if (totalFinal < 0) totalFinal = 0;
    }
    
    // --- LÓGICA DE PAGAMENTO E TROCO (ATUALIZADA) ---
    const methodEl = document.querySelector('input[name="payment-method"]:checked');
    const method = methodEl ? methodEl.value : "Não Informado";
    let obs = document.getElementById("order-notes").value;

    // Validar endereço para entregas ANTES de criar pedido
    if (isDelivery) {
        const cepVal = document.getElementById("cep").value.replace(/\D/g, "");
        const addressVal = document.getElementById("address").value.trim();
        const numberVal = document.getElementById("number").value.trim();
        const neighborhoodVal = document.getElementById("neighborhood").value.trim();
        
        if (!cepVal || cepVal.length !== 8) {
            Toastify({text: "CEP inválido ou incompleto", style: {background: "#ef4444"}}).showToast();
            return;
        }
        if (!addressVal) {
            Toastify({text: "Rua é obrigatória", style: {background: "#ef4444"}}).showToast();
            return;
        }
        if (!numberVal) {
            Toastify({text: "Número é obrigatório", style: {background: "#ef4444"}}).showToast();
            return;
        }
        if (!neighborhoodVal) {
            Toastify({text: "Bairro é obrigatório", style: {background: "#ef4444"}}).showToast();
            return;
        }
    }
    
    // Se for dinheiro, processa o troco ANTES de criar o pedido
    if (method === 'dinheiro') {
        const trocoInput = document.getElementById("troco-valor");
        const trocoVal = trocoInput ? trocoInput.value : "";
        
        if (trocoVal) {
            // Converte virgula para ponto para garantir calculo certo
            const valorTroco = parseFloat(trocoVal.replace(',', '.'));
            
            // VALIDAÇÃO: O troco não pode ser menor que o total do pedido
            if (isNaN(valorTroco) || valorTroco < totalFinal) {
                Toastify({
                    text: `O valor do troco (R$ ${valorTroco.toFixed(2)}) é menor que o total do pedido!`, 
                    duration: 4000,
                    style: {background: "#ef4444"}
                }).showToast();
                document.getElementById("troco-valor").focus();
                return;
            }
            
            // Adiciona na observação para salvar no banco sem mexer no backend
            obs += `\n --- \n LEVAR TROCO PARA: R$ ${valorTroco.toFixed(2)}`;
        } else {
            obs += `\n --- \n NÃO PRECISA DE TROCO (Cliente tem o valor trocado)`;
        }
    }
    // ------------------------------------------------

    const orderData = {
        nome: nome,
        phone: phone,
        total: totalFinal,
        original_total: totalComFrete,
        discount_amount: discountAmount,
        coupon_code: appliedCoupon ? appliedCoupon.code : null,
        method: method,
        obs: obs, // Aqui já vai com o texto do troco
        items: cart,
        order_type: window.TABLE_NUMBER ? 'table' : (isDelivery ? 'delivery' : 'pickup'),
        address: isDelivery ? {
            cep: document.getElementById("cep").value,
            street: document.getElementById("address").value,
            number: document.getElementById("number").value,
            neighborhood: document.getElementById("neighborhood").value
        } : {},
        table_number: window.TABLE_NUMBER
    };

    // --- DECISAO DE FLUXO ---
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
            title: `<span class="text-primary">Pagamento PIX</span>`,
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
                    ${discountAmount > 0 ? `<p class="text-xs text-green-600 font-bold">Desconto: -R$ ${discountAmount.toFixed(2)}</p>` : ''}
                </div>
            `,
            showCancelButton: true,
            confirmButtonText: 'Ja Paguei / Enviar',
            cancelButtonText: 'Voltar / Cancelar',
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
            Toastify({ text: "Pagamento pendente. Escolha outra forma.", style: { background: getPrimaryColor() } }).showToast();
        }

    } else {
        // FLUXO CARTAO/DINHEIRO: Salvar e Enviar direto
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
            
            // Prepara dados do cupom para o WhatsApp
            if (orderData.coupon_code && orderData.discount_amount > 0) {
                orderData.appliedCoupon = {
                    code: orderData.coupon_code,
                    discount_amount: orderData.discount_amount
                };
            } else {
                orderData.appliedCoupon = null;
            }
            
            cart = [];
            saveCart();
            closeCart();
            Toastify({ text: "Pedido Enviado!", style: { background: "#10b981" } }).showToast();
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

// --- RESTANTE DAS FUNÇÕES (UTITILITARIOS) ---

window.copiarPix = () => {
    const copyText = document.getElementById("pix-copia-cola");
    copyText.select();
    copyText.setSelectionRange(0, 99999);
    navigator.clipboard.writeText(copyText.value).then(() => {
        Toastify({ text: "Chave Copiada!", style: { background: "#3b82f6" } }).showToast();
    });
};

// Return tenant primary color from CSS var or fallback
function getPrimaryColor(){
    const v = getComputedStyle(document.documentElement).getPropertyValue('--primary');
    if (v && v.trim()) return v.trim();
    if (window.TENANT_PRIMARY) return window.TENANT_PRIMARY;
    return '#ea580c';
}

function sendToWhatsApp(order) {
    const line = "━━━━━━━━━━━━━━━━━━━━";
    const storeName = window.STORE_CONFIG.storeName || window.STORE_CONFIG.pixName || 'Loja';
    let msg = `PEDIDO #${order.order_id} | ${storeName}\n`;
    msg += `${line}\n\n`;
    msg += `Cliente: ${order.nome}\n`;
    msg += `Tel: ${order.phone}\n\n`;
    
    msg += `ITENS:\n`;
    order.items.forEach(i => {
        msg += `${i.qtd}x ${i.name} - R$ ${(i.price * i.qtd).toFixed(2)}\n`;
        if(i.options && i.options.length > 0) {
            msg += `   + ${i.options.map(o => o.name).join(', ')}\n`;
        }
        if(i.obs) msg += `   Obs: ${i.obs}\n`;
    });
    
    // VERIFICA SE É MESA PRIMEIRO
    if (order.table_number) {
        msg += `\n📍 PEDIDO NA MESA ${order.table_number}\n`;
        msg += `(Cliente aguardando na mesa)\n`;
    } 
    // SE NÃO FOR MESA, VERIFICA ENDEREÇO
    else if (order.address && Object.keys(order.address).length > 0 && order.address.street) {
        msg += `\nEntrega: ${order.address.street}, ${order.address.number} - ${order.address.neighborhood}\n`;
        if (valorFreteAtual > 0) msg += `Frete: R$ ${valorFreteAtual.toFixed(2)}\n`;
        if (window.STORE_CONFIG?.deliveryTime) {
            msg += `Tempo Estimado: ${window.STORE_CONFIG.deliveryTime} min\n`;
        }
    } 
    // SE NÃO TEM MESA NEM ENDEREÇO, É RETIRADA
    else {
        msg += `\nRetirada no Balcão\n`;
        if (window.STORE_CONFIG?.pickupTime) {
            msg += `Tempo Estimado: ${window.STORE_CONFIG.pickupTime} min\n`;
        }
    }
    
    // Mostrar informacoes do cupom se aplicado
    if (order.appliedCoupon && order.appliedCoupon.discount_amount > 0) {
        msg += `\nCupom: ${order.appliedCoupon.code}\n`;
        msg += `   Desconto: -R$ ${order.appliedCoupon.discount_amount.toFixed(2)}\n`;
    }
    
    // order.total ja vem com o desconto aplicado
    msg += `\nTOTAL: R$ ${order.total.toFixed(2)}\n`;
    
    // Formata o método de pagamento para exibição
    let paymentDisplay = order.method ? order.method.toUpperCase() : 'NÃO INFORMADO';
    if (paymentDisplay === 'CARTAO_DINHEIRO') {
        paymentDisplay = 'CARTÃO/DINHEIRO';
    }
    msg += `Pagamento: ${paymentDisplay}\n`;
    
    if (order.obs) msg += `\nObs: ${order.obs}`;

    const phoneStore = window.STORE_CONFIG.phone || "550000000000";
    const url = `https://api.whatsapp.com/send?phone=${phoneStore}&text=${encodeURIComponent(msg)}`;
    window.open(url, '_blank');
    
    setTimeout(() => window.location.reload(), 1500); 
}

// Modais e Carrinho
window.showProductModal = (id) => {
    const product = window.PRODUCTS_DATA[id];
    if (!product) return;
    
    // GERA O HTML DAS OPCOES
    let optionsHtml = '';
    
    if (product.opcoes && product.opcoes.length > 0) {
        product.opcoes.forEach((opt, idx) => {
            let itemsHtml = '';
            opt.items.forEach((item, iIdx) => {
                const inputType = opt.type === 'radio' ? 'radio' : 'checkbox';
                const inputName = opt.type === 'radio' ? `opt_${idx}` : `opt_${idx}[]`;
                
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
                        ${opt.required ? '<span class="text-[10px] bg-gray-800 text-white px-2 py-0.5 rounded">OBRIGATORIO</span>' : '<span class="text-[10px] text-gray-400">OPCIONAL</span>'}
                    </div>
                    ${opt.type === 'checkbox' && opt.max > 1 ? `<p class="text-xs text-gray-400 mb-2">Escolha ate ${opt.max} opcoes</p>` : ''}
                    <div class="space-y-1">${itemsHtml}</div>
                </div>
            `;
        });
    } else { 
        optionsHtml = '<p class="text-xs text-gray-400 italic mb-4">Sem opcoes adicionais.</p>'; 
    }
    Swal.fire({
        title: `<span class="font-serif text-2xl text-gray-900 dark:text-white">${product.name}</span>`,
        html: `
            <div class="text-left mt-1">
                <div class="mb-4 rounded-xl overflow-hidden bg-orange-50 ${product.image && !product.image.startsWith('data:') ? '' : 'h-[180px]'}">
                    ${product.image && !product.image.startsWith('data:') 
                        ? `<img src="${product.image}" class="w-full h-full object-cover">`
                        : `<div class="w-full h-full flex items-center justify-center bg-orange-50">
                            <i class="fas fa-utensils text-6xl text-orange-300"></i>
                            </div>`
                    }
                </div>
                <p class="text-sm text-gray-500 mb-6 border-b border-orange-100 pb-4">${product.description || ''}</p>
                <div class="max-h-[30vh] overflow-y-auto mb-4">${optionsHtml}</div>
                <div class="mt-4">
                    <label class="text-[10px] font-bold text-gray-400 uppercase mb-2 block">Observacoes</label>
                    <textarea id="modal-obs" class="w-full bg-orange-50 border border-orange-200 rounded-xl p-3 text-sm resize-none" rows="2" placeholder="Ex: Sem cebola..."></textarea>
                </div>
                <div class="flex justify-between items-center mt-6 pt-4 border-t border-slate-100">
                    <span class="text-xs font-bold uppercase text-gray-400">Total</span>
                    <span id="modal-total-display" data-base="${product.price}" class="text-2xl font-serif text-gray-900 font-medium">R$ ${product.price.toFixed(2)}</span>
                </div>
            </div>
        `,
        showCloseButton: true, showConfirmButton: true, confirmButtonText: 'ADICIONAR A SACOLA', confirmButtonColor: getPrimaryColor(),
        showCancelButton: true, cancelButtonText: 'Voltar',
        customClass: { popup: 'rounded-3xl overflow-hidden shadow-2xl' },
        preConfirm: () => { 
            const obs = document.getElementById('modal-obs').value;
            const selectedOptions = [];
            let extraPrice = 0;
            
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
    Toastify({ text: "Adicionado a sacola", style: { background: getPrimaryColor() } }).showToast();
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
    
    Swal.fire({
        title: 'Remover item?',
        text: `Deseja retirar "${item.name}" da sacola?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        cancelButtonColor: '#3085d6',
        confirmButtonText: 'Sim, remover',
        cancelButtonText: 'Cancelar',
        width: 300
    }).then((result) => {
        if (result.isConfirmed) {
            cart.splice(i, 1);
            saveCart();
            
            if (cart.length === 0) {
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

window.openCart = () => { 
    document.getElementById("cart-modal").classList.remove("hidden"); 
    document.body.classList.add("overflow-hidden"); 
    window.renderCartItems(); 
    document.getElementById("step-1-cart").classList.remove("hidden"); 
    document.getElementById("step-2-address").classList.add("hidden"); 
    document.getElementById("btn-finalize").classList.add("hidden"); 
    document.getElementById("btn-next-step").classList.remove("hidden"); 
    
    // Atualiza estado dos botoes de entrega/retirada ao abrir carrinho
    updateDeliveryButtons();
};
window.closeCart = () => { document.getElementById("cart-modal").classList.add("hidden"); document.body.classList.remove("overflow-hidden"); };

window.goToAddress = () => { 
    if (!IS_STORE_OPEN) {
        Toastify({ text: "A loja esta fechada no momento.", style: { background: "#ef4444" } }).showToast();
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

// Função centralizada para atualizar visual dos botões de entrega/retirada
function updateDeliveryButtons() {
    const btnDel = document.getElementById("btn-delivery");
    const btnPick = document.getElementById("btn-pickup");
    const addrCont = document.getElementById("address-container");

    if (!btnDel || !btnPick) return;

    if (isDelivery) {
        btnDel.classList.add("bg-white", "text-orange-600");
        btnDel.classList.remove("text-gray-500");
        btnPick.classList.remove("bg-white", "text-orange-600");
        btnPick.classList.add("text-gray-500");
        if (addrCont) addrCont.classList.remove("hidden");
    } else {
        btnPick.classList.add("bg-white", "text-orange-600");
        btnPick.classList.remove("text-gray-500");
        btnDel.classList.remove("bg-white", "text-orange-600");
        btnDel.classList.add("text-gray-500");
        if (addrCont) addrCont.classList.add("hidden");
    }
}

window.toggleDelivery = (val) => {
    isDelivery = val;
    localStorage.setItem(DELIVERY_KEY, JSON.stringify(val));
    const btnDel = document.getElementById("btn-delivery");
    const btnPick = document.getElementById("btn-pickup");
    const addrCont = document.getElementById("address-container");
    
    if (val) {
        // ENTREGA selecionado
        btnDel.classList.add("bg-white", "text-orange-600");
        btnDel.classList.remove("text-gray-500");
        btnPick.classList.remove("bg-white", "text-orange-600");
        btnPick.classList.add("text-gray-500");
        addrCont.classList.remove("hidden");
        
        // Restaura o último valor válido se existir
        if (ultimoValorFreteValido > 0) {
            valorFreteAtual = ultimoValorFreteValido;
        }
    } else {
        // RETIRADA selecionado - salva o valor atual antes de zerar
        if (valorFreteAtual > 0) {
            ultimoValorFreteValido = valorFreteAtual;
        }
        btnPick.classList.add("bg-white", "text-orange-600");
        btnPick.classList.remove("text-gray-500");
        btnDel.classList.remove("bg-white", "text-orange-600");
        btnDel.classList.add("text-gray-500");
        addrCont.classList.add("hidden");
        valorFreteAtual = 0;
    }
    updateDeliveryFeeDisplay();
    updateCartTotal();
};

function updateCartTotal() {
    // Calculate subtotal (items only)
    let subtotal = cart.reduce((a, b) => a + (b.price * b.qtd), 0);
    
    // Calculate total with delivery fee if applicable
    let totalWithDelivery = subtotal + (isDelivery ? valorFreteAtual : 0);
    
    // Calculate final total with discount if applicable
    let finalTotal = totalWithDelivery;
    
    const elFinal = document.getElementById("cart-total-final");
    const elPreview = document.getElementById("cart-total-preview");
    const discountEl = document.getElementById("discount-display");
    const discountAmountEl = document.getElementById("discount-amount");
    
    // Update order summary elements
    const orderSummary = document.getElementById("order-summary");
    const subtotalEl = document.getElementById("cart-subtotal");
    const discountSummaryRow = document.getElementById("discount-summary-row");
    const discountAmountSummaryEl = document.getElementById("discount-amount-summary");
    
    // Always show order summary when there are items
    if (cart.length > 0) {
        if (orderSummary) orderSummary.classList.remove("hidden");
        if (subtotalEl) subtotalEl.innerText = `R$ ${subtotal.toFixed(2)}`;
    } else {
        if (orderSummary) orderSummary.classList.add("hidden");
    }
    
    // Update delivery fee display (handles visibility and value)
    updateDeliveryFeeDisplay();
    
    if (appliedCoupon) {
        finalTotal = totalWithDelivery - appliedCoupon.discount_amount;
        
        if (elFinal) elFinal.innerText = `R$ ${finalTotal.toFixed(2)}`;
        if (elPreview) elPreview.innerText = `R$ ${finalTotal.toFixed(2)}`;
        
        if (discountEl) {
            discountEl.classList.remove("hidden");
            discountAmountEl.innerText = `-R$ ${appliedCoupon.discount_amount.toFixed(2)}`;
        }
        
        // Show discount in summary
        if (discountSummaryRow) {
            discountSummaryRow.classList.remove("hidden");
            if (discountAmountSummaryEl) discountAmountSummaryEl.innerText = `-R$ ${appliedCoupon.discount_amount.toFixed(2)}`;
        }
    } else {
        if (elFinal) elFinal.innerText = `R$ ${totalWithDelivery.toFixed(2)}`;
        if (elPreview) elPreview.innerText = `R$ ${totalWithDelivery.toFixed(2)}`;
        if (discountEl) discountEl.classList.add("hidden");
        if (discountSummaryRow) discountSummaryRow.classList.add("hidden");
    }
}

window.toggleFavorite = (id) => Toastify({ text: "Favoritado", style: { background: getPrimaryColor() } }).showToast();
window.openStoreInfo = () => {
    document.getElementById("store-info-modal").classList.remove("hidden");
    document.body.classList.add("overflow-hidden");
};

window.closeStoreInfo = () => {
    document.getElementById("store-info-modal").classList.add("hidden");
    document.body.classList.remove("overflow-hidden");
};

// CONFIGURACAO PARA HISTORICO DO CLIENTE
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
            content.innerHTML = '';
            
            data.orders.forEach(order => {
                let statusClass = "bg-gray-100 text-gray-600";
                let iconClass = "fa-clock";
                let statusText = order.status;

                if(order.status_key === 'pendente') {
                    statusClass = "bg-orange-100 text-orange-600";
                    iconClass = "fa-circle-notch fa-spin";
                }
                else if(order.status_key === 'em_preparo') {
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

                let typeIcon = '';
                if (order.table_number) {
                    typeIcon = `<div class="bg-orange-100 px-3 py-1.5 rounded-lg flex items-center gap-2 text-xs font-bold text-orange-600">
                        <i class="fas fa-chair text-orange-800"></i> Mesa ${order.table_number}
                        </div>`;
                } else if (order.is_delivery) {
                    typeIcon = `<div class="bg-blue-100 px-3 py-1.5 rounded-lg flex items-center gap-2 text-xs font-bold text-blue-600">
                        <i class="fas fa-motorcycle text-blue-800"></i> Entrega
                        </div>`;
                } else {
                    typeIcon = `<div class="bg-gray-100 px-3 py-1.5 rounded-lg flex items-center gap-2 text-xs font-bold text-gray-600">
                        <i class="fas fa-store text-gray-800"></i> Retirada
                        </div>`;
                }

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
        content.innerHTML = `<div class="text-center py-10 text-red-400">Erro de conexao.</div>`;
    }
};

function formatItemsList(summary) {
    return summary.replace(/(\d+x)/g, '<span class="font-bold text-gray-900 dark:text-white">$1</span>');
}

window.closeHistory = () => {
    document.getElementById("history-modal").classList.add("hidden");
    document.body.classList.remove("overflow-hidden");
};

// Flag global para evitar múltiplas requisições simultâneas
let cepFetching = false;

window.buscarCep = () => {
    // Evita múltiplas requisições simultâneas
    if (cepFetching) return;
    
    const cepInput = document.getElementById("cep");
    const cep = cepInput.value.replace(/\D/g, "");
    
    // VALIDAÇÃO RIGOROSA DE CEP
    if (!cep) {
        Toastify({ 
            text: "CEP vazio. Digite um CEP válido (8 dígitos).", 
            duration: 3000,
            style: { background: "#ef4444" } 
        }).showToast();
        return;
    }
    
    if (cep.length !== 8) {
        Toastify({ 
            text: `CEP inválido! Deve ter exatamente 8 dígitos (digitados: ${cep.length})`, 
            duration: 3000,
            style: { background: "#ef4444" } 
        }).showToast();
        cepInput.value = "";
        cepInput.focus();
        return;
    }
    
    // Verifica se é tudo zero (CEP inválido)
    if (cep === "00000000") {
        Toastify({ 
            text: "CEP inválido! Digite um CEP real.", 
            duration: 3000,
            style: { background: "#ef4444" } 
        }).showToast();
        cepInput.value = "";
        cepInput.focus();
        return;
    }
    
    // Define flag de fetching
    cepFetching = true;
    
    // CHAMADA À API COM TRATAMENTO COMPLETO
    fetch(`https://viacep.com.br/ws/${cep}/json/`)
        .then(r => {
            if (!r.ok) throw new Error(`Erro HTTP: ${r.status}`);
            return r.json();
        })
        .then(d => {
            // Verifica se retornou erro (campo 'erro' da ViaCEP)
            if (d.erro === true || !d.logradouro) {
                throw new Error("CEP não encontrado. Verifique e tente novamente.");
            }
            
            // SUCESSO: Preenche os campos
            document.getElementById("address").value = d.logradouro || "";
            document.getElementById("neighborhood").value = d.bairro || "";
            
            // Libera campo de número para edição
            document.getElementById("number").removeAttribute("readonly");
            document.getElementById("number").value = "";
            document.getElementById("number").focus();
            
            // Mostra mensagem de sucesso
            Toastify({ 
                text: "CEP encontrado com sucesso!", 
                duration: 2000,
                style: { background: "#10b981" } 
            }).showToast();
            
            // Calcula frete baseado no bairro (esta função mostrará a mensagem de taxa)
            window.calcularTaxaEntrega(d.bairro);
        })
        .catch(erro => {
            // ERRO: Mostra mensagem amigável UMA ÚNICA VEZ
            console.error("Erro ao buscar CEP:", erro);
            let mensagem = "CEP inválido ou não encontrado. Digite manualmente.";
            
            if (erro.message.includes("HTTP")) {
                mensagem = "Falha na conexão com o servidor de CEP. Tente novamente.";
            }
            
            Toastify({ 
                text: mensagem, 
                duration: 4000,
                style: { background: "#ef4444" } 
            }).showToast();
            
            // LIBERA OS CAMPOS PARA EDIÇÃO MANUAL
            window.habilitarEnderecoManual();
            cepInput.value = cep; // Mantém o CEP digitado para referência
            cepInput.focus();
        })
        .finally(() => {
            // Libera flag para próximas requisições
            cepFetching = false;
        });
};

window.habilitarEnderecoManual = () => {
    const addressInput = document.getElementById("address");
    const neighborhoodInput = document.getElementById("neighborhood");
    
    addressInput.removeAttribute("readonly");
    neighborhoodInput.removeAttribute("readonly");
    addressInput.focus();
    
    // Remove qualquer listener anterior para evitar duplicatas
    neighborhoodInput.removeEventListener('blur', window.handleNeighborhoodBlur);
    
    // Define a função uma única vez e a reutiliza
    if (!window.handleNeighborhoodBlur) {
        window.handleNeighborhoodBlur = function() {
            if (this.value) {
                window.calcularTaxaEntrega(this.value);
            }
        };
    }
    
    // Adiciona o listener
    neighborhoodInput.addEventListener('blur', window.handleNeighborhoodBlur);
};

function setupEventListeners() {
    const phone = document.getElementById("client-phone");
    if (phone) {
        // Remove listener anterior para evitar duplicatas
        phone.removeEventListener("input", window.handlePhoneInput);
        
        // Define a função se não existir
        if (!window.handlePhoneInput) {
            window.handlePhoneInput = function(e) {
                let v = e.target.value.replace(/\D/g,"");
                v = v.replace(/^(\d{2})(\d)/g,"($1) $2");
                v = v.replace(/(\d)(\d{4})$/,"$1-$2");
                e.target.value = v.substring(0, 15);
            };
        }
        
        // Adiciona o listener
        phone.addEventListener("input", window.handlePhoneInput);
    }
}

// Verifica se a loja esta aberta AGORA
async function checkRestaurantOpen() {
    const statusEl = document.getElementById("status-text");
    const iconEl = document.getElementById("status-icon");
    const container = document.getElementById("status-loja-container");

    // PRIORIDADE 1: Verificar status via API pública (não requer autenticação)
    try {
        const response = await fetch(`/${window.TENANT_SLUG}/api/store/status/`);
        const data = await response.json();

        if (data.status === 'success') {
            if (data.is_open) {
                setStatusOpen(data.message);
                updateCheckoutButtons(true);
                return true;
            } else {
                setStatusClosed(data.message);
                updateCheckoutButtons(false);
                return false;
            }
        }
    } catch (e) {
        console.error("Erro ao verificar status da loja:", e);
    }

    // Fallback: Se a API falhar, faz verificação local com os horários
    const schedule = window.STORE_CONFIG?.schedule;
    if (!schedule || Object.keys(schedule).length === 0) {
        setStatusOpen("ABERTO (Sem horario definido)");
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
                setStatusOpen(`ABERTO - Fecha as ${closeTime}`);
                updateCheckoutButtons(true);
                return true;
            }
        }
    }

    const regraHoje = schedule[diaHoje];
    if (verificarRegra(regraHoje)) {
        let closeTime = regraHoje.close;
        if (getMinutes(regraHoje.close) === 0) closeTime = "00:00";
        setStatusOpen(`ABERTO - Fecha as ${closeTime}`);
        updateCheckoutButtons(true);
        return true;
    }

    // PRIORIDADE 3: Determinar motivo do fechamento
    let msg = "FECHADO HOJE";
    if (regraHoje && !regraHoje.closed && regraHoje.open) {
        const openMin = getMinutes(regraHoje.open);
        if (horaAtualMin < openMin) {
            msg = `FECHADO AGORA - ABRE AS ${regraHoje.open}`;
        }
    } else if (regraHoje && regraHoje.closed) {
        msg = "FECHADO HOJE";
    }

    setStatusClosed(msg);
    updateCheckoutButtons(false);
    return false;
}

function setStatusClosed(msg) {
    const statusEl = document.getElementById("status-text");
    const iconEl = document.getElementById("status-icon");
    const container = document.getElementById("status-loja-container");
    const mobileStatusEl = document.getElementById("mobile-status-text");
    
    if(statusEl) statusEl.innerText = msg;
    if(iconEl) iconEl.className = "fas fa-circle text-[8px] text-red-500";
    if(container) container.className = "inline-flex items-center gap-3 px-5 py-2.5 rounded-full bg-black/40 backdrop-blur-md border border-red-500/30 text-xs font-bold shadow-lg mb-8 transition hover:bg-black/50";
    
    // Atualizar status mobile
    if(mobileStatusEl) {
        mobileStatusEl.innerText = "FECHADO";
        mobileStatusEl.classList.remove("text-green-500", "animate-pulse");
        mobileStatusEl.classList.add("text-red-500");
    }
}

function setStatusOpen(msg) {
    const statusEl = document.getElementById("status-text");
    const iconEl = document.getElementById("status-icon");
    const container = document.getElementById("status-loja-container");
    const mobileStatusEl = document.getElementById("mobile-status-text");
    
    if(statusEl) statusEl.innerText = msg;
    if(iconEl) iconEl.className = "fas fa-circle text-[8px] animate-pulse text-green-400";
    if(container) container.className = "inline-flex items-center gap-3 px-5 py-2.5 rounded-full bg-black/40 backdrop-blur-md border border-green-500/30 text-xs font-bold shadow-lg mb-8 transition hover:bg-black/50";
    
    // Atualizar status mobile
    if(mobileStatusEl) {
        mobileStatusEl.innerText = "ABERTO";
        mobileStatusEl.classList.add("text-green-500", "animate-pulse");
        mobileStatusEl.classList.remove("text-red-500");
    }
}

// Atualiza a cada minuto
setInterval(checkRestaurantOpen, 60000);

// Verificar status imediatamente ao carregar
if (typeof checkRestaurantOpen === 'function') {
    checkRestaurantOpen();
}

// ========================
// FUNCOES DE CUPOM DE DESCONTO
// ========================

let appliedCoupon = null;

window.applyCoupon = async () => {
    const codeInput = document.getElementById("coupon-code");
    const messageEl = document.getElementById("coupon-message");
    const appliedEl = document.getElementById("coupon-applied");
    const codeDisplay = document.getElementById("coupon-applied-code");
    
    const code = codeInput.value.trim().toUpperCase();
    if (!code) {
        showCouponMessage("Digite um codigo de cupom", "error");
        return;
    }
    
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
            appliedCoupon = {
                code: result.coupon.code,
                discount_amount: result.coupon.discount_amount,
                final_value: result.coupon.final_value
            };
            
            codeInput.value = '';
            appliedEl.classList.remove('hidden');
            codeDisplay.innerText = result.coupon.code;
            
            showCouponMessage(`${result.coupon.description || 'Cupom aplicado!'} (-R$ ${result.coupon.discount_amount.toFixed(2)})`, "success");
            
            updateCartTotal();
        } else {
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
    const appliedEl = document.getElementById("coupon-applied");
    const codeEl = document.getElementById("coupon-code");
    if (appliedEl) appliedEl.classList.add("hidden");
    if (codeEl) codeEl.value = '';
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

function updateCheckoutButtons(isOpen) {
    IS_STORE_OPEN = isOpen;
    
    const btnNext = document.getElementById("btn-next-step");
    const btnFinalize = document.getElementById("btn-finalize");
    
    const buttons = [btnNext, btnFinalize];

    buttons.forEach(btn => {
        if (!btn) return;

        if (isOpen) {
            btn.disabled = false;
            btn.classList.remove("bg-gray-400", "cursor-not-allowed");
            
            if (btn.id === "btn-next-step") {
                btn.classList.add("bg-orange-600", "hover:bg-orange-700");
                btn.innerText = "Continuar";
            } else {
                btn.classList.add("bg-green-600", "hover:bg-green-700");
                if(btn.innerText === "Loja Fechada") btn.innerText = "Finalizar Pedido no WhatsApp";
            }
        } else {
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
        text: "Todos os itens serao removidos.",
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
            window.closeCart();
            Toastify({ text: "Sacola limpa!", style: { background: "#ef4444" } }).showToast();
        }
    });
}

// --- FUNÇÂO PARA CALCULAR TOTAL DO MODAL ---
window.calculateModalTotal = () => {
    // 1. Pega o elemento que mostra o preço
    const priceEl = document.getElementById('modal-total-display');
    if (!priceEl) return;

    // 2. Pega o preço base que salvamos no atributo data-base
    let total = parseFloat(priceEl.dataset.base);

    // 3. Pega todos os inputs MARCADOS dentro do modal do SweetAlert
    const checkedInputs = document.querySelectorAll('.swal2-popup input:checked');

    // 4. Soma os valores
    checkedInputs.forEach(input => {
        // O value é "Nome::Preco", então damos split
        const parts = input.value.split('::');
        if (parts.length === 2) {
            total += parseFloat(parts[1]);
        }
    });

    // 5. Atualiza o texto na tela
    priceEl.innerText = `R$ ${total.toFixed(2)}`;
};

// --- FUNÇÃO DE GEOLOCALIZAÇÃO ---
window.usarLocalizacao = () => {
    const btn = document.getElementById("btn-geo");
    const originalText = btn.innerHTML;

    if (!navigator.geolocation) {
        Toastify({ text: "Seu dispositivo não tem GPS.", style: { background: getPrimaryColor() } }).showToast();
        return;
    }

    // Muda estado do botão
    btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Buscando GPS...';
    btn.disabled = true;

    navigator.geolocation.getCurrentPosition(
        // 1. SUCESSO (GPS Funcionou)
        (position) => {
            const lat = position.coords.latitude;
            const lon = position.coords.longitude;
            
            // Busca o endereço exato
            fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&addressdetails=1`)
                .then(response => response.json())
                .then(data => {
                    const addr = data.address;
                    const rua = addr.road || addr.street || addr.pedestrian || "";
                    const bairro = addr.suburb || addr.neighbourhood || addr.residential || "";
                    const cep = addr.postcode || "";
                    
                    if (rua) document.getElementById("address").value = rua;
                    if (bairro) document.getElementById("neighborhood").value = bairro;
                    if (cep) document.getElementById("cep").value = cep.replace(/\D/g, "");

                    document.getElementById("address").removeAttribute("readonly");
                    document.getElementById("neighborhood").removeAttribute("readonly");

                    if (bairro) window.calcularTaxaEntrega(bairro);

                    // Foca no número para o cliente completar
                    document.getElementById("number").value = "";
                    document.getElementById("number").focus();
                    
                    Toastify({ text: "Localização encontrada!", style: { background: "#10b981" } }).showToast();
                })
                .catch(() => {
                    Toastify({ text: "GPS funcionou, mas não achamos o endereço escrito. Digite manualmente.", style: { background: getPrimaryColor() } }).showToast();
                    window.habilitarEnderecoManual(); // Já libera os campos
                })
                .finally(() => {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                });
        },
        // 2. ERRO (Timeout ou Bloqueio)
        (error) => {
            console.error(error);
            // Mensagem amigável dependendo do erro
            let msg = "Não conseguimos sua localização exata.";
            
            if (error.code === 1) msg = "Você negou a permissão de localização.";
            // Se for Timeout (Desktop geralmente cai aqui), pedimos para digitar
            if (error.code === 3) msg = "Sinal de GPS fraco ou indisponível no PC.";

            Toastify({ text: `${msg} Por favor, digite seu endereço.`, duration: 4000, style: { background: getPrimaryColor() } }).showToast();
            
            // Truque de UX: Se der erro, já libera os campos e foca no CEP para ele digitar logo
            window.habilitarEnderecoManual();
            document.getElementById("cep").focus();

            btn.innerHTML = originalText;
            btn.disabled = false;
        },
        // 3. CONFIGURAÇÃO (Prioridade na Precisão)
        {
            enableHighAccuracy: true, // MANTÉM TRUE: Prioriza precisão para entrega
            timeout: 20000,           // Aumentamos para 20s (ajuda celulares lentos)
            maximumAge: 0             // Não usa cache velho, quer a posição de AGORA
        }
    );
};

// Função para mostrar/esconder campo de troco
window.toggleTroco = (show) => {
    const el = document.getElementById("troco-container");
    if (show) {
        el.classList.remove("hidden");
        document.getElementById("troco-valor").focus();
    } else {
        el.classList.add("hidden");
        document.getElementById("troco-valor").value = ""; // Limpa se mudar de ideia
    }
};