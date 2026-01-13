"""
Validadores customizados para o aplicativo de pedidos.
"""
from django.core.exceptions import ValidationError
import re


def validate_cep(cep):
    """
    Valida um CEP brasileiro.
    - Deve ter exatamente 8 dígitos
    - Não pode ser tudo zero
    - Formato: XXXXX-XXX ou XXXXXXXX
    
    Lança ValidationError se inválido.
    """
    if not cep:
        raise ValidationError("CEP não pode estar vazio.")
    
    # Remove caracteres especiais
    cep_clean = re.sub(r'\D', '', str(cep))
    
    # Verifica tamanho
    if len(cep_clean) != 8:
        raise ValidationError(
            f"CEP inválido! Deve conter exatamente 8 dígitos (fornecido: {len(cep_clean)} dígitos)."
        )
    
    # Verifica se é tudo zero
    if cep_clean == "00000000":
        raise ValidationError("CEP inválido! O CEP 00000000 não existe.")
    
    # Verifica se contém apenas dígitos
    if not cep_clean.isdigit():
        raise ValidationError("CEP deve conter apenas números.")
    
    return cep_clean


def validate_phone(phone):
    """
    Valida um número de telefone brasileiro.
    - Deve ter entre 10 e 11 dígitos
    - Pode conter parênteses, hífens e espaços
    """
    if not phone:
        raise ValidationError("Telefone não pode estar vazio.")
    
    phone_clean = re.sub(r'\D', '', str(phone))
    
    if len(phone_clean) < 10 or len(phone_clean) > 11:
        raise ValidationError(
            f"Telefone inválido! Deve ter 10 ou 11 dígitos (fornecido: {len(phone_clean)})."
        )
    
    # Verifica se tem DDD (primeiro dígito não pode ser 0)
    if phone_clean[0] == '0':
        raise ValidationError("Telefone inválido! DDD não pode começar com 0.")
    
    return phone_clean


def validate_order_data(data, order_type):
    """
    Valida os dados completos do pedido.
    
    Args:
        data: Dicionário com dados do pedido
        order_type: Tipo de pedido ('delivery', 'pickup', 'table')
    
    Retorna:
        dict: Dados validados e limpos
        
    Lança ValidationError se inválido.
    """
    errors = []
    
    # Validar nome do cliente
    nome = data.get('nome', '').strip()
    if not nome or len(nome) < 2:
        errors.append("Nome deve ter no mínimo 2 caracteres.")
    if len(nome) > 100:
        errors.append("Nome muito longo (máximo 100 caracteres).")
    
    # Validar telefone
    phone = data.get('phone', '').strip()
    try:
        phone_clean = validate_phone(phone)
    except ValidationError as e:
        errors.append(str(e))
    
    # Validar endereço (apenas para delivery)
    if order_type == 'delivery':
        address_data = data.get('address', {})
        
        # CEP
        cep = address_data.get('cep', '').strip()
        try:
            cep_clean = validate_cep(cep)
        except ValidationError as e:
            errors.append(f"CEP: {e.message}")
        
        # Rua
        street = address_data.get('street', '').strip()
        if not street or len(street) < 3:
            errors.append("Rua deve ter no mínimo 3 caracteres.")
        
        # Número
        number = address_data.get('number', '').strip()
        if not number:
            errors.append("Número é obrigatório.")
        
        # Bairro
        neighborhood = address_data.get('neighborhood', '').strip()
        if not neighborhood or len(neighborhood) < 2:
            errors.append("Bairro deve ter no mínimo 2 caracteres.")
    
    # Validar método de pagamento
    method = data.get('method', '').strip()
    valid_methods = ['pix', 'cartao', 'dinheiro', 'cartao_dinheiro']
    if not method or method not in valid_methods:
        errors.append(f"Método de pagamento inválido. Deve ser um de: {', '.join(valid_methods)}")
    
    # Validar observações (limite de caracteres)
    obs = data.get('obs', '').strip()
    if len(obs) > 500:
        errors.append("Observações muito longas (máximo 500 caracteres).")
    
    if errors:
        raise ValidationError("; ".join(errors))
    
    return {
        'nome': nome,
        'phone': phone_clean if 'phone_clean' in locals() else phone,
        'obs': obs,
        'address': data.get('address', {}) if order_type == 'delivery' else {}
    }
